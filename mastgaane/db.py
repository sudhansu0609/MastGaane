from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Run(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    kind: str = "harvest"  # harvest | resolve | sync | run-all
    reels_seen: int = 0
    reels_new: int = 0
    with_music: int = 0
    notes: str = ""


class Reel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    shortcode: str = Field(index=True, unique=True)
    pk: Optional[str] = None
    owner: str = ""
    caption: str = Field(default="", sa_column=Column(Text))
    taken_at: Optional[datetime] = None
    seen_at: datetime = Field(default_factory=utcnow)
    run_id: Optional[int] = Field(default=None, foreign_key="run.id")
    ig_song_title: Optional[str] = None
    ig_artist: Optional[str] = None
    is_original_audio: bool = False
    audio_id: Optional[str] = None
    video_url: Optional[str] = None
    thumb_url: Optional[str] = None
    times_seen: int = 1
    watched: bool = False  # we actually navigated to it (vs only queued in the feed)
    song_id: Optional[int] = Field(default=None, foreign_key="song.id")
    resolve_status: str = "pending"  # pending | resolved | needs_review | skipped | failed
    resolve_note: str = ""
    raw_json: str = Field(default="", sa_column=Column(Text))

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/reel/{self.shortcode}/"


class Song(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("norm_key", name="uq_song_norm"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    artist: str = ""
    norm_key: str = Field(index=True)
    source_tier: int = 1  # 1 attribution | 2 fingerprint | 3 llm
    confidence: float = 0.0
    genre: str = ""  # film | ghazal | folk | sufi | classical | indie | devotional | other
    language: str = ""  # hindi | urdu | punjabi | english | ...
    year: Optional[int] = None  # release year when YouTube Music knows it
    keep: Optional[bool] = None  # classifier verdict
    classify_reason: str = ""
    status: str = "pending"  # pending | approved | rejected
    times_seen: int = 1
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)
    yt_video_id: Optional[str] = None
    spotify_uri: Optional[str] = None
    apple_url: Optional[str] = None
    lyrics_snippet: str = Field(default="", sa_column=Column(Text))


class PlaylistItem(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("song_id", "target", name="uq_item"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    song_id: int = Field(foreign_key="song.id")
    target: str  # ytmusic | spotify | apple
    playlist_id: str = ""
    track_id: str = ""
    synced_at: datetime = Field(default_factory=utcnow)


class Play(SQLModel, table=True):
    """One thing the listener heard (or skipped / liked) on MastGaane FM — the taste signal."""
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # song | episode
    ref: str = Field(index=True)  # song:<id> | yt:<video id> | ia:<identifier>
    title: str = ""
    artist: str = ""
    genre: str = ""
    programme: str = ""  # archive slug for episodes
    outcome: str = ""  # full | skip | like | dislike
    listened_s: float = 0.0
    duration_s: float = 0.0
    at: datetime = Field(default_factory=utcnow, index=True)


_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
        _migrate(_engine)
    return _engine


def _migrate(eng) -> None:
    """Add columns that were introduced after a DB was created (SQLite has no ALTER-less sync)."""
    from sqlalchemy import inspect, text

    insp = inspect(eng)
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ctype = col.type.compile(eng.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ctype}'))


def session() -> Session:
    return Session(engine())


def get_song_by_key(s: Session, key: str) -> Optional[Song]:
    return s.exec(select(Song).where(Song.norm_key == key)).first()


MANUAL = "you: "


def set_manual_status(song: Song, status: str) -> None:
    """Record a decision the listener made by hand; `reclassify` never overrides these."""
    song.status = status
    if status in ("approved", "rejected"):
        song.keep = status == "approved"
        reason = song.classify_reason.split("; ", 1)[1] if song.classify_reason.startswith(MANUAL) and "; " in song.classify_reason else song.classify_reason
        song.classify_reason = f"{MANUAL}{status}; {reason}"
