"""Tier 2: audio fingerprinting (Shazam via shazamio; AudD as optional fallback)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..config import settings
from .common import SongCandidate, clean_title


async def _shazam(path: Path) -> SongCandidate | None:
    try:
        from shazamio import Shazam
    except ImportError:
        return None
    try:
        out = await Shazam().recognize(str(path))
    except Exception:
        return None
    track = (out or {}).get("track") or {}
    if not track.get("title"):
        return None
    return SongCandidate(title=clean_title(track["title"]), artist=track.get("subtitle", ""), confidence=0.75, tier=2, note="shazam")


def _audd(path: Path) -> SongCandidate | None:
    if not settings.audd_api_key:
        return None
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://api.audd.io/", data={"api_token": settings.audd_api_key, "return": "spotify,apple_music"}, files={"file": f}, timeout=60)
        res = r.json().get("result") or {}
    except Exception:
        return None
    if not res.get("title"):
        return None
    return SongCandidate(title=clean_title(res["title"]), artist=res.get("artist", ""), confidence=0.75, tier=2, note="audd")


def fingerprint(path: Path) -> SongCandidate | None:
    cand = asyncio.run(_shazam(path))
    if cand is None:
        cand = _audd(path)
    return cand
