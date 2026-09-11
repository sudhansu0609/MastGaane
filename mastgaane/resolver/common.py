from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..config import settings

_PAREN = re.compile(r"[\(\[].*?[\)\]]")
_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring|from|official|video|audio|lyrical|lyrics|full song|remix|slowed|reverb|version)\b.*", re.I)
_NONWORD = re.compile(r"[^a-z0-9ऀ-ॿ؀-ۿ]+")


def clean_title(title: str) -> str:
    t = _PAREN.sub(" ", title or "")
    t = _FEAT.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|")
    return t or (title or "").strip()


def norm_key(title: str, artist: str | None) -> str:
    t = _NONWORD.sub("", clean_title(title).lower())
    a = _NONWORD.sub("", (artist or "").split(",")[0].split("&")[0].lower())
    return f"{t}|{a}"


@dataclass
class SongCandidate:
    title: str
    artist: str
    confidence: float
    tier: int
    note: str = ""


@lru_cache(maxsize=1)
def claude_client():
    import anthropic

    if settings.anthropic_api_key:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return anthropic.Anthropic()  # env / `ant auth login` profile


def claude_available() -> bool:
    try:
        claude_client()
        return True
    except Exception:
        return False
