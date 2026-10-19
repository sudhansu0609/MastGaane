"""Moods and times of day: "something sad", "romantic", "morning songs", "late night".

The word lists are a contract shared with Dexter's router (dexter/docs/V15_PLAN.md,
"Cross-WP contract"): keep MOODS and TIMES identical on both sides.

A song's mood is stored as `Song.mood`: one or more moods joined by "|" ("sad|romantic"), or
"any" when nothing hints at one. Until the nightly LLM classifier fills it, `guess_mood()`
reads the genre, the title / lyrics words and the year.
"""
from __future__ import annotations

import re

# canonical mood → the words that ask for it (the first word is the canonical name)
MOODS: dict[str, tuple[str, ...]] = {
    "sad": ("sad", "udaas", "gham", "dard", "dukhi", "judaai", "melancholic"),
    "romantic": ("romantic", "pyaar", "ishq", "mohabbat", "love"),
    "energetic": ("energetic", "upbeat", "josh", "masti", "dance", "party", "peppy"),
    "peaceful": ("peaceful", "calm", "sukoon", "shaant", "soft", "relaxing"),
    "happy": ("happy", "khush", "cheerful"),
    "devotional": ("devotional", "bhakti"),
    "nostalgic": ("nostalgic", "yaadein"),
}

TIMES: dict[str, tuple[str, ...]] = {
    "morning": ("morning", "subah"),
    "afternoon": ("afternoon", "dopahar"),
    "evening": ("evening", "shaam"),
    "night": ("night", "raat", "late night", "midnight"),
}

ANY = "any"

# a time of day with no programme on air plays these moods (first one names the wish)
TIME_MOODS: dict[str, tuple[str, ...]] = {
    "morning": ("devotional", "peaceful"),
    "afternoon": ("happy", "energetic", "nostalgic"),  # Bhoole Bisre Geet hours: old favourites too
    "evening": ("romantic",),
    "night": ("sad", "peaceful"),
}

# the hours each time of day covers [start, end) and the hour that stands for it
TIME_HOURS: dict[str, tuple[int, int, int]] = {
    "morning": (5, 12, 7),
    "afternoon": (12, 17, 14),
    "evening": (17, 21, 19),
    "night": (21, 29, 22),  # 21:00 → 05:00 the next day
}

# title / lyrics words that hint at a mood (prefix match on word starts)
_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("sad", ("dard", "judaai", "tanhai", "yaad", "bichhad")),
    ("romantic", ("pyaar", "ishq", "dil", "mohabbat", "sanam")),
    ("energetic", ("dhoom", "nach", "masti", "dance", "jhoom", "party")),
    ("peaceful", ("sukoon", "raat", "chandni")),
]
_HINT_RE = [(m, re.compile(r"\b(?:" + "|".join(map(re.escape, ws)) + r")", re.I)) for m, ws in _HINTS]
_GENRE_MOODS = {"ghazal": ("sad", "romantic"), "devotional": ("devotional",), "sufi": ("energetic",)}
NOSTALGIC_BEFORE = 1975


def _phrase_in(phrase: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def mood_of_words(text: str) -> str | None:
    """The canonical mood a request names ("something sad" → "sad"), or None."""
    t = (text or "").lower()
    for mood, words in MOODS.items():
        if any(_phrase_in(w, t) for w in words):
            return mood
    return None


def time_of_words(text: str) -> str | None:
    """The canonical time of day a request names ("late night songs" → "night"), or None."""
    t = (text or "").lower()
    for name, words in TIMES.items():
        if any(_phrase_in(w, t) for w in words):
            return name
    return None


def guess_mood(song) -> str:
    """Heuristic mood of a song: "sad|romantic", "energetic", … or "any". Needs title/artist/genre/year
    (and lyrics_snippet when there is one)."""
    found: list[str] = []

    def add(*ms: str) -> None:
        for m in ms:
            if m not in found:
                found.append(m)

    add(*_GENRE_MOODS.get((getattr(song, "genre", "") or "").lower(), ()))
    blob = f"{getattr(song, 'title', '') or ''} {getattr(song, 'lyrics_snippet', '') or ''}"
    for mood, rx in _HINT_RE:
        if rx.search(blob):
            add(mood)
    year = getattr(song, "year", None)
    if year and year < NOSTALGIC_BEFORE:
        add("nostalgic")
    return "|".join(found) or ANY


def moods_of(value: str | None) -> set[str]:
    """The set of moods in a stored "sad|romantic" value (empty for "" / "any")."""
    return {m for m in (value or "").split("|") if m and m != ANY}


def song_moods(song) -> set[str]:
    """Stored mood if the song has one, else the heuristic guess."""
    stored = getattr(song, "mood", "") or ""
    return moods_of(stored) if stored else moods_of(guess_mood(song))


def _in_window(name: str, hour: int) -> bool:
    start, end, _ = TIME_HOURS[name]
    h = hour if hour >= start else hour + 24
    return start <= h < end


def time_to_wish(t: str, hour: int | None = None) -> tuple[str, str]:
    """What a time of day asks for: ("programme", slug) when the radio day has a programme in that
    window (the one on air at `hour` if `hour` falls inside it), else ("mood", m)."""
    from datetime import datetime

    from .jockey import slot

    if t not in TIME_HOURS:
        return ("mood", ANY)
    rep = TIME_HOURS[t][2]
    at = hour if hour is not None and _in_window(t, hour) else rep
    today = datetime.now().replace(minute=0, second=0, microsecond=0)
    for h in (at, rep):
        prog = slot(today.replace(hour=h % 24))
        if prog:
            return ("programme", prog)
    return ("mood", TIME_MOODS[t][0])


def time_moods(t: str) -> tuple[str, ...]:
    """The moods a time of day plays when no programme fills it."""
    return TIME_MOODS.get(t, ())


def time_of_hour(hour: int) -> str:
    """The part of the day an hour falls in (morning 5–12, afternoon 12–17, evening 17–21, night 21–5)."""
    return next((t for t in TIME_HOURS if _in_window(t, hour)), "night")
