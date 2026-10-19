"""The big collection — what a good FM station has on its shelves: about `target` songs (rules.yaml
`library`), nearly all from before 2010, at most `modern_max` from 2010 on (the listener's own songs
included).

Filled from YouTube Music, no login: each singer or composer in `library.artists` (their top songs, then
their albums — up to their cap of kept songs), then the `library.playlists` searches, in turns, so that a
run stopped early is still a mix. Cheap filters first (remixes, lofi, jukeboxes, medleys, another upload
of a song already there); then the local LLM labels each song in batches — the language it is SUNG in
(a Lata list holds Marathi, a Hemant Kumar one is half Bengali), genre, mood, the year of the original
film — and the rules.yaml language and genre gates apply. Kept songs land as status "library": the
jockey plays them like the listener's own, which keep a share of the picks (jockey.FAVOURITES_SHARE).

Only the local model is used, never a paid API. Resumable: every song looked at is in the database
(dropped ones as "rejected"), so a second run carries on where the first stopped.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from rich.console import Console
from sqlmodel import select

from .config import settings
from .db import Song, session
from .lexicon import letters, same_song, singers, work_key
from .resolver.classify import GENRES, LANGUAGES, Mood, rules
from .resolver.common import norm_key

console = Console()
TIER_LIBRARY = 6
MOODS_ALLOWED = Mood.__args__  # type: ignore[attr-defined]
MODERN_FROM = 2010
#: a source whose songs centre before this brings no song from 2010 on but remakes ("Ilu Ilu" 2018 under
#: Kavita Krishnamurthy, "Bhankas" 2020 under Bappi Lahiri — they credit the original composer)
OLD_ERA = 2000
#: a recording of a folk song or a bhajan is dated by the upload, not the song: never "from 2010 on", never a remake
TIMELESS = ("folk", "devotional")
BATCH = 30  # songs per LLM call (the 12B model: ~0.8 s a song)
PAUSE_S = 2.0  # between LLM calls: Dexter shares the GPU
SOURCE_TTL_S = 7 * 86400  # a source's track list is fetched again after a week
MIN_S, MAX_S = 90, 12 * 60  # shorter is a clip or a ringtone, longer a jukebox or a medley
ALBUMS_PER_ARTIST = 30  # at least; a big name (cap 500) gets cap // 3

#: what a song's name says about the upload: not the song as the film had it
_JUNK = re.compile(r"\b(?:remix|lo-?fi|slowed|reverb|jhankar|jhankaar|mashup|medley|karaoke|instrumental|cover|"
                   r"revival|8d|dj|non ?stop|jukebox|bass ?boosted|ringtone|trap|lyrical|whatsapp|status|"
                   r"recreated|reprise|unplugged|female version|male version|sad version|teaser|trailer|"
                   r"with dialogue|dialogue)\b"
                   r"|(?:revival|remix|jhankar|unplugged|reprise)\b", re.I)  # glued on: "Lagta NahinRevival" (live)
#: an album named like this is a compilation: its year is the re-release's, not the film's
_COMPILATION = re.compile(r"\b(?:best|hits|collection|magical|magic|forever|golden|romantic|sentimental|beats|"
                          r"jukebox|evergreen|classics?|legend|legends|vol|volume|top|greatest|memories|duets|"
                          r"essential|selection|ultimate|melodies|moods|special|tribute|ever|songs of|sings|"
                          r"nightingale|saga|treasure|gems|vintage|yaadein|bollywood|retro|presents)\b", re.I)


@dataclass
class Row:
    title: str
    artist: str
    video_id: str
    album: str = ""
    album_id: str = ""
    album_year: int | None = None  # from the album's own page (an album of the film: its year)
    source: str = ""
    seconds: int = 0


@dataclass
class Label:
    language: str
    genre: str
    mood: str
    year: int  # the original release, as the LLM knows it (0 = unknown)
    keep: bool


@dataclass
class Stats:
    kept: int = 0
    dropped: int = 0
    modern: int = 0
    by_source: Counter = field(default_factory=Counter)


# ----------------------------------------------------------------------------- settings
def config() -> dict:
    c = dict(rules().get("library") or {})
    c.setdefault("target", 5000)
    c.setdefault("modern_max", 200)
    c.setdefault("per_artist", 100)
    c.setdefault("per_playlist", 60)
    c.setdefault("artists", {})
    c.setdefault("playlists", [])
    return c


def _seconds(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    try:
        parts = [int(x) for x in str(v).split(":")]
    except ValueError:
        return 0
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def junk(title: str, seconds: int | None = None) -> bool:
    """Not the song as the film had it (a remix, a lofi or jhankar version, a medley…), or no song."""
    if _JUNK.search(title or ""):
        return True
    if any(p.lower() in (title or "").lower() for p in rules().get("always_skip_patterns", [])):
        return True
    return seconds is not None and seconds > 0 and not MIN_S <= seconds <= MAX_S


def compilation(album: str, artist: str = "") -> bool:
    """An album that gathers songs from many films: its year is not the songs'."""
    a = letters(album)
    return bool(_COMPILATION.search(album or "")) or any(len(n) >= 5 and n in a for n in (letters(x) for x in
                                                                                     re.split(r",|&", artist or "")))


# ----------------------------------------------------------------------------- YouTube Music
def _yt():
    from .discover import _yt as yt

    return yt()


def _row(t: dict, source: str, album_year: int | None = None, album: dict | None = None) -> Row | None:
    vid, title = t.get("videoId"), (t.get("title") or "").strip()
    if not vid or not title or t.get("isAvailable") is False:
        return None
    al = album or t.get("album") or {}
    return Row(title=title, artist=", ".join(n for a in t.get("artists") or [] if (n := _singer(a.get("name") or ""))),
               video_id=vid, album=al.get("name") or al.get("title") or "", album_id=al.get("id") or al.get("browseId") or "",
               album_year=album_year, source=source,
               seconds=int(t.get("duration_seconds") or _seconds(t.get("duration") or 0)))


#: what a playlist's video rows carry in the artist field instead of a singer (live: "939K views",
#: "Jan 21, 2022", "90's Gaane", the uploading channel: "Bollywood Classics", "Shemaroo Filmi Gaane", "YRF Music")
#: — the host would announce it
_NOT_ARTIST = re.compile(r"^\d|\bviews?$|^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w* \d{1,2}, \d{4}$"
                         r"|\b(?:music|songs?|gaane|geet|hits|classics?|bollywood|filmi|films?|studios?|records?|audio|"
                         r"entertainment|digital|media|channel|official|cassettes?|productions?|mehfil|vintage|hd|"
                         r"t-series|shemaroo|saregama|goldmines|tips|ultra|wings|ishtar)\b", re.I)


def _singer(name: str) -> str:
    """A credit as a singer's name ("Pankaj Udhas | (Music Arranged By : …)" → "Pankaj Udhas"), or "" for a
    channel, a view count or a date."""
    name = re.split(r"[|(\[]", name or "")[0].strip()
    return name if name and not _NOT_ARTIST.search(name) else ""


#: what an upload puts in brackets: "(From "Dil Se")", "[HD]", "(Female)" — cut off titles leave one open
_BRACKETS = re.compile(r"[\(\[][^\)\]]*(?:[\)\]]|$)")
#: where an upload's name leaves the song: "Song | Singer | Film", "Song : Singer"
_PARTS = re.compile(r"\s:\s|[|｜]")
#: "Song - Singer", "Song by Singer" — but also "Tansen - Kahe Guman Kare" (film first): split only off a known singer
_SOFT = re.compile(r"\s[-–]\s|\s+by\s+", re.I)
#: a part that is only the upload's words ("Full Video", "HD")
_FILLER = re.compile(r"(?:hd|4k|1080p|full|official|video|audio|song|lyrics?|lyrical|with|in|colou?r|\s)+", re.I)
#: what an upload adds after the song's name: "Video Song In COLOR", "audio with lyrics", "Full Song HD"
_UPLOAD_TAIL = re.compile(r"\s+(?:(?:full|official|hd|old|lyrical|superhit|hit)\s+)*(?:video|audio)\b.*$"
                          r"|[\s,]+(?:with lyrics|(?:song )?in colou?r|full song|hd|4k|1080p)\b.*$", re.I)


def song_name(title: str, names: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """The song's own name, as the host says it, and the singers the upload's name gives (`names`: the singers
    known, by letters): "Kaise Rahoon Chup [HD] Video Song : Lata Mangeshkar | Sadhana, Sanjay Khan | Inteqam"
    → ("Kaise Rahoon Chup", ["Lata Mangeshkar"])."""
    names = names or {}
    title = unicodedata.normalize("NFKC", title or "")  # "𝐄𝐤 𝐏𝐚𝐫𝐝𝐞𝐬𝐢" (a channel's bold letters) → "Ek Pardesi"
    parts = [p.strip() for p in _PARTS.split(_BRACKETS.sub(" ", title)) if p.strip() and not _FILLER.fullmatch(p.strip())]
    if len(parts) > 1 and letters(parts[0]) in names:  # "Lata Mangeshkar | Lag Ja Gale"
        parts = parts[1:] + parts[:1]
    found = [names[k] for p in parts[1:] for x in re.split(r",|&", p) if (k := letters(x)) in names]
    head = parts[0] if parts else ""
    soft = [p.strip() for p in _SOFT.split(head)]
    if len(soft) > 1 and any(letters(p) in names for p in soft):  # "Yeh Jeena Kiya Jeena - Noor Jehan - Wah Bhai Wah"
        found = [names[letters(p)] for p in soft if letters(p) in names] + found
        head = next((p for p in soft if letters(p) not in names and not _FILLER.fullmatch(p)), "")
    name = re.sub(r"\s+", " ", _UPLOAD_TAIL.sub("", head)).strip(" -–:")
    return name or (title or "").strip(), list(dict.fromkeys(found))


def _artist_page(yt, name: str) -> dict | None:
    """The artist page that is this name and has songs: YouTube Music has several pages for one singer
    (three "Khayyam"s, the first with no songs) and lists others first ("Ghulam Ali" → Ustad Bade Ghulam
    Ali Khan) — live, 17 Oct."""
    want = letters(name)
    hits = [h for h in yt.search(name, filter="artists", limit=10) if h.get("browseId")]
    for h in [h for h in hits if letters(h.get("artist") or h.get("title") or "") == want][:4]:
        try:
            page = yt.get_artist(h["browseId"])
        except Exception:
            continue
        if (page.get("songs") or {}).get("browseId") or page.get("albums"):
            return page
    return None


def _searched_rows(yt, name: str, limit: int) -> list[Row]:
    """No artist page with songs: a song search, keeping what credits this name."""
    who = singers(name)
    try:
        hits = yt.search(name, filter="songs", limit=limit)
    except Exception:
        return []
    return [r for t in hits if (r := _row(t, name)) and who & singers(r.artist)]


def artist_rows(yt, name: str, cap: int) -> list[Row]:
    """An artist's top songs, then songs from their albums (with the album's year) until there are
    about twice the cap — enough left after the filters; a song search when they have no page."""
    page = _artist_page(yt, name)
    if page is None:
        return _searched_rows(yt, name, max(60, cap * 2))
    rows: list[Row] = []
    songs = page.get("songs") or {}
    if songs.get("browseId"):
        try:
            rows += [r for t in yt.get_playlist(songs["browseId"], limit=max(100, cap * 2)).get("tracks") or []
                     if (r := _row(t, name))]
        except Exception as e:
            console.log(f"[yellow]library: {name}'s songs: {e}[/]")
    albums = page.get("albums") or {}
    if len(rows) < cap * 2 and albums:
        try:
            listed = (yt.get_artist_albums(albums["browseId"], albums.get("params"), limit=max(ALBUMS_PER_ARTIST, cap // 3))
                      if albums.get("browseId") and albums.get("params") else albums.get("results") or [])
        except Exception:
            listed = albums.get("results") or []
        for al in listed[:max(ALBUMS_PER_ARTIST, cap // 3)]:
            if len(rows) >= cap * 2:
                break
            if (_year(al.get("year")) or 0) >= MODERN_FROM:
                continue  # an old composer's newest albums are remakes and covers (live: "Dil Mein Chhupa Loonga")
            try:
                page_al = yt.get_album(al["browseId"])
            except Exception:
                continue
            year = _year(page_al.get("year"))
            if (year or 0) >= MODERN_FROM:
                continue
            info = {"name": page_al.get("title") or al.get("title", ""), "id": al["browseId"]}
            rows += [r for t in page_al.get("tracks") or [] if (r := _row(t, name, year, info))]
    return rows


def playlist_rows(yt, query: str, limit: int = 200) -> list[Row]:
    rows: list[Row] = []
    # a podcast is listed among the playlists, and get_playlist cannot read it (live: MPSP… for "bhojpuri vivah geet")
    for pl in [h for h in yt.search(query, filter="playlists", limit=5)
               if h.get("browseId") and not h["browseId"].startswith("MPSP")][:2]:
        try:
            rows += [r for t in yt.get_playlist(pl["browseId"], limit=limit).get("tracks") or []
                     if (r := _row(t, f"playlist: {query}"))]
        except Exception as e:
            console.log(f"[yellow]library: playlist {query}: {e}[/]")
    return rows


def _year(v) -> int | None:
    try:
        y = int(str(v)[:4])
    except (TypeError, ValueError):
        return None
    return y if 1930 <= y <= 2100 else None


# ----------------------------------------------------------------------------- the LLM
SYSTEM = """You label Indian songs for a Hindi radio station. Input: numbered lines "title — singers — album".
Answer one line per song, nothing else, in this exact form:
n|language|genre|mood|year|keep
language: hindi, urdu, bhojpuri, awadhi, maithili, magahi, braj, rajasthani, haryanvi, garhwali, kumaoni, punjabi, bengali, marathi, gujarati, tamil, telugu, english or other (the language it is SUNG in)
genre: film (Hindi film song), ghazal, folk, sufi, classical, indie, devotional or other
mood: sad, romantic, energetic, peaceful, happy, devotional, nostalgic or any
year: year of the ORIGINAL release (the film's year; an album named after a singer is a compilation), 0 if unknown
keep: yes for a sung song; no for a remix, medley, jhankar, instrumental, cover, recitation, dialogue or talk"""


def _ollama_label(rows: list[Row]) -> list[Label | None]:
    """The local model only (settings.library_llm_model: the 12B) — a paid API never runs for thousands of songs."""
    import httpx

    from .resolver.llm import _loaded_info, _ollama_base

    model = settings.library_llm_model or settings.llm_model
    info = _loaded_info(model) or {}
    lines = "\n".join(f"{i}. {r.title} — {r.artist} — {r.album}" for i, r in enumerate(rows, 1))
    body = {"model": model, "stream": False, "think": False, "keep_alive": settings.llm_keep_alive,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": lines}],
            "options": {"temperature": 0.1, "num_predict": 40 * len(rows) + 200,
                        "num_ctx": int(info.get("context_length") or 8192)}}
    r = httpx.post(f"{_ollama_base()}/api/chat", json=body, timeout=600)
    r.raise_for_status()
    return parse_labels(r.json()["message"]["content"], len(rows))


def parse_labels(text: str, n: int) -> list[Label | None]:
    out: list[Label | None] = [None] * n
    for ln in (text or "").splitlines():
        parts = [p.strip().lower() for p in ln.strip().strip("`").split("|")]
        if len(parts) < 6 or not parts[0].rstrip(".").isdigit():
            continue
        i = int(parts[0].rstrip(".")) - 1
        if not 0 <= i < n:
            continue
        lang = parts[1] if parts[1] in LANGUAGES else "other"
        genre = parts[2] if parts[2] in GENRES else "other"
        mood = parts[3] if parts[3] in MOODS_ALLOWED else "any"
        year = _year(re.sub(r"\D", "", parts[4])) or 0
        out[i] = Label(language=lang, genre=genre, mood=mood, year=year, keep=parts[5].startswith(("y", "t")))
    return out


# ----------------------------------------------------------------------------- building
class Shelf:
    """What is in the collection already: for duplicates, counts and the after-2010 cap."""

    def __init__(self):
        with session() as s:
            songs = s.exec(select(Song)).all()
        self.keys = {x.norm_key for x in songs}
        self.videos = {x.yt_video_id for x in songs if x.yt_video_id}
        self.works: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.kept = self.modern = 0
        self.per_source: Counter = Counter()
        self.years: dict[str, list[int]] = defaultdict(list)  # per source: its kept songs' years
        credits: Counter = Counter()
        for x in songs:
            if x.status != "rejected":
                self.works[work_key(x.title)].append((x.title, x.artist))
            if x.status in ("approved", "library"):
                self.kept += 1
                self.modern += bool(x.year and x.year >= MODERN_FROM and x.genre not in TIMELESS)
                if not x.classify_reason.startswith("library: playlist: "):  # a playlist's videos credit actors too
                    credits.update(n for p in re.split(r",|&", x.artist or "") if len(letters(n := _singer(p))) >= 5)
            if x.status == "library" and x.classify_reason.startswith("library: "):
                self.per_source[x.classify_reason[9:].split(";")[0]] += 1
                if x.year:
                    self.years[x.classify_reason[9:].split(";")[0]].append(x.year)
        #: the singers the collection knows, by letters: an upload's name may give one ("Song | Lata Mangeshkar | Film").
        #: Spelt as most songs credit them ("Lata Mangeshkar", not "LATA MANGESHKAR").
        total: Counter = Counter()
        for n, k in credits.items():
            total[letters(n)] += k
        self.names = {letters(n): n for n, _ in sorted(credits.items(), key=lambda nk: nk[1]) if total[letters(n)] >= 3}

    def tidy(self, r: Row) -> None:
        """The song's own name and singers, not the upload's: "Kaise Rahoon Chup [HD] Video Song : Lata Mangeshkar |
        Inteqam" by "Bollywood Classics" → "Kaise Rahoon Chup" by Lata Mangeshkar."""
        r.title, found = song_name(r.title, self.names)
        own = [n for p in (r.artist or "").split(",") if (n := _singer(p))]  # rows cached before the channel check
        if found and not any(letters(n) in self.names for n in own):  # a channel the pattern misses ("Navsaar Movies")
            own = found
        r.artist = ", ".join(own)

    def known(self, r: Row) -> bool:
        if r.video_id in self.videos or norm_key(r.title, r.artist) in self.keys:
            return True
        return any(same_song(r.title, r.artist, t, a) for t, a in self.works.get(work_key(r.title), ()))

    def era(self, source: str, more: list[int] = ()) -> int | None:
        """The median year of a source's songs (with this batch's), once there are enough to tell."""
        ys = sorted([*self.years.get(source, ()), *more])
        return ys[len(ys) // 2] if len(ys) >= 8 else None

    def add(self, r: Row, kept: bool, modern: bool, year: int | None = None) -> None:
        self.keys.add(norm_key(r.title, r.artist))
        self.videos.add(r.video_id)
        if kept:
            self.works[work_key(r.title)].append((r.title, r.artist))
            self.kept += 1
            self.modern += modern
            self.per_source[r.source] += 1
            if year:
                self.years[r.source].append(year)


def _sources(yt, c: dict) -> list[tuple[str, int, Callable[[], list[Row]]]]:
    out = []
    for name, cap in (c["artists"] or {}).items():
        cap = int(cap or c["per_artist"])
        out.append((name, cap, lambda name=name, cap=cap: artist_rows(yt, name, cap)))
    for q in c["playlists"] or []:
        out.append((f"playlist: {q}", int(c["per_playlist"]), lambda q=q: playlist_rows(yt, q)))
    return out


def _cached(name: str, fetch: Callable[[], list[Row]], cap: int = 0) -> list[Row]:
    """A source's rows, fetched once a week — per cap, since a bigger cap asks for more rows."""
    folder = settings.data_dir / "library_sources"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (re.sub(r"[^\w]+", "_", name.lower())[:80] + (f"_{cap}" if cap else "") + ".json")
    try:
        if time.time() - path.stat().st_mtime < SOURCE_TTL_S:
            return [Row(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        pass
    rows = fetch()
    if not rows:
        return rows  # a failed fetch is tried again next run, not kept for a week
    path.write_text(json.dumps([r.__dict__ for r in rows], ensure_ascii=False), encoding="utf-8")
    return rows


def _decide(r: Row, lab: Label | None, shelf: Shelf, c: dict, allowed_lang: set, allowed_genre: set,
            yt=None, batch_years: list[int] = ()) -> tuple[str, int | None, str]:
    """(status, year, why) for one labelled song."""
    if lab is None:
        return "", None, "not labelled"
    if not lab.keep:
        return "rejected", None, "not a song as sung"
    if lab.language not in allowed_lang:
        return "rejected", None, f"sung in {lab.language}"
    if lab.genre not in allowed_genre:
        return "rejected", None, f"genre {lab.genre}"
    year, how = None, ""
    if r.album_year and not compilation(r.album, r.artist):
        year, how = r.album_year, "album year"
    elif yt is not None and r.album_id and lab.year >= 2005 and not compilation(r.album, r.artist):
        try:  # near the line: the album's own year decides
            year, how = _year(yt.get_album(r.album_id).get("year")), "album year"
        except Exception:
            year = None
    if year is None and lab.year:
        year, how = lab.year, "LLM's year"
    modern = bool(year and year >= MODERN_FROM) and lab.genre not in TIMELESS
    era = shelf.era(r.source, batch_years) if modern and not r.source.startswith("playlist: ") else None
    old_name = era is None or era < OLD_ERA  # a source whose own songs are from 2010 on brings no remakes
    if modern and old_name and not r.source.startswith("playlist: ") and not (singers(r.source) & singers(r.artist)):
        # under an old composer's name on YouTube Music, without them: a remake or a stray (live: "King Of
        # Bihar" 2026 under Shankar-Jaikishan)
        return "rejected", year, f"from {year}, and not {r.source}'s"
    if era is not None and era < OLD_ERA:
        return "rejected", year, f"from {year}, under {r.source} (their songs: the {era // 10 * 10}s) — a remake"
    if modern and shelf.modern >= int(c["modern_max"]):
        return "rejected", year, f"from {year}: the collection has its {c['modern_max']} songs from 2010 on"
    return "library", year, f"{lab.language}/{lab.genre}" + (f", {year} ({how})" if year else "")


def build(target: int | None = None, modern_max: int | None = None, label: Callable | None = None,
          yt=None, log: Callable[[str], None] | None = None) -> Stats:
    """Fill the collection up to `target` kept songs (yours count). Returns what this run did."""
    c = config()
    if target is not None:
        c["target"] = target
    if modern_max is not None:
        c["modern_max"] = modern_max
    label = label or _ollama_label
    yt = yt if yt is not None else _yt()
    say = log or (lambda m: console.print(m))
    r_ = rules()
    allowed_lang = set(r_.get("allowed_languages") or []) or {"hindi", "urdu"}
    allowed_genre = set(r_.get("allowed_genres") or []) or {"film", "ghazal", "folk"}
    shelf, stats = Shelf(), Stats()
    say(f"library: {shelf.kept} songs now ({shelf.modern} from 2010 on); filling to {c['target']}")
    queues: dict[str, list[Row]] = {}
    caps: dict[str, int] = {}
    fetchers = {name: (cap, fetch) for name, cap, fetch in _sources(yt, c)}
    while shelf.kept < c["target"]:  # one batch per source per round: a run stopped early is still a mix
        progressed = False
        for name, (cap, fetch) in fetchers.items():
            if shelf.kept >= c["target"]:
                break
            if shelf.per_source[name] >= cap:
                continue
            if name not in queues:
                try:
                    queues[name] = _cached(name, fetch, cap)
                except Exception as e:
                    say(f"[yellow]library: {name}: {e}[/]")
                    queues[name] = []
                caps[name] = cap
            q = queues[name]
            batch: list[Row] = []
            keys: set[str] = set()  # norm_key is unique: two of a batch may share one ("Tum Ho" twice by one singer)
            while q and len(batch) < BATCH:
                r = q.pop(0)
                r.source = name
                if (r.album_year or 0) >= MODERN_FROM or junk(r.title, r.seconds):  # the upload's whole name
                    continue
                shelf.tidy(r)
                key = norm_key(r.title, r.artist)
                if key.startswith("|") or key in keys:  # "|…": no song's name in it ("#video BEST BHOJPURI GAZAL")
                    continue
                if shelf.known(r) or any(same_song(r.title, r.artist, b.title, b.artist) for b in batch):
                    continue
                keys.add(key)
                batch.append(r)
            if not batch:
                continue
            progressed = True
            try:
                labels = label(batch)
            except Exception as e:
                say(f"[red]library: the LLM failed ({e}); stopping — run it again to carry on[/]")
                return stats
            kept_now = dropped_now = 0
            batch_years = [lab.year for lab in labels if lab and lab.year]
            with session() as s:
                for r, lab in zip(batch, labels):
                    if shelf.per_source[name] >= cap or shelf.kept >= c["target"]:
                        q.insert(0, r)
                        continue
                    status, year, why = _decide(r, lab, shelf, c, allowed_lang, allowed_genre, yt=yt,
                                                batch_years=batch_years)
                    if not status:
                        continue  # not labelled: next run
                    keep = status == "library"
                    s.add(Song(title=r.title[:300], artist=r.artist[:300], norm_key=norm_key(r.title, r.artist),
                               source_tier=TIER_LIBRARY, confidence=0.8, yt_video_id=r.video_id, keep=keep,
                               genre=lab.genre, language=lab.language, year=year,
                               mood=lab.mood if keep else "", status=status,
                               classify_reason=f"library: {name}; {why}"[:500]))
                    modern = bool(keep and year and year >= MODERN_FROM and lab.genre not in TIMELESS)
                    shelf.add(r, keep, modern, year if keep else None)
                    stats.kept += keep
                    stats.dropped += not keep
                    stats.modern += modern
                    stats.by_source[name] += keep
                    kept_now += keep
                    dropped_now += not keep
                s.commit()
            say(f"  {name}: +{kept_now} kept, {dropped_now} dropped — {shelf.kept}/{c['target']}, "
                f"{shelf.modern}/{c['modern_max']} from 2010 on")
            time.sleep(PAUSE_S if label is _ollama_label else 0)
        if not progressed:
            break
    say(f"library: {stats.kept} added ({stats.modern} from 2010 on), {stats.dropped} dropped — "
        f"{shelf.kept} songs in all")
    return stats


def summary() -> dict:
    """The collection by status, decade, genre, language and mood."""
    with session() as s:
        songs = s.exec(select(Song).where(Song.status.in_(("approved", "library")))).all()
    decade = Counter(f"{x.year // 10 * 10}s" if x.year else "unknown" for x in songs)
    return {
        "songs": len(songs),
        "yours": sum(1 for x in songs if x.status == "approved"),
        "from_2010": sum(1 for x in songs if x.year and x.year >= MODERN_FROM and x.genre not in TIMELESS),
        "decades": dict(sorted(decade.items())),
        "genres": dict(Counter(x.genre for x in songs).most_common()),
        "languages": dict(Counter(x.language for x in songs).most_common()),
        "moods": dict(Counter(x.mood or "untagged" for x in songs).most_common(10)),
    }
