"""Tier 2: audio fingerprinting (Shazam via shazamio; AudD as optional fallback)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..config import settings
from .common import SongCandidate, clean_title


async def _shazam_raw(path: Path) -> dict | None:
    """Shazam's whole track record (title, subtitle, images, sections, hub …), or None on a miss.
    shazamio needs `audioop`, which left the standard library in Python 3.13 (audioop-lts brings it back)."""
    try:
        from shazamio import Shazam
    except ImportError:
        return None
    try:
        out = await Shazam().recognize(str(path))
    except Exception:
        return None
    track = (out or {}).get("track") or {}
    return track if track.get("title") else None


def shazam_track(path: Path) -> dict | None:
    return asyncio.run(_shazam_raw(path))


def audd_result(path: Path) -> dict | None:
    if not settings.audd_api_key:
        return None
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://api.audd.io/", data={"api_token": settings.audd_api_key, "return": "spotify,apple_music"}, files={"file": f}, timeout=60)
        res = r.json().get("result") or {}
    except Exception:
        return None
    return res if res.get("title") else None


def fingerprint(path: Path) -> SongCandidate | None:
    track = shazam_track(path)
    if track:
        return SongCandidate(title=clean_title(track["title"]), artist=track.get("subtitle", ""), confidence=0.75, tier=2, note="shazam")
    res = audd_result(path)
    if res:
        return SongCandidate(title=clean_title(res["title"]), artist=res.get("artist", ""), confidence=0.75, tier=2, note="audd")
    return None
