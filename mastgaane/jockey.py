"""MastGaane FM — a jockey that picks what plays next, talks between songs and learns from you.

What plays:
  songs     approved songs, weighted by taste (likes, full listens, skips per artist and genre),
            never one of the last ~40 things played; every 4th pick is a fresh discovery
  programmes archive episodes in time slots loosely after the real Vividh Bharati day
            (Sangeet Sarita in the morning, Jaimala at 7 pm, Hawa Mahal at 8, Chhaya Geet late)
  links     a short Hindi announcement before each item, written by the local LLM when there is
            time (the next item is prefetched while the current one plays) or from templates,
            and spoken by a Microsoft neural Hindi voice (edge-tts)
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel
from rich.console import Console
from sqlmodel import select

from . import archive
from .config import settings
from .db import MANUAL, Play, Song, session, utcnow

console = Console()
VOICE_DIR = settings.data_dir / "jockey_voice"
RECENT = 40  # never repeat anything from the last N plays
DISCOVERY_EVERY = 4

# (weekdays, start, end, programme slug); weekdays 0 = Monday; first match wins
SCHEDULE = [
    (range(7), "06:30", "08:30", "sangeet-sarita"),
    (range(7), "08:30", "10:00", "triveni"),
    (range(7), "13:00", "15:00", "bhoole-bisre-geet"),
    ((5, 6), "15:00", "17:00", "inse-miliye"),
    (range(7), "19:00", "20:00", "jaimala"),
    ((2,), "20:00", "22:00", "geetmala"),  # Binaca Geetmala was Wednesday, 8 pm
    (range(7), "20:00", "21:30", "hawa-mahal"),
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


def _weight(song: Song, t: dict) -> float:
    artists = [a.strip().lower() for a in song.artist.split(",") if a.strip()]
    a = max((t["artist"].get(x, 0.0) for x in artists), default=0.0)
    w = math.exp(max(-2.0, min(2.0, 0.4 * a + 0.2 * t["genre"].get(song.genre, 0.0))))
    return w * (1.5 if song.classify_reason.startswith(MANUAL) else 1.0)


def _recent_refs(s, n: int = RECENT) -> set[str]:
    return {p.ref for p in s.exec(select(Play).order_by(Play.at.desc()).limit(n)).all()}


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
    link_text: str = ""
    link_audio: str = ""


def _song_item(song: Song, discovery: bool) -> Item:
    return Item(ref=f"song:{song.id}", kind="song", title=song.title, artist=song.artist, genre=song.genre,
                yt=song.yt_video_id, audio=f"/jockey/audio/{song.yt_video_id}", discovery=discovery, year=song.year)


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
               "bhajans": "devotional", "devotional": "devotional"}
LANGUAGES = ("bhojpuri", "urdu", "awadhi", "maithili", "magahi", "hindi")
FILLER = {"play", "some", "a", "an", "the", "songs", "song", "music", "by", "of", "from", "gaane", "gane", "geet", "ke", "ki",
          "ka", "sunao", "chalao", "me", "please", "mastgaane", "on", "fm", "radio", "programme", "program"}


def _n(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def resolve_focus(focus: str) -> tuple[str, str]:
    """What a spoken request asks for: ("any", ""), ("programme", slug), ("genre", g), ("language", l),
    ("era", "old" | "1990s"…) or ("text", words to find in artist / title)."""
    import re

    f = focus.lower().strip()
    if not f:
        return ("any", "")
    nf = _n(f)
    for slug, p in archive.PROGRAMMES.items():
        if any(_n(x) and _n(x) in nf for x in (slug, p.name, p.hindi, *p.aliases)):
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
    return True


def pick(exclude: list[str] | None = None, n_picked: int = 0, now: datetime | None = None, focus: str = "") -> Item | None:
    """The next thing to play. `exclude` = refs already queued on the client; `n_picked` = how many
    the client has had this session (drives the 1-in-4 discovery mix); `focus` = a spoken wish
    ("ghazals", "Hawa Mahal", "Jagjit Singh", "90s") that narrows the choice — see resolve_focus()."""
    t = taste()
    want = resolve_focus(focus)
    with session() as s:
        skip = _recent_refs(s) | set(exclude or [])
        if want[0] == "programme":
            return _episode_item(want[1], set(exclude or []))
        prog = slot(now) if want[0] == "any" else None
        if prog:
            since = utcnow() - timedelta(hours=20)
            done = s.exec(select(Play).where(Play.programme == prog, Play.at >= since)).first()
            if done is None and t["programme"].get(prog, 0.0) > -6 and not any(r.startswith(("yt:", "ia:")) for r in exclude or []):
                ep = _episode_item(prog, skip)
                if ep:
                    return ep
        want_new = n_picked % DISCOVERY_EVERY == DISCOVERY_EVERY - 1
        new = [x for x in s.exec(select(Song).where(Song.status == "pending", Song.yt_video_id.is_not(None))).all()
               if f"song:{x.id}" not in skip and _matches(x, want)]
        kept = [x for x in s.exec(select(Song).where(Song.status == "approved", Song.yt_video_id.is_not(None))).all()
                if f"song:{x.id}" not in skip and _matches(x, want)]
        if want[0] != "any" and not new and not kept:  # a narrow wish runs dry: repeats beat silence
            exc = set(exclude or [])
            kept = [x for x in s.exec(select(Song).where(Song.status == "approved", Song.yt_video_id.is_not(None))).all()
                    if f"song:{x.id}" not in exc and _matches(x, want)]
        pools = [(new, True), (kept, False)] if want_new else [(kept, False), (new, True)]
        for pool, is_new in pools:
            if pool:
                song = random.choices(pool, weights=[_weight(x, t) for x in pool])[0]
                return _song_item(song, is_new)
    return None


# ----------------------------------------------------------------------------- the host's voice
class Link(BaseModel):
    text: str


GREETING = {5: "सुप्रभात", 12: "नमस्कार", 17: "शुभ संध्या", 21: "शुभ रात्रि"}


def _greeting(hour: int) -> str:
    return next((g for h, g in sorted(GREETING.items(), reverse=True) if hour >= h), "शुभ रात्रि")


def template_link(item: Item, first: bool = False, now: datetime | None = None) -> str:
    now = now or datetime.now()
    hello = f"{_greeting(now.hour)}! मस्त गाने एफ़ एम में आपका स्वागत है। " if first else ""
    if item.kind == "episode":
        when = f" सन {item.year} की यह रिकॉर्डिंग।" if item.year else ""
        return hello + random.choice([
            f"और अब वक़्त है {item.programme_name} का। सुनिए — {item.title}।",
            f"आइए, सुनते हैं विविध भारती का एक पुराना कार्यक्रम — {item.programme_name}।",
        ]) + when
    singer = item.artist.split(",")[0].strip()
    if item.discovery:
        return hello + random.choice([
            f"अब एक नया गाना, जो शायद आपने पहले नहीं सुना — {singer} की आवाज़ में, {item.title}। पसंद आए तो बताइएगा।",
            f"आज की खोज: {item.title}, {singer}। सुनिए और बताइए कैसा लगा।",
        ])
    return hello + random.choice([
        f"अब सुनिए {singer} की आवाज़ में — {item.title}।",
        f"आपकी पसंद का अगला गीत: {item.title}, {singer}।",
        f"चलिए, अगला नग़मा — {singer}, {item.title}।",
    ])


SYSTEM = (
    "You are the warm, soft-spoken announcer of a late-night Hindi radio channel in the old Vividh Bharati "
    "style. Write ONE or TWO short sentences in Hindi (Devanagari script, max 35 words) introducing the next "
    "item: name the singer and the song (song and singer names may be written in Devanagari). You may mention "
    "the mood or the time of day. Never invent facts such as film names, years or composers. No emojis."
)


def llm_link(item: Item, previous: str = "", now: datetime | None = None) -> str | None:
    from .resolver.llm import LLMUnavailable, complete_json

    now = now or datetime.now()
    what = (f"A recorded episode of the old Vividh Bharati programme '{item.programme_name}' "
            f"({archive.PROGRAMMES[item.programme].about}). Episode title: {item.title}. "
            + (f"Recorded in {item.year}. " if item.year else "") + "This is a programme, "
            "not a song; a person named in the title is its presenter or guest, not a singer." if item.kind == "episode"
            else f"Song: {item.title}\nSinger: {item.artist}\nGenre: {item.genre}" + (f"\nReleased: {item.year}" if item.year else "") + ("\n(This is a new discovery the listener has not heard.)" if item.discovery else ""))
    try:
        link = complete_json(SYSTEM, f"Time: {now:%H:%M}\nPrevious: {previous or 'nothing, this is the start'}\nNext:\n{what}", Link)
    except LLMUnavailable:
        return None
    text = link.text.strip()
    return text if 5 < len(text) < 400 else None


def speak(text: str) -> str:
    """mp3 of the text in the configured Hindi voice; returns the cache key (file stem)."""
    voice = settings.jockey_voice
    key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20]
    path = VOICE_DIR / f"{key}.mp3"
    if not path.exists():
        import edge_tts

        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        asyncio.run(edge_tts.Communicate(text, voice).save(str(tmp)))
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


def next_item(exclude: list[str] | None = None, n_picked: int = 0, previous: str = "", talk: bool = True, use_llm: bool = True,
              focus: str = "") -> Item | None:
    item = pick(exclude, n_picked, focus=focus)
    if item is None:
        return None
    prefetch_audio(item)
    if talk:
        text = (llm_link(item, previous) if use_llm and n_picked > 0 else None) or template_link(item, first=n_picked == 0)
        item.link_text = text
        try:
            item.link_audio = f"/jockey/voice/{speak(text)}.mp3"
        except Exception as e:  # no internet for the voice: the on-screen text still shows
            console.log(f"[yellow]voice failed: {e}[/]")
    return item


def record(kind: str, ref: str, outcome: str, title: str = "", artist: str = "", genre: str = "", programme: str = "",
           listened_s: float = 0.0, duration_s: float = 0.0) -> None:
    """Store what happened; a like/dislike on a song is also a hand decision on that song."""
    from .db import set_manual_status

    with session() as s:
        s.add(Play(kind=kind, ref=ref, outcome=outcome, title=title, artist=artist, genre=genre, programme=programme,
                   listened_s=listened_s, duration_s=duration_s))
        if ref.startswith("song:") and outcome in ("like", "dislike"):
            song = s.get(Song, int(ref.split(":", 1)[1]))
            if song:
                set_manual_status(song, "approved" if outcome == "like" else "rejected")
        s.commit()
