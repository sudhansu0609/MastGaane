"""Tier 3: transcribe the lyric snippet (faster-whisper) and let Claude guess the song."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import settings
from ..db import Reel
from .common import SongCandidate, clean_title
from .llm import LLMUnavailable, complete_json


class SongGuess(BaseModel):
    title: str = Field(description="Best-guess song title in its common Latin-script spelling; empty string if unknown")
    artist: str = Field(description="Best-guess singer/artist; empty string if unknown")
    confidence: float = Field(ge=0, le=1, description="0-1 probability that title+artist is correct")
    reasoning: str = Field(description="One sentence on what the guess was based on")


@lru_cache(maxsize=1)
def _whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        return WhisperModel(settings.whisper_model, device="auto", compute_type="int8")
    except Exception:
        return None


def transcribe(path: Path) -> str:
    model = _whisper()
    if model is None:
        return ""
    try:
        segments, _info = model.transcribe(str(path), beam_size=3, vad_filter=True)
        return " ".join(s.text.strip() for s in segments)[:1500]
    except Exception:
        return ""


SYSTEM = (
    "You identify songs from Instagram Reels, mostly Hindi film (Bollywood) songs, ghazals and Hindi/Urdu folk; "
    "identify songs in other languages accurately too (they are filtered later). You are given the reel caption, hashtags, the account that posted it, any audio label Instagram "
    "showed, and a rough speech-to-text transcript of ~25 seconds of the audio (it may be garbled or in the "
    "wrong script). Use lyric fragments, names, and context to identify the actual song and original artist. "
    "If it is clearly not a song (talking, comedy, ambient), return empty title with confidence 0."
)


def guess_song(reel: Reel, lyrics: str) -> SongCandidate | None:
    user = (
        f"Account: @{reel.owner}\n"
        f"Instagram audio label: {reel.ig_song_title or '(none)'} — {reel.ig_artist or ''}\n"
        f"Caption: {reel.caption[:1200] or '(none)'}\n"
        f"Transcript: {lyrics or '(none)'}\n"
    )
    try:
        g = complete_json(SYSTEM, user, SongGuess)
    except LLMUnavailable:
        return None
    if not g.title.strip():
        return None
    return SongCandidate(title=clean_title(g.title), artist=g.artist.strip(), confidence=min(g.confidence, 0.7), tier=3, note=g.reasoning)
