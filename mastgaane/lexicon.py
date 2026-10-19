"""How Whisper mishears what is said to MastGaane, and what was meant (after Dexter's backend/lexicon.py).

Whisper runs in English mode, so Hindi comes out as English-looking words. Live, 17 Oct: the owner's
"Bharti" was written "Marthi." and "Bhaarati", and "Hanuman Chalisa" came out "Leharman Chalisa". Two
corrections live here:

* `heard_name(word)` — a wake word as Whisper mis-spells it, by the shape of how the name sounds
  (b / bh / m / v, "ar", t / th / d, a final i / ee / y), not by edit distance: "party", "bright" and
  "marte" (a lyric: "hum tum pe marte hain") are as close to "bharti" by letters, and are everyday words.
* `correct_wish(words)` — a wish ("leharman chalisa") matched to a title, singer or programme in the
  collection by consonant shape and spelling (what an Indian accent and an English-only model keep);
  None when nothing is close enough — a misheard word must not play the wrong thing.
"""

from __future__ import annotations

import difflib
import re
import time
from functools import lru_cache

# ----------------------------------------------------------------------------- the wake words
#: How each wake word sounds, as Whisper may spell it when it mishears it (the exact spellings are in
#: voice_control.SPELLINGS). Not "party"/"pretty" (p), "bharat" (no final vowel), "marte" (a lyric).
HEARD = {
    "bharti": re.compile(r"^(?:bh|b|m|sm|v)aa?r(?:aa?)?(?:t|th|tt|d|dh)(?:i|ii|ee|y|ie|ea|ey)s?$"),
}


#: Words as close to a wake word by spelling, that are words: "Bharat mata ki jai" is a lyric, "bhakti" a
#: genre ("bhakti sunao"), "aarti" a devotional song.
NOT_NAMES = {"bharat", "bharata", "bhakti", "bhakthi", "aarti", "arti", "aarthi", "party", "marte", "bharte"}

#: Looser, for a second listen without the prompt to a name Whisper was unsure of: there the name only
#: has to be heard again in some form ("party", "smarty", "bharat", "bhakti" — the owner's real call, 17 Oct,
#: came back "Hey Bhakti!"), it does not start anything by itself. Not "part" / "smart": a lyric's "a part
#: of you" was taken for the name (live, 17 Oct) — without "bh" the final vowel must be there.
LOOSE = {
    "bharti": re.compile(r"^s?(?:bh|b|m|p|v)aa?r(?:aa?)?(?:t|th|tt|d|dh)(?:i|ii|ee|y|ie|ea|ey|hi)s?$"
                         r"|^bhaa?r(?:aa?)?th?$|^bhaa?kth?(?:i|ee|y)$"),
}


def heard_name(word: str, wake: str = "bharti") -> bool:
    """Does `word` (letters only, lower case) sound like the wake word, as Whisper mis-spells it?"""
    pattern = HEARD.get(wake)
    return bool(pattern and pattern.match(word))


def sounds_like(word: str, wake: str = "bharti") -> bool:
    """`heard_name`, loosely (see LOOSE)."""
    pattern = LOOSE.get(wake)
    return bool(pattern and pattern.match(word))


# ----------------------------------------------------------------------------- phonetics
_CONSONANT_H = re.compile(r"(?<=[b-df-gj-np-tv-z])h")


def letters(s: str) -> str:
    return re.sub(r"[^a-z]+", "", (s or "").lower())


def normalise(s: str) -> str:
    """Lowercase letters and single spaces ("don't" is one word)."""
    s = re.sub(r"['’]", "", (s or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", s)).strip()


def skeleton(s: str) -> str:
    """Consonant shape of a phrase: "Hanuman Chalisa" → "hnmncls"; aspirates are plain consonants."""
    s = letters(s)
    if not s:
        return ""
    s = s.replace("ph", "f").replace("v", "w").replace("z", "j")
    for asp, plain in (("bh", "b"), ("dh", "d"), ("th", "t"), ("kh", "k"), ("gh", "g"), ("ch", "c")):
        s = s.replace(asp, plain)
    s = re.sub(r"(.)\1+", r"\1", s)
    s = s[:1] + re.sub(r"[aeiou]", "", s[1:])
    s = _CONSONANT_H.sub("", s)
    return re.sub(r"(.)\1+", r"\1", s)


def loose_skeleton(s: str) -> str:
    """The skeleton with nasals and liquids merged (n / l / r / m) and a final vowel + nasal dropped."""
    s = letters(s)
    trimmed = re.sub(r"[aeiou]+[nm][aeiou]*$", "", s)
    sk = skeleton(trimmed if len(trimmed) >= 2 else s)
    if not sk:
        return ""
    sk = sk[:1] + re.sub(r"[nlr]", "m", sk[1:]) if sk[0] not in "nlr" else re.sub(r"[nlr]", "m", sk)
    return re.sub(r"(.)\1+", r"\1", sk)


#: words an upload adds to a song's name ("Abhi Na Jao Chhod Kar Revival", "Darmiyaan - Unplugged")
_UPLOAD_WORDS = re.compile(r"\b(?:from|official|lyrical|lyrics?|video|audio|full|song|hd|remastered|version|revival|"
                           r"unplugged|reprise|jhankar|remix|lofi|slowed|reverb)\b", re.I)


@lru_cache(maxsize=50000)
def work_key(title: str) -> str:
    """One song however an upload spells it: "Hum Tere Shahar Me Aaye Hai" and "Hum Tere Shahar Mein Aaye
    Hain | Ghulam Ali" (both played within an hour, 17 Oct) share a key — the consonant shape of the short
    title without its n's (me / mein, hai / hain). Two songs may share one ("Tum Ho", "Tum Hi Ho"): with
    `same_singer` it tells a repeat or a duplicate upload from another song."""
    short = _short_title(title) or title or ""
    return skeleton(normalise(_UPLOAD_WORDS.sub(" ", short))).replace("n", "")


def singers(artist: str) -> frozenset[str]:
    """The singers of an artist field ("Lata Mangeshkar, Mohammed Rafi" / "A & B" / "A feat. B")."""
    parts = re.split(r",|&|\band\b|\bfeat\.?|\bft\.?|\bwith\b", (artist or "").lower())
    return frozenset(letters(p) for p in parts if len(letters(p)) >= 3)


def same_song(title_a: str, artist_a: str, title_b: str, artist_b: str) -> bool:
    """The same song, another upload: the same work key, and a singer in common (or one has none). A key
    this short ("my" for "Main Yahaan Hoon") stands for many titles: those must read the same."""
    key = work_key(title_a)
    if key != work_key(title_b):
        return False
    if len(key) < 4 and normalise(_UPLOAD_WORDS.sub(" ", _short_title(title_a))) != normalise(
            _UPLOAD_WORDS.sub(" ", _short_title(title_b))):
        return False
    a, b = singers(artist_a), singers(artist_b)
    return not a or not b or bool(a & b)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


@lru_cache(maxsize=50000)
def _shape(s: str) -> tuple[str, str, str]:
    """(spelling, consonant shape, loose shape) of a phrase — the parts `score` compares, worked out once."""
    return normalise(s), skeleton(s), loose_skeleton(s)


def _bound(x: str, y: str) -> float:
    """The most difflib's ratio of two strings of these lengths can be."""
    return 2 * min(len(x), len(y)) / (len(x) + len(y)) if x and y else 0.0


def score(a: str, b: str, floor: float = 0.0) -> float:
    """0.6 × consonant-shape similarity + 0.4 × spelling similarity, or 0.9 × the loose shape's. Below
    `floor` it may answer 0 without comparing (lengths alone rule it out: 5,000 titles, 1–2 s a wish)."""
    (na, sa, la), (nb, sb, lb) = _shape(a), _shape(b)
    if floor and max(0.6 * _bound(sa, sb), 0.9 * 0.6 * _bound(la, lb)) + 0.4 * _bound(na, nb) < floor:
        return 0.0
    spelling = _ratio(na, nb)
    return max(0.6 * _ratio(sa, sb) + 0.4 * spelling, 0.9 * (0.6 * _ratio(la, lb) + 0.4 * spelling))


# ----------------------------------------------------------------------------- wishes
WISH_T = 0.76  # about Dexter's bar for a programme / artist while music is the topic
SHORT_T = 0.86  # a name under 8 letters ("Tum Ho", "Ilahi"): almost anything is that close to it
RARE_SONGS = 2  # a word in this few titles names them ("chalisa" → Shree Hanuman Chalisa)
#: words in many titles that name no song by themselves ("Coke Studio Bharat", "O Meri Laila - Radio")
COMMON = {"songs", "gaane", "gaana", "sunao", "chalao", "lagao", "bajao", "please", "music", "wala", "wale",
          "waale", "radio", "remix", "version", "female", "male", "unplugged", "reprise", "instrumental", "official",
          "video", "audio", "lyrics", "lyrical", "studio", "revival", "live", "cover", "lofi", "slowed", "reverb",
          "mashup", "film", "theme", "bharat", "coke", "mix", "edit", "full", "song", "new", "old"}
_TTL_S = 600.0
_cache: tuple[float, list[str], dict[str, list[str]]] = (0.0, [], {})


_HONORIFIC = re.compile(r"^(?:shree|shri|sri|sree)\s+", re.I)


def _short_title(title: str) -> str:
    """ "Arz Kiya Hai | Coke Studio Bharat" → "Arz Kiya Hai"; "Darmiyaan - Unplugged" → "Darmiyaan"."""
    return re.split(r"\s[-|–]\s|\(|\[", title or "")[0].strip()


def vocabulary() -> tuple[list[str], dict[str, list[str]]]:
    """(names to match a wish against — titles, singers, programmes; word → the titles holding it),
    cached ten minutes."""
    global _cache
    at, names, words = _cache
    if names and time.monotonic() - at < _TTL_S:
        return names, words
    from sqlmodel import select

    from . import archive
    from .db import Song, session

    with session() as s:
        songs = s.exec(select(Song).where(Song.status != "rejected", Song.yt_video_id.is_not(None))).all()
    found: dict[str, None] = {}
    words = {}
    for x in songs:
        short = _short_title(x.title or "")
        if len(letters(short)) >= 4:
            found[short] = None
            plain = _HONORIFIC.sub("", short)  # "Shree Hanuman Chalisa" is asked for as "Hanuman Chalisa"
            if plain != short and len(letters(plain)) >= 4:
                found[plain] = None
            for w in set(normalise(short).split()):
                if len(w) >= 5 and w not in COMMON:
                    words.setdefault(w, []).append(short)
        for a in (x.artist or "").split(","):
            if len(letters(a)) >= 4:
                found[a.strip()] = None
    for p in archive.PROGRAMMES.values():
        for n in (p.name, *p.aliases):
            if n.isascii() and len(letters(n)) >= 4:
                found[n] = None
    _cache = (time.monotonic(), list(found), words)
    return _cache[1], _cache[2]


def forget() -> None:
    """Read the collection again next time (a test, or new songs)."""
    global _cache
    _cache = (0.0, [], {})


#: words a name's window may not start or end on: "the rules" is not "The Rish" (Dexter's rule too)
_EDGE = {"a", "an", "the", "in", "on", "of", "to", "for", "and", "or", "is", "it", "i", "you", "me", "my", "we",
         "he", "she", "they", "this", "that", "there", "what", "so", "but", "at", "by", "be", "do", "no", "not",
         "can", "will", "all", "with", "from", "up", "oh", "hey", "ok", "okay", "yes"}
MAX_WISH_WORDS = 5  # a wish is a few words; a longer run is a lyric or Whisper rambling


def correct_wish(focus: str) -> str | None:
    """The title, singer or programme a misheard wish meant, or None. A window of the wish that leaves
    at most one word out is scored against every name (best wins, at least WISH_T; a short name, which
    anything resembles, at least SHORT_T); failing that, a rare word heard right ("… chalisa") names the
    one or two titles that hold it. Checked against a day of the room's chatter as Whisper wrote it:
    without these limits a third of it "named" a song."""
    words = normalise(focus).split()
    if not words or len(words) > MAX_WISH_WORDS or len(letters(focus)) < 5:
        return None
    names, by_word = vocabulary()
    best, best_name = 0.0, None
    for size in range(len(words), max(1, len(words) - 1) - 1, -1):
        for i in range(len(words) - size + 1):
            part = words[i:i + size]
            if part[0] in _EDGE or part[-1] in _EDGE or len(letters("".join(part))) < 5:
                continue
            window = " ".join(part)
            for name in names:
                if abs(len(name.split()) - size) > 1:
                    continue
                # a short name, a window shorter than the name ("present" → "Paras Nath"), or one that leaves a
                # word of the wish out: anything is close — with 5,000 titles "chalisa" alone found the
                # singer "Caralisa" (0.83) before "leharman chalisa" found Hanuman Chalisa (0.78)
                full = len(letters(name)) >= 8 and size >= len(name.split()) and size == len(words)
                bar = WISH_T if full else SHORT_T
                sc = score(window, name, floor=bar)
                # more of what was said explains it better: "pal pal dil ke paas" is that song, not "Pal Pal"
                rank = sc + 0.1 * size / len(words)
                if sc >= bar and rank > best:
                    best, best_name = rank, name
    if best_name:
        return best_name
    for w in words:
        titles = by_word.get(w, [])
        if len(w) >= 6 and 0 < len(titles) <= RARE_SONGS:
            return titles[0]
    return None
