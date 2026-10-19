"""MastGaane FM — a jockey that picks what plays next, talks between songs and learns from you.

What plays:
  songs     your songs and the big collection (library.py), weighted by taste (likes, full listens,
            skips per artist and genre, and per mood and genre at this hour: hour_taste) and leaning
            to the hour (morning bhakti, night ghazals: TIME_SHARE), never one of the last ~40 songs
            played, in any upload of it; every 4th pick is a fresh discovery
  programmes archive episodes in time slots loosely after the real Vividh Bharati day
            (Sangeet Sarita in the morning, Jaimala at 7 pm, Hawa Mahal at 8, Chhaya Geet late)
  links     a short Hindi announcement before each item, written by the local LLM when there is
            time (the next item is prefetched while the current one plays) or from templates,
            and spoken by a Microsoft neural Hindi voice (edge-tts) in the song's feeling: each
            sentence in its own pitch and pace (excited, tender, wistful…), with real pauses
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import sys
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel
from rich.console import Console
from sqlmodel import select

from . import archive, moods
from .config import settings
from .db import MANUAL, Play, Song, session, utcnow

console = Console()
VOICE_DIR = settings.data_dir / "jockey_voice"
RECENT = 40  # never repeat anything from the last N plays — another upload of the same song included
DISCOVERY_EVERY = 4
#: songs that play as kept ones: yours (approved) and the big collection (library.py)
KEPT = ("approved", "library")
#: with no wish, this share of the kept picks comes from your own songs (else 125 of yours drown in 5000)
FAVOURITES_SHARE = 0.3
# with no wish, this share of the songs suits the hour: bhakti in the morning, happy songs after lunch,
# romantic ones in the evening, ghazals and sad songs at night (moods.TIME_MOODS)
TIME_SHARE = 0.65

# (weekdays, start, end, programme slug); weekdays 0 = Monday; first match wins
SCHEDULE = [
    (range(7), "06:30", "08:30", "sangeet-sarita"),
    (range(7), "08:30", "10:00", "triveni"),
    (range(7), "13:00", "15:00", "bhoole-bisre-geet"),
    ((5, 6), "15:00", "17:00", "inse-miliye"),
    (range(7), "19:00", "20:00", "jaimala"),
    ((2,), "20:00", "22:00", "geetmala"),  # Binaca Geetmala was Wednesday, 8 pm
    (range(7), "20:00", "21:30", "hawa-mahal"),
    ((0, 4), "23:00", "23:59", "khooni-monday"),  # Monday (the name) and Friday night
    ((5,), "23:00", "23:59", "radio-horror"),  # Saturday night
    ((6,), "00:00", "01:00", "radio-horror"),
    (range(7), "21:30", "23:59", "chhaya-geet"),
    (range(7), "00:00", "01:30", "chhaya-geet"),
]


def slot(now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    hm = now.strftime("%H:%M")
    for days, start, end, slug in SCHEDULE:
        if now.weekday() in days and start <= hm < end:
            return slug
    return None


# ----------------------------------------------------------------------------- taste
OUTCOME_SCORE = {"like": 3.0, "full": 1.0, "skip": -1.0, "dislike": -4.0}


def taste(days: int = 120) -> dict[str, dict[str, float]]:
    """Affinity per artist / genre / programme from listening history (positive = likes it)."""
    out: dict[str, dict[str, float]] = {"artist": defaultdict(float), "genre": defaultdict(float), "programme": defaultdict(float)}
    since = utcnow() - timedelta(days=days)
    with session() as s:
        for p in s.exec(select(Play).where(Play.at >= since)).all():
            sc = OUTCOME_SCORE.get(p.outcome, 0.0)
            for a in (p.artist or "").split(","):
                if a.strip():
                    out["artist"][a.strip().lower()] += sc
            if p.genre:
                out["genre"][p.genre] += sc / 3
            if p.programme:
                out["programme"][p.programme] += sc
    return out


def hour_taste(days: int = 120) -> dict[str, dict[str, float]]:
    """What you finish, like and skip at each part of the day — {"night": {"mood:sad": 6.0,
    "genre:ghazal": 2.5, …}} — so the hour's lean becomes yours, not only moods.TIME_MOODS."""
    from datetime import timezone

    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    since = utcnow() - timedelta(days=days)
    with session() as s:
        for p in s.exec(select(Play).where(Play.at >= since, Play.kind == "song")).all():
            sc = OUTCOME_SCORE.get(p.outcome, 0.0)
            part = moods.time_of_hour(p.at.replace(tzinfo=timezone.utc).astimezone().hour)  # stored UTC
            for m in moods.moods_of(p.mood):
                out[part]["mood:" + m] += sc
            if p.genre:
                out[part]["genre:" + p.genre] += sc
    return out


def _hour_weight(song: Song, part: dict[str, float]) -> float:
    """×0.4…×2.7 for how its moods and genre went at this hour before (ten finished sad songs at night:
    ×2.7 for the next sad one then; skipped dance songs there count against)."""
    if not part:
        return 1.0
    ms = moods.song_moods(song)
    m = sum(part.get("mood:" + x, 0.0) for x in ms) / len(ms) if ms else 0.0
    return math.exp(max(-1.0, min(1.0, 0.1 * m + 0.05 * part.get("genre:" + song.genre, 0.0))))


def _weight(song: Song, t: dict, part: dict[str, float] | None = None) -> float:
    artists = [a.strip().lower() for a in song.artist.split(",") if a.strip()]
    a = max((t["artist"].get(x, 0.0) for x in artists), default=0.0)
    w = math.exp(max(-2.0, min(2.0, 0.4 * a + 0.2 * t["genre"].get(song.genre, 0.0))))
    return w * (1.5 if song.classify_reason.startswith(MANUAL) else 1.0) * _hour_weight(song, part or {})


def _recent_refs(s, n: int = RECENT) -> set[str]:
    return {p.ref for p in s.exec(select(Play).order_by(Play.at.desc()).limit(n)).all()}


def _recent_songs(s, n: int = RECENT) -> list[tuple[str, str]]:
    """(title, artist) of the last songs played — another upload of one of them is a repeat too."""
    return [(p.title or "", p.artist or "") for p in s.exec(select(Play).where(Play.kind == "song")
                                                             .order_by(Play.at.desc()).limit(n)).all()]


def _fresh(pool: list[Song], recent: list[tuple[str, str]]) -> list[Song]:
    """The pool without another upload of a song just played, and with one upload per song (else a song
    with five uploads would come five times as often)."""
    from .lexicon import same_song, work_key

    by_work: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for title, artist in recent:
        by_work[work_key(title)].append((title, artist))
    out, seen = [], defaultdict(list)
    for x in pool:
        k = work_key(x.title)
        if any(same_song(x.title, x.artist, t, a) for t, a in by_work.get(k, ())):
            continue
        if any(same_song(x.title, x.artist, y.title, y.artist) for y in seen.get(k, ())):
            continue
        seen[k].append(x)
        out.append(x)
    return out


# ----------------------------------------------------------------------------- picking
class Item(BaseModel):
    ref: str
    kind: str  # song | episode
    title: str
    artist: str = ""
    genre: str = ""
    programme: str = ""
    programme_name: str = ""
    yt: str | None = None  # YouTube id (songs in video mode, YouTube episodes)
    audio: str  # ad-free audio URL (local route or archive.org)
    discovery: bool = False
    year: int | None = None
    mood: str = ""  # the song's mood ("sad|romantic") — stored on its Play
    caught: bool = False  # the listener just identified it on /listen — the host says so
    opener: bool = False  # the morning opener (Hanuman Chalisa): the host opens the day with it
    link_text: str = ""
    link_audio: str = ""


def _song_item(song: Song, discovery: bool) -> Item:
    return Item(ref=f"song:{song.id}", kind="song", title=song.title, artist=song.artist, genre=song.genre,
                yt=song.yt_video_id, audio=f"/jockey/audio/{song.yt_video_id}", discovery=discovery, year=song.year,
                mood=song.mood or moods.guess_mood(song))


def _episode_item(slug: str, exclude: set[str]) -> Item | None:
    p = archive.PROGRAMMES[slug]
    eps = [e for e in archive.episodes(slug) if f"{e.source}:{e.id}" not in exclude]
    if not eps:
        return None
    with session() as s:
        heard = {r for r in s.exec(select(Play.ref).where(Play.kind == "episode")).all()}
    eps = [e for e in eps if f"{e.source}:{e.id}" not in heard] or eps
    # every era gets its turn — otherwise recent uploads (the bulk of what exists) would always win
    by_era: dict[str, list] = defaultdict(list)
    for e in eps:
        by_era[archive.decade_of(e.year)].append(e)
    era = random.choice(sorted(by_era))
    pool = by_era[era]
    downloaded = {f.stem for f in archive.AUDIO_DIR.glob("*.*")} if archive.AUDIO_DIR.exists() else set()
    ready = [e for e in pool if e.source == "yt" and e.id in downloaded]
    e = random.choice(ready or pool)
    if e.source == "yt":
        audio = f"/archive/yt/{e.id}.audio"
    else:
        try:
            files = archive.ia_files(e.id)
        except Exception:
            return None
        if not files:
            return None
        audio = random.choice(files)["url"]
    return Item(ref=f"{e.source}:{e.id}", kind="episode", title=e.title, programme=slug, programme_name=p.name,
                yt=e.id if e.source == "yt" else None, audio=audio, year=e.year)


GENRE_WORDS = {"ghazal": "ghazal", "ghazals": "ghazal", "folk": "folk", "lok": "folk", "film": "film", "filmi": "film",
               "bollywood": "film", "sufi": "sufi", "qawwali": "sufi", "classical": "classical", "bhajan": "devotional",
               "bhajans": "devotional", "devotional": "devotional",
               # as speech recognition spells them ("Bharti, gajal sunao")
               "gajal": "ghazal", "gajals": "ghazal", "gazal": "ghazal", "gazals": "ghazal", "ghazel": "ghazal",
               # and as English-mode Whisper hears "ghazal" over music
               "jazeel": "ghazal", "jazil": "ghazal", "chazil": "ghazal", "chhazal": "ghazal",
               "bajan": "devotional", "bhajjan": "devotional", "kawwali": "sufi", "qawali": "sufi", "bhakti": "devotional"}
LANGUAGES = ("bhojpuri", "urdu", "awadhi", "maithili", "magahi", "hindi")
FILLER = {"play", "some", "a", "an", "the", "songs", "song", "music", "by", "of", "from", "gaane", "gane", "geet", "ke", "ki",
          "ka", "sunao", "chalao", "me", "please", "mastgaane", "on", "fm", "radio", "programme", "program"}


def _n(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


FUZZY_CUTOFF = 0.75


def _fuzzy_programme(words: list[str]) -> str | None:
    """A programme named with a slip ("chaya geet", "hawa mehal"): difflib over slug / name / hindi /
    aliases, comparing word windows of the request of about the same length."""
    import difflib

    best, best_slug = FUZZY_CUTOFF, None
    for slug, p in archive.PROGRAMMES.items():
        for cand in (slug.replace("-", " "), p.name, p.hindi, *p.aliases):
            c = _n(cand)
            if len(c) < 4:
                continue
            k = max(1, len(cand.split()))
            for size in {k - 1, k, k + 1} - {0}:
                for i in range(len(words) - size + 1):
                    w = _n("".join(words[i:i + size]))
                    if len(w) < 4:
                        continue
                    r = difflib.SequenceMatcher(None, w, c).ratio()
                    if r >= best:
                        best, best_slug = r, slug
    return best_slug


def resolve_focus(focus: str) -> tuple[str, str]:
    """What a spoken request asks for: ("any", ""), ("programme", slug), ("genre", g), ("language", l),
    ("era", "old" | "1990s"…), ("mood", "sad" …), ("time", "morning" …) or ("text", words to find in
    artist / title)."""
    import re

    f = focus.lower().strip()
    if not f:
        return ("any", "")
    nf = _n(f)
    for slug, p in archive.PROGRAMMES.items():
        if any(_n(x) and _n(x) in nf for x in (slug, p.name, p.hindi, *p.aliases, *p.spoken)):
            return ("programme", slug)
    words = re.findall(r"[\w']+", f)
    for w in words:
        if w in GENRE_WORDS:
            return ("genre", GENRE_WORDS[w])
        if w in LANGUAGES:
            return ("language", w)
    m = re.search(r"\b(?:19|20)?([0-9])0s\b", f)
    if m:
        d = int(m.group(1))
        return ("era", f"{1900 + d * 10 if d >= 3 else 2000 + d * 10}s")
    if any(w in ("old", "purane", "purana", "classic", "classics", "retro") for w in words):
        return ("era", "old")
    mood = moods.mood_of_words(f)
    if mood:
        return ("mood", mood)
    tod = moods.time_of_words(f)
    if tod:
        return ("time", tod)
    fuzzy = _fuzzy_programme(words)
    if fuzzy:
        return ("programme", fuzzy)
    rest = " ".join(w for w in words if w not in FILLER)
    return ("text", rest) if rest else ("any", "")


def _matches(song: Song, focus: tuple[str, str]) -> bool:
    kind, val = focus
    if kind == "genre":
        return song.genre == val
    if kind == "language":
        return song.language == val
    if kind == "era":
        if not song.year:
            return False
        return song.year < 1990 if val == "old" else int(val[:4]) <= song.year < int(val[:4]) + 10
    if kind == "text":
        q = _n(val)
        return bool(q) and (q in _n(song.artist) or q in _n(song.title))
    if kind == "mood":
        return val in moods.song_moods(song)
    if kind == "time":
        return bool(moods.song_moods(song) & set(moods.time_moods(val)))
    return True


def describe_focus(focus: tuple[str, str]) -> str:
    """The resolved wish in a few words, for the spoken summary ("sad songs", "Hawa Mahal")."""
    kind, val = focus
    if kind == "programme" and val in archive.PROGRAMMES:
        return archive.PROGRAMMES[val].name
    if kind in ("mood", "time"):
        return f"{val} songs"
    if kind == "era":
        return "old songs" if val == "old" else f"songs from the {val}"
    if kind in ("genre", "language", "text"):
        return val
    return ""


CATCH_FRESH_DAYS = 3


def fresh_catch(s, exclude: set[str]) -> Song | None:
    """The newest song identified on /listen (last few days) that has not played since it was caught."""
    from .db import Catch

    since = utcnow() - timedelta(days=CATCH_FRESH_DAYS)
    for c in s.exec(select(Catch).where(Catch.at >= since, Catch.song_id.is_not(None)).order_by(Catch.at.desc())).all():
        song = s.get(Song, c.song_id)
        ref = f"song:{c.song_id}"
        if song is None or not song.yt_video_id or song.status == "rejected" or ref in exclude:
            continue
        heard = s.exec(select(Play.at).where(Play.ref == ref).order_by(Play.at.desc())).first()
        # both are UTC; SQLite hands them back naive
        if heard is None or heard.replace(tzinfo=None) < c.at.replace(tzinfo=None):
            return song
    return None


def morning_opener() -> Song | None:
    """The song that opens each morning: MORNING_OPENER (a YouTube Music search) found once, kept in the
    collection as a hand-approved song, and remembered in data/morning_opener.json (no network after)."""
    import json

    q = settings.morning_opener.strip()
    if not q:
        return None
    f = settings.data_dir / "morning_opener.json"
    with session() as s:
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            song = s.get(Song, int(saved["song_id"])) if saved.get("query") == q else None
            if song is not None and song.status != "rejected":
                return song
        except (OSError, ValueError, KeyError, TypeError):
            pass
        from .discover import _yt
        from .resolver.common import norm_key

        hit = next((h for h in _yt().search(q, filter="songs", limit=5) if h.get("videoId")), None)
        if hit is None:
            return None
        title = hit.get("title") or q
        artist = ", ".join(a.get("name", "") for a in hit.get("artists") or [])
        song = s.exec(select(Song).where(Song.norm_key == norm_key(title, artist))).first()
        if song is None:
            song = Song(title=title, artist=artist, norm_key=norm_key(title, artist), source_tier=4, confidence=1.0,
                        yt_video_id=hit["videoId"], genre="devotional", language="hindi", mood="devotional", keep=True,
                        classify_reason=f"{MANUAL}the morning opener ({q})")
        song.status = "approved"
        s.add(song)
        s.commit()
        s.refresh(song)
        f.write_text(json.dumps({"query": q, "song_id": song.id}), encoding="utf-8")
        return song


def opener_due(now: datetime | None = None) -> Song | None:
    """The morning opener, when it is morning and it has not played (or been skipped) since 5 am today."""
    from datetime import timezone

    now = now or datetime.now()
    if moods.time_of_hour(now.hour) != "morning":
        return None
    try:
        song = morning_opener()
    except Exception as e:  # offline on the first morning: just the usual mix
        console.log(f"[yellow]morning opener: {e}[/]")
        return None
    if song is None or not song.yt_video_id:
        return None
    since = now.replace(hour=5, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    with session() as s:
        played = s.exec(select(Play).where(Play.ref == f"song:{song.id}", Play.at >= since)).first()
    return None if played else song


def song_item(song: Song, caught: bool = False) -> Item:
    item = _song_item(song, False)
    item.caught = caught
    return item


def pick(exclude: list[str] | None = None, n_picked: int = 0, now: datetime | None = None, focus: str = "") -> Item | None:
    """The next thing to play. `exclude` = refs already queued on the client; `n_picked` = how many
    the client has had this session (drives the 1-in-4 discovery mix); `focus` = a spoken wish
    ("ghazals", "Hawa Mahal", "Jagjit Singh", "90s") that narrows the choice — see resolve_focus().
    A song just identified on /listen comes first when there is no wish."""
    t = taste()
    part = hour_taste().get(moods.time_of_hour((now or datetime.now()).hour), {})
    want = resolve_focus(focus)
    if want[0] == "any" and n_picked == 0:  # the day's first start, in the morning: Hanuman Chalisa
        opener = opener_due(now)
        if opener and f"song:{opener.id}" not in set(exclude or []):
            item = _song_item(opener, False)
            item.opener = True
            return item
    with session() as s:
        skip = _recent_refs(s) | set(exclude or [])
        morning = moods.time_of_hour((now or datetime.now()).hour) == "morning"
        if (want[0] == "any" and morning and 0 < n_picked <= settings.morning_bhajans
                and s.exec(select(Song).where(Song.genre == "devotional", Song.yt_video_id.is_not(None))).first()):
            # the morning's first few songs after the opener are bhajans (settings.morning_bhajans)
            want = ("genre", "devotional")
        if want[0] == "programme":
            return _episode_item(want[1], set(exclude or []))
        if want[0] == "any":
            caught = fresh_catch(s, set(exclude or []))
            if caught:
                return song_item(caught, caught=True)
        prog = slot(now) if want[0] == "any" else None
        if want[0] == "time":  # "late night" → Chhaya Geet at any hour; else the time's moods
            wish = moods.time_to_wish(want[1], (now or datetime.now()).hour)
            prog = wish[1] if wish[0] == "programme" else None
        if prog:
            since = utcnow() - timedelta(hours=20)
            done = s.exec(select(Play).where(Play.programme == prog, Play.at >= since)).first()
            if done is None and t["programme"].get(prog, 0.0) > -6 and not any(r.startswith(("yt:", "ia:")) for r in exclude or []):
                ep = _episode_item(prog, skip)
                if ep:
                    return ep
        want_new = n_picked % DISCOVERY_EVERY == DISCOVERY_EVERY - 1
        recent = _recent_songs(s)
        new = _fresh([x for x in s.exec(select(Song).where(Song.status == "pending", Song.yt_video_id.is_not(None))).all()
                      if f"song:{x.id}" not in skip and _matches(x, want)], recent)
        kept = _fresh([x for x in s.exec(select(Song).where(Song.status.in_(KEPT), Song.yt_video_id.is_not(None))).all()
                       if f"song:{x.id}" not in skip and _matches(x, want)], recent)
        if want[0] != "any" and not new and not kept:  # a narrow wish runs dry: repeats beat silence
            exc = set(exclude or [])
            kept = [x for x in s.exec(select(Song).where(Song.status.in_(KEPT), Song.yt_video_id.is_not(None))).all()
                    if f"song:{x.id}" not in exc and _matches(x, want)]
        if want[0] == "any" and random.random() < TIME_SHARE:  # the usual mix leans to the hour
            hour_fit = ("time", moods.time_of_hour((now or datetime.now()).hour))
            fit_new, fit_kept = [x for x in new if _matches(x, hour_fit)], [x for x in kept if _matches(x, hour_fit)]
            if want_new:  # a discovery turn stays one, suiting the hour if it can
                new = fit_new or new
            elif fit_kept:
                kept = fit_kept
            elif fit_new:  # mornings with no kept bhajan yet play a newly found one
                kept, new = [], fit_new
        if want[0] == "any" and random.random() < FAVOURITES_SHARE:
            kept = [x for x in kept if x.status == "approved"] or kept
        pools = [(new, True), (kept, False)] if want_new else [(kept, False), (new, True)]
        for pool, is_new in pools:
            if pool:
                song = random.choices(pool, weights=[_weight(x, t, part) for x in pool])[0]
                return _song_item(song, is_new)
    return None


# ----------------------------------------------------------------------------- the host's voice
class Link(BaseModel):
    text: str


GREETING = {5: "सुप्रभात", 12: "नमस्कार", 17: "शुभ संध्या", 21: "शुभ रात्रि"}

# what the host feels while introducing a song, from its mood (Song.mood, moods.MOODS)
MOOD_EMOTION = {"energetic": "excited", "happy": "cheerful", "romantic": "tender", "sad": "wistful",
                "peaceful": "calm", "devotional": "calm", "nostalgic": "nostalgic"}


def emotion_of(item: Item) -> str:
    """excited | cheerful | warm | nostalgic | tender | calm | wistful"""
    if item.caught:
        return "excited"
    if item.kind == "episode":
        return "nostalgic"
    for m in (item.mood or "").split("|"):
        if m in MOOD_EMOTION:
            return MOOD_EMOTION[m]
    return "cheerful" if item.discovery else "warm"


def _greeting(hour: int) -> str:
    return next((g for h, g in sorted(GREETING.items(), reverse=True) if hour >= h), "शुभ रात्रि")


# song links by feeling; "…" is a breath, "!" lifts the voice, "?" asks (see _delivery)
SONG_LINKS: dict[str, list[str]] = {
    "excited": [
        "अरे वाह! अब थोड़ी मस्ती हो जाए! {singer} लेकर आए हैं — {title}!",
        "वॉल्यूम ज़रा बढ़ा लीजिए! ये रहा {title}, {singer} की आवाज़ में!",
    ],
    "cheerful": [
        "चेहरे पर एक मुस्कान ले आइए! सुनिए {singer} की आवाज़ में — {title}!",
        "मूड एकदम बढ़िया करने वाला गाना! {title}… {singer}!",
    ],
    "warm": [
        "अब सुनिए {singer} की आवाज़ में — {title}!",
        "आपकी पसंद का अगला गीत… {title}, {singer}!",
        "चलिए, अगला नग़मा! {singer}… {title}।",
        "क्या बात है! अब बारी है {title} की — {singer} की आवाज़ में।",
    ],
    "nostalgic": [
        "चलिए, ज़रा पीछे चलते हैं… वो पुराना दौर याद है? सुनिए {title}, {singer}।",
        "यादों की गलियों से एक गीत… {singer} की आवाज़ में, {title}।",
    ],
    "tender": [
        "दिल की बात… ज़रा धीरे से। {singer} की आवाज़ में, {title}।",
        "किसी ख़ास की याद आ रही है? तो ये गीत उन्हीं के नाम… {title}, {singer}।",
        "मोहब्बत भरा एक नग़मा… {singer} की आवाज़ में, {title}।",
    ],
    "calm": [
        "थोड़ा ठहरिए… एक गहरी साँस लीजिए… और सुनिए {singer} की आवाज़ में, {title}।",
        "सुकून के कुछ पल… {title}, {singer} की आवाज़ में।",
    ],
    "wistful": [
        "कुछ दर्द ऐसे होते हैं… जो बस गीतों में ही कहे जा सकते हैं। सुनिए {singer} की आवाज़ में, {title}।",
        "हाय… ये गीत दिल को छू जाता है। {singer}… {title}।",
        "आँखें थोड़ी नम हो जाएँ, तो कोई बात नहीं… सुनिए {title}, {singer} की आवाज़ में।",
    ],
}


def template_link(item: Item, first: bool = False, now: datetime | None = None) -> str:
    now = now or datetime.now()
    hello = f"{_greeting(now.hour)} दोस्तों! मस्त गाने पर आपका बहुत-बहुत स्वागत है! " if first else ""
    if item.kind == "episode":
        when = f" सन {item.year} की यह रिकॉर्डिंग।" if item.year else ""
        p = archive.PROGRAMMES.get(item.programme)
        if p and p.intro:  # its own mood: a horror story is not introduced like Jaimala
            # "Haunted Apsara Talkies | Horror Story in Hindi | सच्ची कहानी | Khooni Monday E350🔥🔥" → the story's name
            story = re.split(r"\s*[|｜]\s*", item.title)[0].strip() or item.title
            return hello + random.choice(p.intro).format(name=item.programme_name, title=story)
        return hello + random.choice([
            f"और अब… वक़्त है {item.programme_name} का! सुनिए — {item.title}।",
            f"आइए, ज़रा पुराने दिनों में लौट चलें… उस दौर का एक प्यारा कार्यक्रम — {item.programme_name}!",
            f"याद है वो दौर, जब {item.programme_name} का बेसब्री से इंतज़ार रहता था? तो लीजिए… {item.title}।",
        ]) + when
    singer = item.artist.split(",")[0].strip()
    if item.opener:
        jai = " जय बजरंगबली!" if "hanuman" in item.title.lower() else ""
        return hello + random.choice([
            f"दिन की शुभ शुरुआत… {item.title} से।{jai}",
            f"आइए, आज के दिन की शुरुआत करते हैं… {singer} की आवाज़ में, {item.title} के साथ।{jai}",
        ])
    if item.caught:
        by = f"{singer} की आवाज़ में " if singer else ""
        return hello + random.choice([
            f"वाह! अभी-अभी आपने जो गाना पहचाना था — वो अब आपके संग्रह में है! सुनिए {by}— {item.title}।",
            f"लीजिए, आपकी नई पसंद, सीधे आपके संग्रह से! {by}{item.title}।",
        ])
    if item.discovery:
        return hello + random.choice([
            f"अरे, ये सुनिए! एक नया गाना, जो शायद आपने पहले कभी नहीं सुना… {singer} की आवाज़ में, {item.title}। पसंद आए, तो ज़रूर बताइएगा!",
            f"आज की ख़ास खोज! {item.title}… {singer} की आवाज़ में। सुनिए, और बताइए — कैसा लगा?",
        ])
    if not singer:  # an upload that credits no singer: the song alone, not "… की आवाज़ में"
        return hello + random.choice([f"अब सुनिए — {item.title}!", f"आपकी पसंद का अगला गीत… {item.title}!"])
    return hello + random.choice(SONG_LINKS[emotion_of(item)]).format(singer=singer, title=item.title)


SYSTEM = (
    "You are the lively, big-hearted host of a Hindi music radio channel in the classic Vividh Bharati style, an "
    "RJ who feels every song. Write TWO or THREE short sentences in spoken Hindi (Devanagari script, max 40 words) "
    "introducing the next item, with real emotion that matches the given feeling: excited and bubbly for an upbeat "
    "song or a new find, cheerful for a happy one, tender for a romantic one, hushed and aching for a sad one "
    "(wistful), calm for a peaceful or devotional one, fond and dreamy for an old one (nostalgic). Use an "
    "interjection where it fits (अरे वाह!, हाय…, क्या बात है!, सुनिए…), exclamation marks, now and then a question "
    "to the listener, and '…' for a pause. Name the singer and the song (names may be written in Devanagari). "
    "Never invent facts such as film names, years or composers. No emojis. Never say the words रेडियो, "
    "भारती, Radio, Bharti or FM: they are the listener's voice-command words."
)


def llm_link(item: Item, previous: str = "", now: datetime | None = None, model: str | None = None) -> str | None:
    """A link written by the LLM; `model` = an Ollama model already in memory (nothing new is loaded)."""
    from .resolver.llm import LLMUnavailable, complete_json

    now = now or datetime.now()
    what = (f"A recorded episode of the radio programme '{item.programme_name}' "
            f"({archive.PROGRAMMES[item.programme].about}). Episode title: {item.title}. "
            + (f"Recorded in {item.year}. " if item.year else "") + "This is a programme, "
            "not a song; a person named in the title is its presenter or guest, not a singer." if item.kind == "episode"
            else f"Song: {item.title}\nSinger: {item.artist}\nGenre: {item.genre}" + (f"\nReleased: {item.year}" if item.year else "") + ("\n(This is a new discovery the listener has not heard.)" if item.discovery else ""))
    try:
        link = complete_json(SYSTEM, f"Time: {now:%H:%M}\nFeeling: {emotion_of(item)}\n"
                             f"Previous: {previous or 'nothing, this is the start'}\nNext:\n{what}", Link, model=model)
    except LLMUnavailable:
        return None
    text = link.text.strip()
    return text if 5 < len(text) < 400 else None


# How each feeling sounds, as (rate %, pitch Hz, volume %) over the voice's own delivery. The Hz are
# for a female voice (~200 Hz) and are scaled down for the deeper male ones.
EMOTIONS: dict[str, tuple[int, int, int]] = {
    "excited": (12, 14, 6),
    "cheerful": (7, 9, 3),
    "warm": (2, 4, 0),
    "nostalgic": (-5, 0, -3),
    "tender": (-7, -3, -6),
    "calm": (-10, -4, -8),
    "wistful": (-12, -7, -10),
}
LATE_NIGHT = (-3, -2, -6)  # 23:00–05:00: a touch slower and quieter
MALE_VOICES = ("Madhur", "Andrew", "Brian", "William", "Florian", "Remy", "Giuseppe", "Hyunsu", "Prabhat", "Salman", "Asad")
VOICE_STYLE = "v2"  # bump when the delivery changes, so cached clips are spoken afresh
MAX_PIECES = 8
_PIECE = re.compile(r"[^।!?…]+(?:[।!?…]+|$)")


def _pieces(text: str) -> list[str]:
    """Sentences and '…' breaths: each is spoken in its own tone, with a natural pause between."""
    out = [p.strip() for p in _PIECE.findall(text.replace("...", "…")) if p.strip(" —-,")]
    if len(out) > MAX_PIECES:
        out = out[:MAX_PIECES - 1] + [" ".join(out[MAX_PIECES - 1:])]
    return out or [text]


def _delivery(text: str, emotion: str, voice: str, hour: int) -> list[tuple[str, int, int, int]]:
    """(piece, rate %, pitch Hz, volume %) per piece: the feeling's tone, lifted on '!', rising on '?',
    slowing into a '…' and unhurried on the closing line that names the song."""
    rate, pitch, vol = EMOTIONS.get(emotion, EMOTIONS["warm"])
    if hour >= 23 or hour < 5:
        rate, pitch, vol = rate + LATE_NIGHT[0], pitch + LATE_NIGHT[1], vol + LATE_NIGHT[2]
    scale = 0.6 if any(m in voice for m in MALE_VOICES) else 1.0
    pieces = _pieces(text)
    out = []
    for i, p in enumerate(pieces):
        r, h, v = rate, pitch, vol
        end = p[-1]
        if end == "!":
            r, h, v = r + 6, h + 10, v + 6
        elif end == "?":
            h += 6
        elif end == "…":
            r -= 8
        elif i == len(pieces) - 1 and len(pieces) > 1:
            r -= 4
        out.append((p, max(-30, min(30, r)), round(max(-30, min(30, h)) * scale), max(-30, min(20, v))))
    return out


async def _say(parts: list[tuple[str, int, int, int]], voice: str) -> bytes:
    import edge_tts

    async def one(text: str, rate: int, pitch: int, vol: int) -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz",
                                                volume=f"{vol:+d}%").stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        if not buf:
            raise RuntimeError(f"no audio for {text!r}")
        return bytes(buf)

    clips = await asyncio.gather(*(one(*p) for p in parts))
    try:
        return _stitch(clips, [p[0] for p in parts])
    except Exception as e:  # MP3 frames also join as they are, only with each clip's own long silences
        console.log(f"[yellow]voice: could not stitch the pieces ({e}); joining them as they are[/]")
        return b"".join(clips)


SR = 24000  # edge-tts speaks 24 kHz mono
PAUSE_S = {"…": 0.28, "।": 0.42, "!": 0.36, "?": 0.45}  # after a piece, by how it ends; else 0.2


def _ffmpeg(args: list[str], data: bytes) -> bytes:
    import subprocess

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args], input=data, capture_output=True,
                          check=True, creationflags=flags).stdout


def _stitch(clips: list[bytes], pieces: list[str]) -> bytes:
    """One mp3 from the pieces: each clip's own ~1 s of lead-in and tail cut, and a pause of our choosing
    between them (a breath after '…', a full stop after '।'). Needs ffmpeg; without it they are just joined."""
    import shutil

    if len(clips) == 1 or not shutil.which("ffmpeg"):
        return b"".join(clips)
    import numpy as np

    out = [np.zeros(int(0.12 * SR), np.int16)]
    for clip, piece in zip(clips, pieces):
        x = np.frombuffer(_ffmpeg(["-i", "-", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], clip), np.int16)
        loud = np.flatnonzero(np.abs(x) > 500)
        if loud.size:
            x = x[max(0, loud[0] - int(0.03 * SR)):loud[-1] + int(0.08 * SR)]
        out += [x, np.zeros(int(PAUSE_S.get(piece[-1], 0.2) * SR), np.int16)]
    pcm = np.concatenate(out).tobytes()
    return _ffmpeg(["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-"], pcm)


def _gemini_speak(text: str, emotion: str, hour: int) -> str:
    """The link through Gemini TTS (gemini_tts.py) as a cached mp3; raises when it cannot speak."""
    from . import gemini_tts

    late = hour >= 23 or hour < 5
    vid = gemini_tts.voice()
    # not keyed by model: a clip either model made serves again
    key = hashlib.sha1(f"gemini|{vid}|{gemini_tts.style(emotion, late)}|{text}".encode()).hexdigest()[:20]
    path = VOICE_DIR / f"{key}.mp3"
    if not path.exists():
        wav = gemini_tts.synthesize(text, emotion, late, vid)
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        # trim the silence at both ends, then mp3 like the edge-tts links
        trim = "silenceremove=start_periods=1:start_threshold=-45dB"
        tmp.write_bytes(_ffmpeg(["-i", "-", "-af", f"{trim},areverse,{trim},areverse,adelay=120",
                                 "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-"], wav))
        tmp.replace(path)
    return key


def speak(text: str, emotion: str = "warm", now: datetime | None = None, voice: str | None = None) -> str | None:
    """mp3 of the text said with `emotion`; returns the cache key (file stem). Gemini TTS when it is set up
    (GEMINI_API_KEY, JOCKEY_TTS), else edge-tts in JOCKEY_VOICE. When Gemini cannot speak (its free day is
    used up): None — the host stays quiet — or, with JOCKEY_FALLBACK=edge, edge-tts speaks."""
    from . import gemini_tts

    hour = (now or datetime.now()).hour
    if voice is None and gemini_tts.enabled():
        try:
            return _gemini_speak(text, emotion, hour)
        except Exception as e:
            if (settings.jockey_fallback or "quiet").lower() != "edge":
                console.log(f"[yellow]voice: Gemini cannot speak this link ({e}); the host stays quiet[/]")
                return None
            console.log(f"[yellow]voice: Gemini cannot speak this link ({e}); the Microsoft voice does[/]")
    voice = voice or settings.jockey_voice
    parts = _delivery(text, emotion, voice, hour)
    key = hashlib.sha1(f"{VOICE_STYLE}|{voice}|{parts}".encode()).hexdigest()[:20]
    path = VOICE_DIR / f"{key}.mp3"
    if not path.exists():
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(asyncio.run(_say(parts, voice)))
        tmp.replace(path)
    return key


def prefetch_audio(item: Item) -> None:
    """Start fetching the ad-free audio in the background so it is ready when its turn comes."""
    if item.yt and (item.kind == "song" or item.audio.startswith("/archive/yt/")):
        threading.Thread(target=lambda: _safe(archive.yt_audio, item.yt), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        console.log(f"[yellow]prefetch failed: {e}[/]")


def _link_model(links: str) -> tuple[bool, str | None]:
    """May the LLM write this link, and with which model? resident = only a model already loaded in
    Ollama (never a second resident LLM), always = settings.llm_model (may load it), never = templates."""
    mode = (links or settings.jockey_llm_links or "resident").lower()
    if mode == "never":
        return False, None
    if mode == "always":
        return True, None
    from .resolver.llm import resident_model

    model = resident_model()
    return bool(model), model


def next_item(exclude: list[str] | None = None, n_picked: int = 0, previous: str = "", talk: bool = True, use_llm: bool = True,
              focus: str = "", links: str = "") -> Item | None:
    item = pick(exclude, n_picked, focus=focus)
    if item is None:
        return None
    return announce(item, n_picked, previous, talk, use_llm, links)


def talks_before(item: Item, n_picked: int) -> bool:
    """Like a real RJ, the host does not talk before every song: the first item, programmes, new finds and
    caught songs always get a link, other songs every JOCKEY_TALK_EVERY-th pick."""
    every = max(1, settings.jockey_talk_every)
    return n_picked == 0 or item.kind == "episode" or item.discovery or item.caught or n_picked % every == 0


def announce(item: Item, n_picked: int = 0, previous: str = "", talk: bool = True, use_llm: bool = True, links: str = "") -> Item:
    """Start fetching the item's audio and give it the host's link (text + voice) when she talks before it."""
    prefetch_audio(item)
    if talk and talks_before(item, n_picked):
        text = None
        if use_llm and n_picked > 0 and not item.caught:  # the LLM would not know it was just caught
            allowed, model = _link_model(links)
            text = llm_link(item, previous, model=model) if allowed else None
        text = text or template_link(item, first=n_picked == 0)
        item.link_text = text
        try:
            key = speak(text, emotion_of(item))
            item.link_audio = f"/jockey/voice/{key}.mp3" if key else ""
        except Exception as e:  # no internet for the voice: the on-screen text still shows
            console.log(f"[yellow]voice failed: {e}[/]")
    return item


def record(kind: str, ref: str, outcome: str, title: str = "", artist: str = "", genre: str = "", programme: str = "",
           listened_s: float = 0.0, duration_s: float = 0.0, mood: str = "") -> None:
    """Store what happened; a like/dislike on a song is also a hand decision on that song."""
    from .db import set_manual_status

    with session() as s:
        s.add(Play(kind=kind, ref=ref, outcome=outcome, title=title, artist=artist, genre=genre, programme=programme,
                   listened_s=listened_s, duration_s=duration_s, mood=mood))
        if ref.startswith("song:") and outcome in ("like", "dislike"):
            song = s.get(Song, int(ref.split(":", 1)[1]))
            if song:
                set_manual_status(song, "approved" if outcome == "like" else "rejected")
        s.commit()
