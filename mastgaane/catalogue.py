"""The song catalogue in git: every song MastGaane knows, as one small file a new install loads.

The database (data/mastgaane.db) stays out of git: it also holds your Instagram reels and what you
listened to and when. `mastgaane export-songs` writes only the songs (title, artist, YouTube id, genre,
language, mood, year, status, playlist sync) to library/songs.jsonl.gz; `mastgaane import-songs` adds
the ones a database does not have yet and never changes one it has, so a new computer (a Mac) starts
with the whole collection, and a later `git pull` + import brings in the new finds.

No audio is in it: the songs stream from YouTube and stay in data/archive_audio after their first play.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from .config import ROOT
from .db import PlaylistItem, Song, session, utcnow

DEFAULT = ROOT / "library" / "songs.jsonl.gz"
FIELDS = ("title", "artist", "norm_key", "source_tier", "confidence", "genre", "language", "year", "keep",
          "classify_reason", "status", "times_seen", "first_seen", "last_seen", "yt_video_id", "spotify_uri",
          "apple_url", "apple_id", "apple_name", "mood")
DATES = ("first_seen", "last_seen")


def export(path: Path = DEFAULT) -> int:
    """Every song to `path` (gzipped JSON lines, sorted, so an unchanged collection gives the same file)."""
    with session() as s:
        synced: dict[int, list[dict]] = {}
        for pi in s.exec(select(PlaylistItem).order_by(PlaylistItem.target)).all():
            synced.setdefault(pi.song_id, []).append(
                {"target": pi.target, "playlist_id": pi.playlist_id, "track_id": pi.track_id})
        lines = []
        for song in s.exec(select(Song).order_by(Song.norm_key)).all():
            row = {f: getattr(song, f) for f in FIELDS}
            for f in DATES:
                row[f] = row[f].isoformat() if row[f] else None
            if song.id in synced:
                row["synced"] = synced[song.id]  # already in your YouTube Music playlist: not added twice
            lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(("\n".join(lines) + "\n").encode("utf-8"))
    tmp.replace(path)
    return len(lines)


def _date(value) -> datetime:
    try:
        return datetime.fromisoformat(value) if value else utcnow()
    except (TypeError, ValueError):
        return utcnow()


def load(path: Path = DEFAULT) -> tuple[int, int]:
    """Add the songs in `path` that this database does not have. Returns (added, already there)."""
    opener = gzip.open if path.suffix == ".gz" else open
    added = known = 0
    with session() as s:
        have = set(s.exec(select(Song.norm_key)).all())
        new: list[tuple[Song, list[dict]]] = []
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not row.get("norm_key") or not row.get("title"):
                    continue
                if row["norm_key"] in have:
                    known += 1
                    continue
                song = Song(**{k: row[k] for k in FIELDS if k in row and k not in DATES},
                            **{k: _date(row.get(k)) for k in DATES})
                s.add(song)
                have.add(song.norm_key)
                new.append((song, row.get("synced") or []))
                added += 1
        s.flush()  # the new songs' ids, for their playlist entries
        for song, items in new:
            for it in items:
                s.add(PlaylistItem(song_id=song.id, target=it.get("target", ""),
                                   playlist_id=it.get("playlist_id", ""), track_id=it.get("track_id", "")))
        s.commit()
    return added, known
