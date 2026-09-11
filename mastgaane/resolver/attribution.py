"""Tier 1: Instagram's own music attribution (licensed tracks)."""
from __future__ import annotations

from ..db import Reel
from .common import SongCandidate, clean_title

GENERIC = {"original audio", "original sound", "audio original", ""}


def from_attribution(reel: Reel) -> SongCandidate | None:
    if not reel.ig_song_title or reel.is_original_audio:
        return None
    title = clean_title(reel.ig_song_title)
    if title.lower() in GENERIC:
        return None
    return SongCandidate(title=title, artist=(reel.ig_artist or "").strip(), confidence=0.9, tier=1, note="instagram attribution")
