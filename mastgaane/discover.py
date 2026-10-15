"""Find new songs from the listener's own taste instead of Instagram's.

Seeds are songs the listener approved (hand-approved ones first) plus a few singers from the
rules.yaml folk/ghazal lists. Each seed starts a YouTube Music "radio"; every unseen track there
is classified and queued as *pending* for the Discover page, where a thumbs-up makes it a seed
for the next round. Search and radio work without a YouTube Music login.
"""
from __future__ import annotations

import random

from rich.console import Console
from sqlmodel import select

from .db import MANUAL, Song, session
from .playlists.youtube_music import YouTubeMusicAdapter
from .resolver.classify import classify, rules
from .resolver.common import norm_key

console = Console()
TIER_DISCOVERED = 4


def _yt():
    return YouTubeMusicAdapter(anonymous=True)._yt()


def _year(v) -> int | None:
    try:
        y = int(str(v)[:4])
    except (TypeError, ValueError):
        return None
    return y if 1930 <= y <= 2100 else None


def match_missing(statuses: tuple[str, ...] = ("approved", "pending"), limit: int = 500) -> int:
    """Give songs a YouTube video id (so the in-app player can play them) without needing a login."""
    yt = YouTubeMusicAdapter(anonymous=True)
    found = 0
    with session() as s:
        rows = s.exec(select(Song).where(Song.status.in_(statuses), Song.yt_video_id.is_(None)).limit(limit)).all()
        for song in rows:
            try:
                t = yt.search(song.title, song.artist)
            except Exception as e:
                console.log(f"[yellow]search failed: {song.title}: {e}[/]")
                continue
            if t:
                song.yt_video_id = t.id
                s.commit()
                found += 1
        console.print(f"matched {found}/{len(rows)} songs to YouTube")
    return found


def fill_years(limit: int = 500) -> int:
    """Release year for songs that have a YouTube match (album year — a re-release can make it later)."""
    yt = _yt()
    found = 0
    with session() as s:
        rows = s.exec(select(Song).where(Song.yt_video_id.is_not(None), Song.year.is_(None), Song.status != "rejected").limit(limit)).all()
        for song in rows:
            try:
                tr = yt.get_watch_playlist(videoId=song.yt_video_id, limit=1)["tracks"][0]
            except Exception:
                continue
            y = _year(tr.get("year")) if tr.get("videoId") == song.yt_video_id else None
            if y:
                song.year = y
                s.commit()
                found += 1
    console.print(f"years found for {found}/{len(rows)} songs")
    return found


def _artist_seeds(n: int) -> list[tuple[str, str]]:
    r = rules()
    pool = list(r.get("folk_artists", [])) + list(r.get("ghazal_artists", []))
    out = []
    for artist in random.sample(pool, min(n, len(pool))):
        try:
            hit = next((h for h in _yt().search(artist, filter="songs", limit=3) if h.get("videoId")), None)
        except Exception:
            hit = None
        if hit:
            out.append((hit["videoId"], f"{hit.get('title', '')} — {artist}"))
    return out


def _query_seeds(n: int) -> list[tuple[str, str]]:
    """Seeds from rules.yaml `seed_queries` — searches that steer discovery (e.g. 1990s–2000s ghazals)."""
    out = []
    for q in random.sample(list(rules().get("seed_queries", [])), min(n, len(rules().get("seed_queries", [])))):
        try:
            hit = next((h for h in _yt().search(q, filter="songs", limit=5) if h.get("videoId")), None)
        except Exception:
            hit = None
        if hit:
            out.append((hit["videoId"], f"{hit.get('title', '')} (search: {q})"))
    return out


def seeds(n: int = 12, artist_share: float = 0.4, query_share: float = 0.25) -> list[tuple[str, str]]:
    """(video id, label) pairs: hand-approved songs first, then other approved songs, then
    rules.yaml seed searches, then singers."""
    with session() as s:
        liked = s.exec(select(Song).where(Song.status == "approved", Song.yt_video_id.is_not(None))).all()
    mine = [x for x in liked if x.classify_reason.startswith(MANUAL)]
    # then songs a rules.yaml singer list vouches for; songs only the LLM approved come last
    ruled = [x for x in liked if x not in mine and x.classify_reason.startswith(("ghazal artist", "folk artist", "always-keep", "folk form"))]
    rest = random.sample(ruled, len(ruled)) + random.sample([x for x in liked if x not in mine and x not in ruled], len(liked) - len(mine) - len(ruled))
    n_artists = max(1, round(n * artist_share))
    n_queries = max(1, round(n * query_share))
    picked = random.sample(mine, min(len(mine), n - n_artists - n_queries))
    picked += rest[: max(0, n - n_artists - n_queries - len(picked))]
    out = [(x.yt_video_id, f"{x.title} — {x.artist}") for x in picked]
    out += _query_seeds(n_queries)
    return out + _artist_seeds(n - len(out))


def discover(n_seeds: int = 12, per_seed: int = 25, max_new: int = 60) -> dict:
    """Queue up to `max_new` unseen songs from radios of the seeds. Returns counts."""
    yt = _yt()
    kept = rejected = 0
    with session() as s:
        known = set(s.exec(select(Song.norm_key)).all())
        # each seed gets an equal share, so one radio cannot fill the whole round
        quota = max(3, -(-max_new // n_seeds))
        for vid, label in seeds(n_seeds):
            if kept + rejected >= max_new:
                break
            taken = 0
            try:
                tracks = yt.get_watch_playlist(videoId=vid, radio=True, limit=per_seed).get("tracks", [])
            except Exception as e:
                console.log(f"[yellow]radio failed for {label}: {e}[/]")
                continue
            console.print(f"[bold]radio[/] {label}: {len(tracks)} tracks")
            for t in tracks[1:]:  # [0] is the seed itself
                if taken >= quota:
                    break
                title, tvid = t.get("title") or "", t.get("videoId")
                artist = ", ".join(a.get("name", "") for a in t.get("artists") or [])
                key = norm_key(title, artist)
                if not tvid or not title or key in known:
                    continue
                known.add(key)
                v = classify(title, artist)
                s.add(Song(
                    title=title, artist=artist, norm_key=key, source_tier=TIER_DISCOVERED, confidence=0.9,
                    yt_video_id=tvid, keep=v.keep, genre=v.genre, language=v.language, year=_year(t.get("year")),
                    classify_reason=f"radio from {label}; {v.reason}"[:500], status="pending" if v.keep else "rejected",
                ))
                s.commit()
                kept, rejected, taken = kept + v.keep, rejected + (not v.keep), taken + 1
                console.print(f"  {'+' if v.keep else '·'} {title} — {artist}  ({v.language}/{v.genre}{', ' + str(t.get('year')) if t.get('year') else ''})")
                if kept + rejected >= max_new:
                    break
    console.print(f"discover: {kept} queued for you, {rejected} filtered out")
    return {"queued": kept, "filtered": rejected}
