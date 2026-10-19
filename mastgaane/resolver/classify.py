"""Decide whether a song belongs in the late-night playlist.

Two gates, both configurable in rules.yaml:
  language  — Hindi/Urdu plus the Hindi-belt folk languages (Bhojpuri, Awadhi, Maithili, ...) by default
  genre     — Hindi film songs, ghazals and folk by default
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
Language = Literal[
    "hindi", "urdu", "bhojpuri", "awadhi", "maithili", "magahi", "braj", "rajasthani", "haryanvi", "garhwali", "kumaoni",
    "punjabi", "bengali", "marathi", "gujarati", "tamil", "telugu", "english", "other",
]
Mood = Literal["sad", "romantic", "energetic", "peaceful", "happy", "devotional", "nostalgic", "any"]  # moods.MOODS keys
GENRES = Genre.__args__  # type: ignore[attr-defined]
LANGUAGES = Language.__args__  # type: ignore[attr-defined]
HINDUSTANI = {"hindi", "urdu"}
BIHAR_UP = ("bhojpuri", "awadhi", "maithili", "magahi")


class Verdict(BaseModel):
    keep: bool = Field(description="True if this is a real SUNG song with melody (not talk, comedy, hype/party/EDM). Do not judge by the caption.")
    language: Language = Field(description="language the song is sung in; Hindi film songs and ghazals are 'hindi' or 'urdu'; Bhojpuri/Awadhi/Maithili folk get their own label")
    genre: Genre = Field(description="film = Bollywood/Hindi film song; ghazal; folk = lokgeet incl. Chhath/kajri/chaita/sohar/birha/nirgun; sufi/qawwali; classical; indie = non-film pop; devotional = bhajan/naat")
    reason: str = Field(description="short reason")
    mood: Mood = Field(default="any", description="the song's main mood: sad (dard, judaai), romantic, energetic (dance, party), peaceful (sukoon), happy, devotional, nostalgic; 'any' if unclear")


DEFAULT_RULES = {
    "allowed_languages": ["hindi", "urdu", *BIHAR_UP],
    "allowed_genres": ["film", "ghazal", "folk"],
    "always_keep_artists": [
        "Jagjit Singh", "Ghulam Ali", "Mehdi Hassan", "Begum Akhtar", "Farida Khanum", "Abida Parveen",
        "Nusrat Fateh Ali Khan", "Rahat Fateh Ali Khan", "Pankaj Udhas", "Talat Aziz", "Hariharan",
        "Arijit Singh", "Atif Aslam", "Papon", "Mohit Chauhan", "Lata Mangeshkar", "Kishore Kumar",
        "Mohammed Rafi", "Mukesh", "Asha Bhosle", "Shreya Ghoshal", "Sonu Nigam", "KK", "Udit Narayan",
    ],
    # always kept; genre ghazal
    "ghazal_artists": [
        "Jagjit Singh", "Chitra Singh", "Ghulam Ali", "Mehdi Hassan", "Begum Akhtar", "Farida Khanum",
        "Iqbal Bano", "Pankaj Udhas", "Talat Aziz", "Talat Mahmood", "Bhupinder Singh", "Anup Jalota",
        "Chandan Dass", "Ahmed Hussain", "Penaz Masani", "Rekha Bhardwaj",
    ],
    # always kept; genre folk, language bhojpuri unless a mapping says otherwise
    "folk_artists": [
        "Sharda Sinha", "Bharat Sharma Vyas", "Malini Awasthi", "Chandan Tiwari", "Kalpana Patowary",
        "Mohammad Khalil", "Madan Rai", "Bharat Singh Bharti", "Vindhyavasini Devi", "Maithili Thakur",
    ],
    # a folk form named in the title/caption makes it folk (Bihar/UP lokgeet)
    "folk_keywords": [
        "chhath", "chhat geet", "kajri", "kajari", "chaita", "chaiti", "sohar", "birha", "purbi", "purvi",
        "nirgun", "lokgeet", "lok geet", "vivah geet", "jhumar", "faguwa", "bidesiya", "bhojpuri lok",
    ],
    # searches that seed discovery (see discover.py) — steer it toward 1990s–2000s ghazals and folk
    "seed_queries": [
        "Jagjit Singh 90s ghazal", "Pankaj Udhas ghazal 1990s", "Hariharan ghazal album", "Talat Aziz ghazal",
        "Jagjit Chitra Singh ghazal", "2000s Hindi ghazal", "Hindi ghazal unplugged",
        "Sharda Sinha Chhath geet", "Bharat Sharma Vyas birha", "Bhojpuri lokgeet purane", "Chandan Tiwari lokgeet",
        "Malini Awasthi kajri", "Bhojpuri nirgun bhajan", "Kalpana Patowary Bhojpuri folk",
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
    for a in r["folk_artists"]:
        if a.lower() in art:
            return Verdict(keep=True, language="bhojpuri", genre="folk", reason=f"folk artist {a}")
    for a in r["ghazal_artists"]:
        if a.lower() in art:
            return Verdict(keep=True, language="urdu" if URDU.search(blob) else "hindi", genre="ghazal", reason=f"ghazal artist {a}")
    for a in r["always_keep_artists"]:
        if a.lower() in art:
            genre = "ghazal" if "ghazal" in blob else "film"
            return Verdict(keep=True, language="hindi", genre=genre, reason=f"always-keep artist {a}")
    for k in r["folk_keywords"]:
        if re.search(rf"\b{re.escape(k)}", blob):
            return Verdict(keep=True, language="bhojpuri", genre="folk", reason=f"folk form '{k}'")
    return None


SYSTEM = (
    "You label songs for a listener who loves Hindi/Urdu ghazals, old and melodious Hindi film songs, and "
    "Bhojpuri / Awadhi / Maithili folk (Chhath geet, kajri, chaita, sohar, birha, nirgun). "
    "Label the language the song is SUNG in and its genre, using what you know about the title and artist. "
    "The reel caption is written by a random uploader and is usually unrelated: never set keep=false because "
    "the caption is irrelevant, and never reject a song for not being a ghazal. "
    "keep=false ONLY when the audio is not a real sung song (comedy/meme, dialogue, speech, podcast) or is "
    "hype/party/EDM/phonk/rap or a vulgar/item party number. Modern Bhojpuri party songs with double-meaning "
    "lyrics (the 'Lollipop Lagelu' kind) are NOT folk: keep=false. "
    "Be strict about language: an English, Punjabi, Tamil or Korean song is not 'hindi' even if the reel is Indian; "
    "a Bhojpuri song is 'bhojpuri', not 'hindi'."
)


def classify(title: str, artist: str, caption: str = "", lyrics: str = "") -> Verdict:
    rv = _rule_verdict(title, artist, caption)
    if rv is not None:
        return gate(rv)
    try:
        v = gate(complete_json(SYSTEM, f"Song: {title}\nArtist: {artist}\nReel caption: {caption[:600]}\nLyrics snippet: {lyrics[:400]}", Verdict))
        # the LLM uses the regional labels as a catch-all for any "folk" (it called Telugu, Punjabi and
        # French songs Bhojpuri/Magahi) — without a rule backing it, the listener decides
        if v.keep and v.language in BIHAR_UP:
            v.reason = f"{CHECK}{v.language} only per LLM; {v.reason}"
        return v
    except LLMUnavailable as e:  # no local model and no API key — fall back to heuristics
        reason = f"no LLM ({str(e)[:60]})"
    blob = f"{title} {artist} {caption}".lower()
    lang = guess_language(title, artist, caption, lyrics)
    keep = any(k in blob for k in rules()["keep_keywords"]) or lang is not None
    genre = "ghazal" if "ghazal" in blob else ("folk" if "folk" in blob or "lok" in blob else "film")
    return gate(Verdict(keep=keep, language=lang or "other", genre=genre, reason=f"{CHECK if keep else ''}{reason}; heuristic"))


CHECK = "[check] "


def needs_review(v: Verdict) -> bool:
    """Kept, but on evidence too thin to auto-approve: goes to Discover for a 👍 / 👎."""
    return v.keep and CHECK in v.reason
