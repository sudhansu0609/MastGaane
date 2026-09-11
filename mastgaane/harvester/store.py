from __future__ import annotations

import json

from sqlmodel import Session, select

from ..db import Reel, utcnow
from .parse import ReelItem


def upsert_reel(s: Session, item: ReelItem, run_id: int | None, watched: bool = False) -> tuple[Reel, bool]:
    """Insert or update a reel row. Returns (reel, created)."""
    reel = s.exec(select(Reel).where(Reel.shortcode == item.shortcode)).first()
    created = reel is None
    if reel is None:
        reel = Reel(
            shortcode=item.shortcode,
            pk=item.pk,
            owner=item.owner,
            caption=item.caption[:4000],
            taken_at=item.taken_at,
            run_id=run_id,
            ig_song_title=item.song_title,
            ig_artist=item.artist,
            is_original_audio=item.is_original_audio,
            audio_id=item.audio_id,
            video_url=item.video_url,
            thumb_url=item.thumb_url,
            watched=watched,
            raw_json=json.dumps(item.raw, ensure_ascii=False)[:200_000],
        )
        s.add(reel)
    else:
        if watched and not reel.watched:
            reel.watched = True
        elif watched:
            reel.times_seen += 1
        reel.seen_at = utcnow()
        # fill in anything we did not have before
        if not reel.ig_song_title and item.song_title:
            reel.ig_song_title, reel.ig_artist = item.song_title, item.artist
            reel.is_original_audio, reel.audio_id = item.is_original_audio, item.audio_id
        if not reel.video_url and item.video_url:
            reel.video_url = item.video_url
        if not reel.thumb_url and item.thumb_url:
            reel.thumb_url = item.thumb_url
        if not reel.owner and item.owner:
            reel.owner = item.owner
        if not reel.caption and item.caption:
            reel.caption = item.caption[:4000]
    s.commit()
    s.refresh(reel)
    return reel, created


def mark_watched(s: Session, shortcode: str) -> None:
    reel = s.exec(select(Reel).where(Reel.shortcode == shortcode)).first()
    if reel is None:
        return
    if reel.watched:
        reel.times_seen += 1
    reel.watched = True
    reel.seen_at = utcnow()
    s.commit()
