"""Old Vividh Bharati / Radio Ceylon programmes, ad-free.

Episodes come from two places:
  archive.org — older off-air recordings (Binaca Geetmala, Jaimala with guest artists, ...),
                played straight from archive.org's MP3 files
  YouTube     — listeners' off-air uploads; the audio is fetched once with yt-dlp and served by
                this app, so there is no YouTube player and no YouTube ads
Search results are noisy, so an episode is kept only if its title names the programme
(English or Devanagari spelling).
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import settings

CACHE = settings.data_dir / "archive_cache.json"
CACHE_TTL = 3 * 24 * 3600
AUDIO_DIR = settings.data_dir / "archive_audio"
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
IA_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


@dataclass
class Programme:
    name: str
    hindi: str
    about: str
    aliases: list[str]
    yt_queries: list[str]
    ia_query: str = ""
    exclude: list[str] = field(default_factory=list)
    spoken: list[str] = field(default_factory=list)  # what a request may say ("horror", "bhoot") — never matched to titles
    intro: list[str] = field(default_factory=list)  # the host's own lines for it ({name}, {title}); else the general ones
    vintage: bool = True  # an old radio programme: also search for its 1990s/2000s off-air recordings


@dataclass
class Episode:
    source: str  # yt | ia
    id: str
    title: str
    duration: str = ""
    year: int | None = None  # broadcast year when the title or archive.org says so


THIS_YEAR = time.localtime().tm_year
FULL_YEAR = re.compile(r"(?<!\d)(19[4-9]\d|20[0-4]\d)(?!\d)")
# 16 6 15 · 03.09.2022 · 21-08-25 · 091216 (day month year)
DMY = re.compile(r"(?<!\d)(\d{1,2})[ .\-/_](\d{1,2})[ .\-/_](\d{2})(?!\d)")
DMY_PACKED = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")
DECADES = [("before 1996", 0, 1995), ("1996–2005", 1996, 2005), ("2006–2015", 2006, 2015), ("2016–now", 2016, 9999)]


def _two_digit(yy: int) -> int:
    return 2000 + yy if 2000 + yy <= THIS_YEAR else 1900 + yy


def year_of(title: str) -> int | None:
    """Broadcast year written in an episode title, if any."""
    m = FULL_YEAR.search(title)
    if m and int(m.group(1)) <= THIS_YEAR:
        return int(m.group(1))
    for rx in (DMY, DMY_PACKED):
        for d, mo, yy in rx.findall(title):
            if 1 <= int(d) <= 31 and 1 <= int(mo) <= 12:
                return _two_digit(int(yy))
    return None


def decade_of(year: int | None) -> str:
    if year is None:
        return "year unknown"
    return next(label for label, lo, hi in DECADES if lo <= year <= hi)


PROGRAMMES: dict[str, Programme] = {
    "hawa-mahal": Programme(
        "Hawa Mahal", "हवा महल", "Short radio plays and comedy sketches (natika), ~15 min",
        ["hawa mahal", "hawamahal", "हवा महल", "हवामहल"],
        ["hawa mahal natika vividh bharati", "hawa mahal vividh bharati", "हवा महल नाटिका"], '"hawa mahal" AND (natika OR natak OR vividh OR radio)',
        exclude=["jaipur", "palace", "cultural", "winds of music", "tour", "vlog", "rajasthani", "animation", "dance"]),
    "chhaya-geet": Programme(
        "Chhaya Geet", "छायागीत", "Late-night themed songs with an announcer's words, ~30 min",
        ["chhaya geet", "chhayageet", "chaya geet", "छायागीत", "छाया गीत"],
        ["chhaya geet vividh bharati", "छायागीत विविध भारती"], '"chhaya geet"'),
    "jaimala": Programme(
        "Jaimala", "जयमाला", "Songs for the armed forces, often with a guest film artist, ~40 min",
        ["jaimala", "jaymala", "जयमाला"],
        ["jaimala vividh bharati", "जयमाला विविध भारती"], "jaimala"),
    "bhoole-bisre-geet": Programme(
        "Bhoole Bisre Geet", "भूले बिसरे गीत", "Forgotten songs of the 1930s–50s, ~30 min",
        ["bhoole bisre", "bhule bisre", "bhule bisare", "bhoole bisare", "भूले बिसरे"],
        ["bhoole bisre geet vividh bharati", "भूले बिसरे गीत विविध भारती"], '"bhoole bisre geet" OR "bhule bisre geet"'),
    "sangeet-sarita": Programme(
        "Sangeet Sarita", "संगीत सरिता", "Morning raga-based film songs with a musician guest, ~15 min",
        ["sangeet sarita", "संगीत सरिता"],
        ["sangeet sarita vividh bharati", "संगीत सरिता विविध भारती"], '"sangeet sarita"'),
    "geetmala": Programme(
        "Geetmala", "गीतमाला", "Binaca/Cibaca Geetmala with Ameen Sayani, and Vividh Bharati ki Geetmala",
        ["geetmala", "geethmala", "geet mala", "गीतमाला"],
        ["binaca geetmala ameen sayani", "vividh bharati ki geetmala", "cibaca geetmala"], 'geetmala OR geethmala'),
    "inse-miliye": Programme(
        "Inse Miliye", "इनसे मिलिए", "Interviews with film artists",
        ["inse miliye", "इनसे मिलिए", "inse miliyae"],
        ["inse miliye vividh bharati", "इनसे मिलिए विविध भारती"], '"inse miliye"'),
    "triveni": Programme(
        "Triveni", "त्रिवेणी", "Morning programme: three songs woven around one thought, ~15 min",
        ["triveni", "त्रिवेणी"],
        ["triveni vividh bharati", "त्रिवेणी विविध भारती"], ""),
    # horror for the late night — before Khooni Monday: "english horror" names both
    "radio-horror": Programme(
        "Classic Radio Horror", "अंग्रेज़ी रेडियो की डरावनी कहानियाँ",
        "American radio horror plays of the 1940s–50s in English: Inner Sanctum, Quiet Please, Lights Out, "
        "The Mysterious Traveler, The Witch's Tale; ~30 min",
        ["quiet please", "lights out", "inner sanctum", "witch's tale", "witchs tale", "mysterious traveler"], [],
        '(title:"quiet please" OR title:"lights out" OR title:"inner sanctum" OR title:"witch\'s tale" '
        'OR title:"mysterious traveler") AND (otr OR "old time radio" OR radio)',
        exclude=["episode guide", "log", "script"],
        spoken=["english horror", "angrezi horror", "english bhoot", "old time radio", "inner sanctum", "quiet please"],
        intro=["अब ज़रा पुराने अंग्रेज़ी रेडियो की एक डरावनी कहानी… {title}। बत्तियाँ धीमी कर लीजिए!",
               "रात गहरी है… सुनिए {name} — {title}।"],
        vintage=False),
    "khooni-monday": Programme(
        "Khooni Monday", "ख़ूनी मंडे", "Hindi horror stories told as audio, many of them 'सच्ची कहानी' "
        "(listeners' true accounts); ~10–60 min",
        ["khooni monday", "ख़ूनी मंडे", "खूनी मंडे"],
        ["khooni monday", "khooni monday horror stories in hindi", "khooni monday podcast"], "",
        exclude=["trailer", "teaser", "promo", "rap song", "rap video", "raps", "music video", "react"],  # its raps are songs
        spoken=["horror", "bhoot", "darawni", "daravni", "chudail", "हॉरर", "भूत", "डरावनी", "चुड़ैल"],
        intro=["बत्तियाँ धीमी कर लीजिए… अब वक़्त है {name} का! सुनिए — {title}।",
               "दिल थाम के बैठिए… आज की डरावनी कहानी — {title}।"],
        vintage=False),
}


MIN_SECONDS = 300  # shorter uploads are clips and shorts, not episodes


def _seconds(duration: str) -> int:
    try:
        parts = [int(x) for x in duration.split(":")]
    except ValueError:
        return 0
    return sum(v * 60**i for i, v in enumerate(reversed(parts)))


NOT_EPISODES = ("full movie", "hindi movie", "full film", "movie scene", "trailer", "#shorts")


def _matches(title: str, p: Programme) -> bool:
    t = title.lower()
    return any(a in t for a in p.aliases) and not any(x in t for x in p.exclude + list(NOT_EPISODES))


def _yt_search(p: Programme, limit: int = 40) -> list[Episode]:
    from ytmusicapi import YTMusic

    yt, out, seen = YTMusic(), [], set()
    old = old_queries(p) if p.vintage else []
    for q in p.yt_queries + old:
        try:
            hits = yt.search(q, filter="videos", limit=40)
        except Exception:
            continue
        for h in hits:
            vid, title = h.get("videoId"), h.get("title") or ""
            if vid and vid not in seen and _matches(title, p):
                seen.add(vid)
                out.append(Episode("yt", vid, title, h.get("duration") or "", year_of(title)))
    # yt-dlp finds the listener uploads YouTube Music misses — always for the old-recording queries
    out += _ytdlp_search(p, old + (p.yt_queries if len(out) < 15 else []), seen, limit)
    return out[:limit]


def old_queries(p: Programme) -> list[str]:
    """Searches aimed at recordings from the 1990s and 2000s, not just recent uploads."""
    return [f"{p.name} old recording", f"{p.name} 1990s vividh bharati", f"{p.name} 2000 vividh bharati",
            f"purana {p.name}", f"{p.hindi} पुरानी रिकॉर्डिंग"]


def _ytdlp_search(p: Programme, queries: list[str], seen: set[str], limit: int = 40) -> list[Episode]:
    """yt-dlp's YouTube search finds listener uploads that YouTube Music's video search misses."""
    import yt_dlp

    out = []
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "noprogress": True}) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(f"ytsearch30:{q}", download=False)
            except Exception:
                continue
            for h in info.get("entries") or []:
                vid, title = h.get("id"), h.get("title") or ""
                if vid and vid not in seen and YT_ID.match(vid) and _matches(title, p):
                    seen.add(vid)
                    secs = int(h.get("duration") or 0)
                    out.append(Episode("yt", vid, title, f"{secs // 60}:{secs % 60:02d}" if secs else "", year_of(title)))
    return out[:limit]


def _ia_search(p: Programme, limit: int = 100) -> list[Episode]:
    if not p.ia_query:
        return []
    try:
        r = httpx.get(
            "https://archive.org/advancedsearch.php",
            params={"q": f"({p.ia_query}) AND mediatype:audio", "fl[]": ["identifier", "title", "year", "date"], "rows": limit, "output": "json"},
            timeout=30,
        )
        docs = r.json()["response"]["docs"]
    except Exception:
        return []
    out = []
    for d in docs:
        title = d.get("title") or d["identifier"]
        if not _matches(title, p):
            continue
        meta_year = str(d.get("year") or (d.get("date") or "")[:4])
        year = year_of(title) or (int(meta_year) if meta_year.isdigit() and 1940 <= int(meta_year) <= THIS_YEAR else None)
        out.append(Episode("ia", d["identifier"], title, "", year))
    return out


def by_year(eps: list[Episode]) -> list[Episode]:
    """Newest first, undated last."""
    return sorted(eps, key=lambda e: (e.year is None, -(e.year or 0)))


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_cache_lock = threading.Lock()


def episodes(slug: str, refresh: bool = False) -> list[Episode]:
    """Episodes of one programme: archive.org first (older), then YouTube. Cached for 3 days."""
    p = PROGRAMMES[slug]
    with _cache_lock:
        cache = _load_cache()
        hit = cache.get(slug)
        has_years = hit and all("year" in e for e in hit["episodes"])  # cached before years existed → refetch
        if has_years and not refresh and time.time() - hit["at"] < CACHE_TTL:
            return [Episode(**e) for e in hit["episodes"]]
    eps = by_year([e for e in _ia_search(p) + _yt_search(p, limit=150) if not e.duration or _seconds(e.duration) >= MIN_SECONDS])
    if not eps and hit:  # both sources down: keep what we had
        return [Episode(**e) for e in hit["episodes"]]
    with _cache_lock:
        cache = _load_cache()
        cache[slug] = {"at": time.time(), "episodes": [asdict(e) for e in eps]}
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return eps


def ia_files(identifier: str) -> list[dict]:
    """Playable MP3 files inside one archive.org item."""
    if not IA_ID.match(identifier):
        raise ValueError("bad archive.org identifier")
    meta = httpx.get(f"https://archive.org/metadata/{identifier}", timeout=30).json()
    files = [f for f in meta.get("files", []) if f.get("name", "").lower().endswith(".mp3")]
    originals = [f for f in files if f.get("source") == "original"] or files
    return [
        {"title": f.get("title") or Path(f["name"]).stem, "url": f"https://archive.org/download/{identifier}/{quote(f['name'])}"}
        for f in sorted(originals, key=lambda f: f["name"])
    ]


_dl_locks: dict[str, threading.Lock] = {}


def yt_audio(video_id: str) -> Path:
    """Local audio file for a YouTube episode, fetched with yt-dlp on first play."""
    if not YT_ID.match(video_id):
        raise ValueError("bad video id")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    lock = _dl_locks.setdefault(video_id, threading.Lock())
    with lock:
        done = [f for f in AUDIO_DIR.glob(f"{video_id}.*") if f.suffix in (".m4a", ".webm", ".mp3", ".opus")]
        if done:
            return done[0]
        import yt_dlp

        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio",
            "outtmpl": str(AUDIO_DIR / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "noprogress": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        done = [f for f in AUDIO_DIR.glob(f"{video_id}.*") if f.suffix in (".m4a", ".webm", ".mp3", ".opus")]
        if not done:
            raise RuntimeError("download produced no audio file")
        return done[0]


MEDIA_TYPES = {".m4a": "audio/mp4", ".webm": "audio/webm", ".opus": "audio/ogg", ".mp3": "audio/mpeg"}


NEW_LOG = settings.data_dir / "new_episodes.json"


def fetch_new(per_programme: int = 2) -> list[dict]:
    """Overnight: refresh every programme and download a couple of unplayed YouTube episodes of each,
    so they play instantly. Programmes the listener keeps skipping are left alone."""
    from sqlmodel import select

    from .db import Play, session, utcnow

    with session() as s:
        plays = s.exec(select(Play).where(Play.kind == "episode")).all()
    heard = {p.ref for p in plays}
    score: dict[str, float] = {}
    for p in plays:
        score[p.programme] = score.get(p.programme, 0) + {"like": 3, "full": 1, "skip": -1, "dislike": -4}.get(p.outcome, 0)
    got = []
    for slug in PROGRAMMES:
        if score.get(slug, 0) <= -6:
            continue
        try:
            eps = episodes(slug, refresh=True)
        except Exception:
            continue
        have = {f.stem for f in AUDIO_DIR.glob("*.*")} if AUDIO_DIR.exists() else set()
        fresh = [e for e in eps if e.source == "yt" and f"yt:{e.id}" not in heard and e.id not in have][:per_programme]
        for e in fresh:
            try:
                yt_audio(e.id)
                got.append({"programme": slug, "title": e.title, "id": e.id, "at": utcnow().isoformat()})
            except Exception:
                continue
    try:
        log = json.loads(NEW_LOG.read_text(encoding="utf-8"))
    except Exception:
        log = []
    NEW_LOG.write_text(json.dumps((got + log)[:200], ensure_ascii=False, indent=1), encoding="utf-8")
    return got
