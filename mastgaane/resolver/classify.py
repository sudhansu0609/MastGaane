"""Decide whether a song belongs in the late-night playlist.

Two gates, both configurable in rules.yaml:
  language  — only Hindi/Urdu (Hindustani) by default; English, Punjabi, Tamil, ... are rejected
  genre     — only Hindi film songs, ghazals and folk by default
The LLM (local Ollama or Claude) labels language + genre; the gate is applied in code so the
result is the same whichever backend answered.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..config import settings
from .llm import LLMUnavailable, complete_json

Genre = Literal["film", "ghazal", "folk", "sufi", "classical", "indie", "devotional", "other"]
Language = Literal["hindi", "urdu", "punjabi", "bengali", "marathi", "tamil", "telugu", "english", "other"]
GENRES = Genre.__args__  # type: ignore[attr-defined]
LANGUAGES = Language.__args__  # type: ignore[attr-defined]
HINDUSTANI = {"hindi", "urdu"}


class Verdict(BaseModel):
    keep: bool = Field(description="True only if this is a melodious / slow / soulful SONG (not talk, comedy, hype music)")
    language: Language = Field(description="language the song is sung in; Hindi film songs and ghazals are 'hindi' or 'urdu'")
    genre: Genre = Field(description="film = Bollywood/Hindi film song; ghazal; folk = regional/folk; sufi/qawwali; classical; indie = non-film pop; devotional = bhajan/naat")
    reason: str = Field(description="short reason")


DEFAULT_RULES = {
    "allowed_languages": ["hindi", "urdu"],
    "allowed_genres": ["film", "ghazal", "folk"],
    "always_keep_artists": [
        "Jagjit Singh", "Ghulam Ali", "Mehdi Hassan", "Begum Akhtar", "Farida Khanum", "Abida Parveen",
        "Nusrat Fateh Ali Khan", "Rahat Fateh Ali Khan", "Pankaj Udhas", "Talat Aziz", "Hariharan",
        "Arijit Singh", "Atif Aslam", "Papon", "Mohit Chauhan", "Lata Mangeshkar", "Kishore Kumar",
        "Mohammed Rafi", "Mukesh", "Asha Bhosle", "Shreya Ghoshal", "Sonu Nigam", "KK", "Udit Narayan",
    ],
    "always_skip_patterns": ["meme", "comedy", "funny", "dialogue", "prank", "roast", "podcast", "motivation", "gym", "phonk", "dj ", "remix"],
    "keep_keywords": ["ghazal", "sufi", "qawwali", "unplugged", "nazm", "shayari", "soulful", "melod", "bollywood", "lofi"],
}

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
URDU = re.compile(r"[؀-ۿ]")
# Romanised-Hindi function words that almost never appear in English captions/titles
HINDI_WORDS = re.compile(r"\b(tum|tera|teri|tere|mera|meri|mere|dil|pyaar|pyar|ishq|mohabbat|zindagi|jaane|rahe|raat|chand|sapna|sapne|kyun|kya|nahi|nahin|hai|hain|ho|jo|ke|ki|ka|se|mein|hum|ham|yaad|yaar|saath|judaai|khuda|rab|sanam|jaana|jaan|aankh|aankhon|kabhi|phir)\b", re.I)


@lru_cache(maxsize=1)
def rules() -> dict:
    if settings.rules_file.exists():
        try:
            data = yaml.safe_load(settings.rules_file.read_text(encoding="utf-8")) or {}
            return {**DEFAULT_RULES, **data}
        except Exception:
            pass
    return DEFAULT_RULES


def guess_language(*texts: str) -> Language | None:
    """Cheap script/keyword language guess used when no LLM is available. None = unknown."""
    blob = " ".join(t for t in texts if t)
    if DEVANAGARI.search(blob):
        return "hindi"
    if URDU.search(blob):
        return "urdu"
    if len(HINDI_WORDS.findall(blob)) >= 2:
        return "hindi"
    return None


def gate(v: Verdict) -> Verdict:
    """Apply the language + genre allow-lists to an LLM verdict."""
    r = rules()
    langs = {x.lower() for x in r["allowed_languages"]}
    genres = {x.lower() for x in r["allowed_genres"]}
    if v.keep and v.language not in langs:
        v.keep, v.reason = False, f"language {v.language} not in {sorted(langs)}; {v.reason}"
    elif v.keep and v.genre not in genres:
        v.keep, v.reason = False, f"genre {v.genre} not in {sorted(genres)}; {v.reason}"
    return v


def _rule_verdict(title: str, artist: str, caption: str) -> Verdict | None:
    r = rules()
    blob = f"{title} {artist} {caption}".lower()
    art = artist.lower()
    for p in r["always_skip_patterns"]:
        if re.search(rf"\b{re.escape(p.strip())}", f"{title} {artist}".lower()):
            return Verdict(keep=False, language="other", genre="other", reason=f"skip pattern '{p}'")
    for a in r["always_keep_artists"]:
        if a.lower() in art:
            genre = "ghazal" if "ghazal" in blob or a in ("Jagjit Singh", "Ghulam Ali", "Mehdi Hassan", "Begum Akhtar", "Farida Khanum", "Pankaj Udhas", "Talat Aziz") else "film"
            return Verdict(keep=True, language="hindi", genre=genre, reason=f"always-keep artist {a}")
    return None


SYSTEM = (
    "You curate a late-night listening playlist of melodious HINDI/URDU songs: Hindi film (Bollywood) songs, "
    "ghazals, and Hindi/Hindustani folk songs. Slow, melodic, soulful. Label the language the song is SUNG in "
    "(not the caption language — captions are often English) and the genre. Reject comedy/meme audio, dialogues, "
    "hype/party/EDM/phonk/rap, aggressive remixes, motivational speeches, and anything that is not a song. "
    "Be strict about language: an English, Punjabi, Tamil or Korean song is not 'hindi' even if the reel is Indian."
)


def classify(title: str, artist: str, caption: str = "", lyrics: str = "") -> Verdict:
    rv = _rule_verdict(title, artist, caption)
    if rv is not None:
        return gate(rv)
    try:
        v = complete_json(SYSTEM, f"Song: {title}\nArtist: {artist}\nReel caption: {caption[:600]}\nLyrics snippet: {lyrics[:400]}", Verdict)
        return gate(v)
    except LLMUnavailable as e:  # no local model and no API key — fall back to heuristics
        reason = f"no LLM ({str(e)[:60]})"
    blob = f"{title} {artist} {caption}".lower()
    lang = guess_language(title, artist, caption, lyrics)
    keep = any(k in blob for k in rules()["keep_keywords"]) or lang is not None
    genre = "ghazal" if "ghazal" in blob else ("folk" if "folk" in blob or "lok" in blob else "film")
    return gate(Verdict(keep=keep, language=lang or "other", genre=genre, reason=f"{reason}; heuristic"))
