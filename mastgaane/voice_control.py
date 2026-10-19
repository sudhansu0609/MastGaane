"""Talk to MastGaane: "Bharti" … *ding* … "agla gaana" — no Dexter needed.

The name first (VOICE_WAKE_WORDS: Bharti, Radio, FM), then the command:
  - the name alone → a chime, the music dips, and the next ~6 s are recorded as the command — with
    the music down it is heard clean;
  - the name and the command in one breath ("Bharti, next song") → done straight away.

How it hears. A thread reads the microphone (ffmpeg DirectShow, the mics 🎤 Listen uses — here the
Brio webcam). In a quiet room (or on headphones) a loudness segmenter cuts out sentences; with the
music on speakers the mic never goes quiet, so it is read in 3 s windows every 1.5 s. Whisper's
fast model (`base`, primed with just the names so it spells them) looks for a name anywhere in a
window and at the start of a sentence; a wish to play also goes through the full model (`small`)
for the names in it.

Mishearings (lexicon.py, after Dexter's): live, Whisper wrote the owner's "Bharti" as "Marthi." and
"Bhaarati", and "Hanuman Chalisa" as "Leharman Chalisa". The name is also matched by how it sounds
(b / bh / m / v, "ar", t / th / d, a final i / y — not "party", "bharat" or the lyric "marte"), and a
wish no title matched exactly is matched to the collection's titles, singers and programmes by
consonant shape.

What it will not do (each one caught live):
  - obey Whisper's echo of its own prompt — fed only music it writes "Bharti." back: a lone name
    Whisper was unsure of (two or more names, a high no-speech score, no voice in it) is heard again
    without the prompt, and counts only if the name is there again in some form. Dropping such names
    outright threw away nearly every real call (live, 17 Oct);
  - dip or listen because something got loud (on speakers that was every song and every intro);
  - take the host's words for yours: her links avoid the wake words, and while she talks (and a few
    seconds after) only stop / pause / next are obeyed and nothing opens the listening window;
  - do one command twice: the same action within a few seconds (Dexter hands over what he hears
    starting with a name, and overlapping windows) is done once.

"Dexter, …" dips the music too (the first ~1.2 s of a sentence are checked), so Dexter hears the
rest clean; Dexter ignores sentences that start with our names and hands them over (POST /api/voice/heard).

    next / skip / agla gaana / change      pause / ruko        resume / chalao      stop / band karo
    I like this / badhiya / dislike / hatao             what's playing / kaun sa gaana hai
    play ghazals · gajal sunao · Hawa Mahal lagao · Jagjit Singh ke gaane · something sad · purane gaane
"""
from __future__ import annotations

import difflib
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from rich.console import Console

from . import lexicon
from .config import settings

console = Console()
RATE = 16000
FRAME = 480  # 30 ms
DIP_MAX_S = 12.0  # the music comes back up after this even if nothing was understood
WINDOW_S, HOP_S = 3.0, 1.5  # music in the room is read in 3 s windows every 1.5 s
WHILE_HOST_TALKS = {"stop", "pause", "next", "mute", "unmute"}  # obeyed even while the host speaks; other wishes wait
QUICK = {"next", "stop", "pause", "resume", "like", "dislike", "now", "mute", "unmute"}  # done on the fast model's word
HOST_TAIL_S = 4.5  # the host's words are still in the mic's windows this long after she stops
ARM_S = 6.0  # after the name alone: how long the command may take to begin
CAPTURE_MAX_S = 7.0  # the longest command recorded
CHIME_GUARD_S = 0.45  # the chime (through the speakers) is not you speaking
PREFILL_S = 0.4  # a command that began before the listening window opened: keep this much before it
PROMPT_REPLY_S = 3.0  # nothing more said this long after the chime: the command came with the name (2 s
# was shorter than hearing the chime and starting to speak: a live capture ended before the command)
DEDUP_S = 5.0  # the same command again this soon was heard twice, not said twice
# a lone primed name this likely "no speech" may be Whisper's echo, and is heard again unprimed. Measured
# (8 of the owner's songs, 90 s each, at the Brio's −38 dBFS): 205 lone-name echoes, none below 0.47;
# names said as loud as the music: 0.26–0.58, most under 0.45. At 0.3, most real calls were dropped.
ECHO_NSP = 0.44
# Over the music, a name that comes with no command is always heard again unprimed first: the Brio's
# numbers above did not hold on the Yeti (its echoes scored 0.17–0.53: 26 listening windows in 47 min,
# nobody calling), and Hawa Mahal's dialogue came back "Bharti." at 0.01–0.10, as sure as a real call.
HEARD_SCORE = 0.7  # _name_score of a name as Whisper mis-spells it ("Marthi") — lexicon.HEARD
DEBUG_CLIPS = 120  # VOICE_DEBUG keeps this many recent clips (a name or a command in them) in data/voice_debug/
_NOWINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ----------------------------------------------------------------------------- what was said
# the ways Whisper writes each wake word (VOICE_WAKE_WORDS picks which are on)
SPELLINGS = {
    "bharti": ("bharti", "bharati", "bharthi", "bhaarti", "bharty", "barti", "भारती"),
    "radio": ("radio", "radyo", "rediyo", "रेडियो"),
    "fm": ("fm", "efem", "efam", "effem", "एफएम", "एफ़एम"),
    "mastgaane": ("mastgaane", "mastgane", "mastgana", "mastgaana", "mustgaane", "mustgane", "mustgana", "mustgo",
                  "मस्तगाने", "मस्तगान"),
}
_LEAD = {"hey", "hi", "ok", "okay", "oye", "oy", "arre", "are", "o", "oh", "a", "um", "uh", "so", "suno", "listen", "hello"}


def wake_words() -> list[str]:
    return [w.strip().lower() for w in settings.voice_wake_words.split(",") if w.strip()]


def _forms() -> list[str]:
    return [f for w in wake_words() for f in SPELLINGS.get(_norm(w), (_norm(w),))]


def _norm(s: str) -> str:
    """Letters, marks and digits only (keeps Devanagari vowel signs, drops spaces and punctuation)."""
    return "".join(ch for ch in s.lower() if unicodedata.category(ch)[0] in "LMN")


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.!?।…:;\"“”()\-]+", text.lower()) if t]


def _name_score(words: list[str]) -> float:
    """How well these words spell a wake word as Whisper may write it ("Bharati", "must gaane"); 0 when
    not close enough — short words need a closer match, so "party" is not "Bharti". A spelling that
    only sounds like the name ("Marthi", "bar tea") scores HEARD_SCORE. Never a possessive: live, a video's
    "How do you feel about Bharti's studio?" played Coke Studio — nobody calls "Bharti's, …"."""
    if words and re.search(r"['’]s$", words[-1]):
        return 0.0
    w = _norm("".join(words))
    if w in _forms():  # exact, short ones too ("FM", "F. M.")
        return 1.0
    if len(w) < 4 or w in lexicon.NOT_NAMES:
        return 0.0
    best = 0.0
    for f in _forms():
        if len(f) < 4:
            continue  # "fm" only ever matches exactly
        if len(f) < 8 and abs(len(w) - len(f)) > 1:  # "hari" is not "bharti"
            continue
        r = difflib.SequenceMatcher(None, w, f).ratio()
        # short names need a close spelling: at 0.8 "bhakti" (a genre!), "bharat" and "aarti" were "Bharti";
        # how the name is misheard is lexicon.HEARD's job
        if r >= (0.75 if len(f) >= 8 else 0.86):
            best = max(best, r)
    if not best and len(words) <= 2 and any(lexicon.heard_name(w, k) for k in wake_words()):
        return HEARD_SCORE
    return best


def _clause_edges(text: str) -> tuple[set[int], set[int]]:
    """The token indices (as `_tokens` counts them) where a clause begins (past any lead-in word) and
    where one ends: "…that's Marty, next song" — a name is said on its own, set off by commas."""
    heads, tails, n = set(), set(), 0
    for clause in re.split(r"[.,!?।…;:]+", text):
        toks = _tokens(clause)
        j = 0
        while j < len(toks) and toks[j] in _LEAD:
            j += 1
        heads.add(n + j)
        if toks:
            tails.add(n + len(toks) - 1)
        n += len(toks)
    return heads, tails


def split_wake(text: str) -> str | None:
    """What was asked when the sentence opens with a wake word (the name comes first); None when it is
    not for us. Of the 1–3 word spans that could be the name, the closest wins."""
    toks = _tokens(text)
    i = 0
    while i < len(toks) and toks[i] in _LEAD:
        i += 1
    head = max(((_name_score(toks[i:i + n]), n) for n in (1, 2, 3) if i + n <= len(toks)), default=(0.0, 0))
    if head[0]:  # "bharti …", "f m …"
        return " ".join(toks[_past_names(toks, i + head[1]):])
    return None


def _past_names(toks: list[str], j: int) -> int:
    """Past the name said again ("Bharti… Bharti!", "Bharti ji"): it is not a wish — a second "Bharti"
    was taken for one, and fuzzily for the genre "bhakti" (caught in testing)."""
    while j < len(toks):
        if toks[j] in _LEAD or toks[j] in ("ji", "jee"):
            j += 1
            continue
        n = next((n for n in (1, 2) if j + n <= len(toks) and _name_score(toks[j:j + n])), 0)
        if not n:
            break
        j += n
    return j


def find_wake(text: str) -> str | None:
    """Like split_wake, but the name may come anywhere: a 4 s window of music starts with whatever the
    song was singing ("…isn't it? Bharti, next song") — caught live, the head-only check missed every
    command. What follows the first name is what was asked. A word that only sounds like the name counts
    where a call would be — starting a clause, or the last thing heard — not inside a lyric ("…smarty
    pants…")."""
    toks = _tokens(text)
    heads, tails = _clause_edges(text)
    for i in range(len(toks)):
        best = max(((_name_score(toks[i:i + n]), n) for n in (1, 2, 3) if i + n <= len(toks)), default=(0.0, 0))
        if best[0] == HEARD_SCORE and i not in heads and i + best[1] - 1 not in tails:
            continue
        if best[0]:
            return " ".join(toks[_past_names(toks, i + best[1]):])
    return None


def has_name(text: str) -> bool:
    """Is a wake word anywhere in this, in any form — "party" and "smart" too? For a second, unprimed
    listen to a name Whisper was unsure of: it only has to find the name there again, not spell it."""
    toks = _tokens(text)
    if any(_name_score(toks[i:i + n]) for i in range(len(toks)) for n in (1, 2) if i + n <= len(toks)):
        return True
    return any(lexicon.sounds_like(_norm(t), w) for t in toks for w in wake_words())


DIP_FOR = ("dexter",)  # names the music also dips for: Dexter hears the rest of the sentence clean


def addressed(text: str) -> bool:
    """Does this sentence open with one of our wake words, or with Dexter's name?"""
    if split_wake(text) is not None:
        return True
    toks = [t for t in _tokens(text) if t not in _LEAD]
    return bool(toks) and any(difflib.SequenceMatcher(None, _norm(toks[0]), n).ratio() >= 0.8 for n in DIP_FOR)


def _command_words() -> list[str]:
    """Whole command words only — never a piece of a phrase: "chazil" was mapped to "chal" (from "kya chal
    raha"), and then to the mood "calm" (caught in testing)."""
    words = {p for group in (NOW, DISLIKE, LIKE, PAUSE, STOP, NEXT, RESUME, PLAY, MUTE, UNMUTE) for p in group
             if " " not in p}
    return sorted(w for w in words | {"gaana", "gaane", "sunao", "chalao", "lagao", "bajao"} if w.isascii() and len(w) >= 4)


def canon(text: str) -> str:
    """Hinglish as Whisper spells it, mapped onto the words the commands know: "aglah gana chalaw" →
    "agla gaana chalao"."""
    from . import jockey, moods

    known = _command_words()
    # what to play stays as said: "bhajan" must not become "baja"
    keep = set(jockey.GENRE_WORDS) | set(jockey.LANGUAGES) | {"bhajan", "bhajans", "ghazal", "ghazals"} | {
        w for words in (*moods.MOODS.values(), *moods.TIMES.values()) for w in words}
    toks = []
    for t in _tokens(text):  # a verb run onto the wish ("jazeelsunao") comes off it
        v = next((v for v in ("sunao", "chalao", "lagao", "bajao") if t.endswith(v) and len(t) > len(v) + 2), None)
        toks += [t[: -len(v)], v] if v else [t]
    out = []
    for t in toks:
        if t.isascii() and len(t) >= 4 and t not in known and t not in keep:
            near = [k for k in difflib.get_close_matches(t, known, n=3, cutoff=0.8) if abs(len(k) - len(t)) <= 1]
            t = near[0] if near else t
        out.append(t)
    return " ".join(out)


@dataclass
class Command:
    action: str  # next | pause | resume | stop | like | dislike | now | mute | unmute | play
    focus: str = ""


NOW = ("what's playing", "whats playing", "what is playing", "which song", "what song", "what is this", "kaun sa",
       "kaunsa", "kon sa", "konsa", "kya chal", "kya baj", "कौन सा", "कौनसा", "क्या चल", "क्या बज")
DISLIKE = ("don't like", "dont like", "do not like", "dislike", "hate", "never play", "not good", "pasand nahi",
           "pasand nahin", "nahi pasand", "bekar", "bekaar", "bakwas", "hatao", "हटाओ", "पसंद नहीं", "नापसंद", "बेकार", "बकवास")
LIKE = ("like", "love", "accha", "achha", "acha", "badhiya", "badiya", "pasand", "great", "nice", "wah", "waah",
        "अच्छा", "बढ़िया", "पसंद", "वाह")
PAUSE = ("pause", "ruko", "ruk jao", "roko", "rok do", "hold on", "wait", "thamo", "रुको", "रोको", "पॉज़", "पॉज")
STOP = ("stop", "band", "bandh", "bas", "turn off", "switch off", "chup", "quiet", "बंद", "बस", "चुप", "स्टॉप")
NEXT = ("next", "skip", "agla", "agala", "aglaa", "dusra", "doosra", "change", "badlo", "badal do", "another",
        "अगला", "दूसरा", "बदलो", "बदल दो", "नेक्स्ट", "स्किप")
RESUME = ("resume", "continue", "unpause", "chalu", "chaalu", "phir se", "wapas", "start", "shuru", "चालू", "फिर से", "शुरू")
# no sound, the show going on ("awaaz band" is not "band": stop) — and the sound back
_AWAAZ = ("awaaz", "awaz", "aawaz", "aawaaz", "avaaz", "avaz", "awaj", "aawaj")
# not "khamosh": "khamoshi sunao" (a song) would be read as it
MUTE = ("mute", "muted", "sound off", "volume off", "silent", "silence", "म्यूट", "आवाज़ बंद", "आवाज बंद", "खामोश",
        *(f"{a} {b}" for a in _AWAAZ for b in ("band", "bandh", "off")))
UNMUTE = ("unmute", "un mute", "unmuted", "sound on", "sound back", "volume on", "अनम्यूट", "आवाज़ खोलो", "आवाज़ चालू",
          "आवाज खोलो", "आवाज चालू", *(f"{a} {b}" for a in _AWAAZ for b in ("kholo", "khol do", "chalu", "chaalu", "on",
                                                                          "wapas", "lao")))
PLAY = ("play", "put on", "sunao", "suna do", "sunaiye", "chalao", "chala do", "chalaiye", "lagao", "laga do", "bajao",
        "baja do", "i want", "want to hear", "chahiye", "सुनाओ", "चलाओ", "लगाओ", "बजाओ", "सुना दो", "चला दो", "लगा दो",
        "बजा दो", "प्ले")
REST_FILLER = {"kuch", "koi", "mujhe", "please", "plz", "now", "ab", "abhi", "yaar", "zara", "thoda", "gaana", "gana",
               "gaane", "gane", "track", "one", "this", "that", "it", "ye", "yeh", "wala", "wale", "waale", "do", "dijiye",
               "karo", "kar", "the", "a", "an", "hai", "is", "song", "songs", "music", "me", "for", "us", "can", "you",
               "could", "would", "will", "let's", "lets", "some", "something", "something's", "to", "hear", "listen",
               "and", "again", "ji", "jee", "किसी", "कुछ", "गाना", "गाने", "कोई", "ये", "यह", "दो", "करो", "है", "मुझे"}


def _has(text: str, phrases) -> bool:
    return any(re.search(rf"(?:^|\s){re.escape(p)}(?:\s|$)", text) for p in phrases)


def _rest(text: str) -> str:
    """The words left once the command words and fillers are gone: what to play."""
    from .jockey import FILLER

    t = f" {text} "
    for p in sorted(PLAY + RESUME + NEXT + UNMUTE, key=len, reverse=True):
        t = re.sub(rf"(?<=\s){re.escape(p)}(?=\s)", " ", t)
    words = (re.sub(r"'s$", "", w) for w in t.split())  # "jagjit singh's songs" → jagjit singh
    return " ".join(w for w in words if w and w not in REST_FILLER and w not in FILLER)


def parse(command: str) -> Command | None:
    """The player action in what was asked ("" = the name alone: play / resume)."""
    c = canon(command.replace("’", "'"))
    if not c:
        return Command("resume")
    if _has(c, NOW) or c.endswith("playing"):
        return Command("now")
    played = _has(c, PLAY)
    if _has(c, UNMUTE) and not played:  # "un mute" holds "mute"; "awaaz chalu" holds "chalu" (resume)
        return Command("unmute")  # "unmute and play ghazals" is that wish (playing unmutes)
    if _has(c, MUTE) and not played:  # "awaaz band" holds "band" (stop); "khamoshi sunao" is a wish
        return Command("mute")
    if _has(c, DISLIKE):
        return Command("dislike")
    if _has(c, LIKE) and not played:
        return Command("like")
    if _has(c, PAUSE):
        return Command("pause")
    if _has(c, STOP) and not _has(c, ("don't stop", "dont stop")):
        return Command("stop")
    rest = _rest(c)
    if rest and not _has(c, NEXT):
        return Command("play", rest)
    if _has(c, NEXT):
        return Command("next")
    if _has(c, RESUME) or played:
        return Command("resume")
    return None


_SAID_WITH = {"next": NEXT, "stop": STOP, "pause": PAUSE, "resume": RESUME + PLAY, "like": LIKE, "dislike": DISLIKE,
              "now": NOW, "mute": MUTE, "unmute": UNMUTE}


def confident(asked: str, cmd: Command) -> bool:
    """Does what followed the name really sound like this command, rather than a lyric that happened to
    hold a name ("…on the radio all night long…" must not become "play night songs")? A stop / next /
    pause word comes within the first few words; a wish to play has a play word (sunao, lagao, play…) or is
    a few words that all mean something to the player (a genre, mood, programme or singer)."""
    c = canon(asked)
    if cmd.action in _SAID_WITH:
        return _has(" ".join(c.split()[:4]), _SAID_WITH[cmd.action]) or (cmd.action == "now" and c.endswith("playing"))
    if _has(c, PLAY):
        return True
    rest = _rest(c).split()
    return 0 < len(rest) <= 3 and playable(" ".join(rest)) is not None


def playable(focus: str) -> str | None:
    """The wish as the player should get it, or None when nothing would play — a misheard word must not
    stop the music. A singer Whisper spelt its own way ("Jagjeet Singh") becomes the one in the
    collection ("Jagjit Singh")."""
    from sqlmodel import select

    from . import jockey
    from .db import Song, session

    want = jockey.resolve_focus(focus)
    if want[0] == "text":  # garbled around a word it knows ("k s m gajal"): that word is the wish
        for w in focus.split():
            if len(w) >= 3 and jockey.resolve_focus(w)[0] not in ("any", "text"):
                return w
        # ... or a genre / language Whisper spelt its own way ("chazil sunao" → ghazal). Not moods: short
        # words like "calm" or "soft" are matched by anything.
        # 5+ letters only: "fazil" came out "film" (caught in testing) — a short word is close to anything
        known = [w for w in [*jockey.GENRE_WORDS, *jockey.LANGUAGES, "purane"] if len(w) >= 5]
        for w in focus.split():
            near = [k for k in difflib.get_close_matches(w, known, n=3, cutoff=0.65) if abs(len(k) - len(w)) <= 2]
            if len(w) >= 4 and near:
                return near[0]
    if want[0] == "any":
        return None
    if want[0] != "text":
        return focus
    with session() as s:
        songs = s.exec(select(Song).where(Song.status != "rejected", Song.yt_video_id.is_not(None))).all()
    # whole words of a singer or a title: "gha" (a word cut off) must not match Ghulam Ali
    wanted = re.findall(r"\w+", focus.lower())
    # not a word every other title has ("studio", "remix"): "Bharti's studio" played Coke Studio (live)
    if (wanted and not set(wanted) <= lexicon.COMMON
            and any(set(wanted) <= set(re.findall(r"\w+", f"{x.artist} {x.title}".lower())) for x in songs)):
        return focus
    singers = {a.strip() for x in songs for a in (x.artist or "").split(",") if a.strip()}
    by_norm = {jockey._n(a): a for a in singers}
    near = difflib.get_close_matches(jockey._n(want[1]), list(by_norm), n=1, cutoff=0.8)
    if near:
        return by_norm[near[0]]
    # misheard ("leharman chalisa"): the title, singer or programme it sounds like (lexicon.py)
    return lexicon.correct_wish(focus)


# ----------------------------------------------------------------------------- hearing
class Segmenter:
    """Cuts sentences out of a 16 kHz mono int16 stream by loudness over an adaptive noise floor. A sound
    longer than `max_s` (music in the room, a conversation) is dropped whole."""

    def __init__(self, on_speech: Callable[[np.ndarray], None], above_db: float = 12.0, min_db: float = -50.0,
                 hang_s: float = 0.6, pre_s: float = 0.3, min_s: float = 0.5, max_s: float = 8.0,
                 on_start: Callable[[], None] | None = None, on_cancel: Callable[[], None] | None = None,
                 on_window: Callable[[np.ndarray], None] | None = None, window_s: float = 4.0, hop_s: float = 2.0,
                 on_early: Callable[[np.ndarray], None] | None = None, early_s: float = 1.2):
        self.on_speech = on_speech
        self.on_start, self.on_cancel = on_start or (lambda: None), on_cancel or (lambda: None)
        # the first `early_s` of a sentence, while it is still being said (is it a wake word?)
        self.on_early, self.early_f = on_early, int(early_s * 1000 / 30)
        # music in the room never ends a sentence: every `hop_s` the last `window_s` go to `on_window`,
        # so a wake word spoken over it is still found
        self.on_window = on_window
        self.win_f, self.hop_f = int(window_s * 1000 / 30), int(hop_s * 1000 / 30)
        self._ring: list[np.ndarray] = []
        self._since_hop = 0
        self.above, self.min_db = above_db, min_db
        self.hang, self.pre = int(hang_s * 1000 / 30), int(pre_s * 1000 / 30)
        self.min_f, self.max_f = int(min_s * 1000 / 30), int(max_s * 1000 / 30)
        self.floor = -60.0
        self._buf = np.zeros(0, np.int16)
        self._pre: list[np.ndarray] = []
        self._cur: list[np.ndarray] | None = None
        self._loud_run = self._quiet_run = 0
        self._too_long = False

    def feed(self, pcm: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, pcm])
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            self._frame(frame)

    def _window(self, frame: np.ndarray) -> None:
        """While a sound goes on (a sentence still being said, or the music), every `hop_s` the last
        `window_s` go to `on_window` — from the first seconds, not only once the sound is "too long":
        caught in testing, a command said as the song played was thrown away with the first 8 s."""
        if not self.on_window:
            return
        self._ring = (self._ring + [frame])[-self.win_f:]
        self._since_hop += 1
        if self._since_hop >= self.hop_f and len(self._ring) >= self.win_f:
            self._since_hop = 0
            self.on_window(np.concatenate(self._ring))

    def _frame(self, frame: np.ndarray) -> None:
        x = frame.astype(np.float32) / 32768
        db = 20 * float(np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-6)))
        loud = db > max(self.floor + self.above, self.min_db)
        if self._cur is None and not self._too_long:  # the floor learns from what is not speech
            self.floor = db if db < self.floor else 0.995 * self.floor + 0.005 * db
        if self._too_long:  # wait for the long sound to end, listening in windows meanwhile
            self._quiet_run = 0 if loud else self._quiet_run + 1
            self._window(frame)
            if self._quiet_run >= self.hang:
                self._too_long, self._quiet_run, self._ring, self._since_hop = False, 0, [], 0
            return
        if self._cur is None:
            self._pre = (self._pre + [frame])[-self.pre:]
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= 3:  # ~90 ms of voice: someone is speaking
                self._cur, self._quiet_run = list(self._pre), 0
                self._ring, self._since_hop = list(self._pre), 0
                self.on_start()
            return
        self._cur.append(frame)
        self._window(frame)
        self._quiet_run = 0 if loud else self._quiet_run + 1
        if self.on_early and len(self._cur) == self.pre + self.early_f:
            self.on_early(np.concatenate(self._cur))
        if len(self._cur) > self.max_f:  # its windows have been read all along; it goes on as "music"
            self._cur, self._too_long, self._pre = None, True, []
            self.on_cancel()
        elif self._quiet_run >= self.hang:
            seg, self._cur, self._pre, self._loud_run = self._cur, None, [], 0
            if len(seg) - self.hang >= self.min_f:
                self.on_speech(np.concatenate(seg))
            else:
                self.on_cancel()


def pick_mic() -> str | None:
    """VOICE_MIC / LISTEN_MIC's match, else the loudest real mic in a short take (a dead one reads -76 dB)."""
    from . import listen

    want = (settings.voice_mic or settings.listen_mic).strip().lower()
    names = listen._dshow_audio()
    if want:
        return next(iter(listen.preferred(names, want)), None)
    real = [n for n in names if not listen.NOT_A_MIC.search(n)]
    if len(real) <= 1:
        return real[0] if real else None
    with tempfile.TemporaryDirectory() as tmp:
        takes = listen.record_mics(2, Path(tmp))
    return takes[0][2] if takes and takes[0][1] > listen.SILENCE_DB else (real[0] if real else None)


def _db(pcm: np.ndarray) -> float:
    x = pcm.astype(np.float32) / 32768
    return 20 * float(np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-6))) if x.size else -120.0


class _Capture:
    """The command after the name: recorded with the music dipped, until you stop talking. Whether you
    are talking is Silero's call (the speech detector bundled with faster-whisper), checked every 0.3 s:
    by loudness alone the dipped music kept "talking" and the recording ran to its limit (caught in
    testing)."""

    CHECK_EVERY = 10  # frames (0.3 s)
    END_SILENCE_S = 0.7

    def __init__(self, prefill: list[np.ndarray]):
        self.frames: list[np.ndarray] = list(prefill)
        self.prefilled = len(prefill)  # you may have been saying it already when the window opened
        self.n = 0  # frames since the window opened: time is counted in audio, not on the wall clock
        self.guard = int(CHIME_GUARD_S / 0.03)
        self.dbs: list[float] = [_db(f) for f in prefill]  # loudness per frame

    def feed(self, frame: np.ndarray) -> str | None:
        """One 30 ms frame; returns "done" (you spoke and stopped), "none" (nothing said in time) or None."""
        self.frames.append(frame)
        self.n += 1
        self.dbs.append(_db(frame))
        if self.n <= self.guard or self.n % self.CHECK_EVERY:
            return None
        t = self.n * 0.03
        voiced = self.voiced()
        if not voiced:
            if self.prefilled and t > PROMPT_REPLY_S:
                return "done"  # you said it straight after the name, before the window opened: it is recorded
            return "none" if t > ARM_S else None
        if (len(self.dbs) - 1 - voiced[-1]) * 0.03 >= self.END_SILENCE_S:
            return "done"
        if (len(self.dbs) - voiced[0]) * 0.03 > CAPTURE_MAX_S:
            return "done"
        return None

    def voiced(self) -> list[int]:
        """The frames since the window opened where you are talking: speech to Silero AND clearly above the
        dipped music between your words. Silero alone took the dipped song's singing for a voice, and loudness
        alone a drum — either way the recording ran to its limit (caught in testing)."""
        start = self.prefilled + self.guard
        after = self.dbs[start:]
        if not after:
            return []
        room = float(np.percentile(after, 30))  # the music, down 14 dB, between your words
        mask = np.zeros(len(self.dbs), bool)
        for s in self.speech():
            mask[s["start"] // FRAME:s["end"] // FRAME + 1] = True
        return [i for i in range(start, len(self.dbs)) if mask[i] and self.dbs[i] > room + 9.0]

    def speech(self) -> list[dict]:
        """Where Silero hears a voice in what was recorded."""
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        audio = np.concatenate(self.frames).astype(np.float32) / 32768
        return get_speech_timestamps(audio, VadOptions(threshold=0.5, min_silence_duration_ms=250, speech_pad_ms=60))


class VoiceControl:
    """The listening thread, Whisper, and what a command does to the PC player."""

    def __init__(self, player: Callable, notify: Callable[[str], None] | None = None):
        from collections import deque

        self._player = player
        self._notify = notify
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._heard: queue.Queue = queue.Queue(maxsize=4)
        self._models: dict = {}
        self._early = None
        self._early_busy = threading.Lock()
        self._armed_until = 0.0  # a sentence before this needs no name (the tray's "Speak a command")
        self._capture: _Capture | None = None  # recording the command after the name
        self._recent: deque = deque(maxlen=int(6 / 0.03))  # the last ~6 s of 30 ms frames, for prefill
        self._frames_seen = 0
        self._dipped_at = 0.0
        self._windows_quiet_until = 0  # a frame index: windows before it hold audio already acted on
        self._last_done: tuple[tuple, float] = ((), 0.0)
        self.mic: str | None = None
        self.last = ""  # what was heard last, and what came of it
        self.history: deque = deque(maxlen=15)  # the last sentences heard, with the time and what was done
        self.running = False

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.running = True
        threading.Thread(target=self._listen, daemon=True, name="mastgaane-voice-mic").start()
        threading.Thread(target=self._work, daemon=True, name="mastgaane-voice-stt").start()

    def stop(self) -> None:
        self.running = False
        self._stop.set()
        self._capture = None
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.kill()

    def arm(self, prefill_from: int | None = None) -> None:
        """Open the listening window: chime, dip the music, record what comes next as the command (also
        the tray's *Speak a command*). `prefill_from` = the frame where the name ended, when a command
        may already have begun before the window opened."""
        prefill = []
        if prefill_from is not None:
            keep = max(0, self._frames_seen - prefill_from + int(PREFILL_S / 0.03))
            prefill = list(self._recent)[-keep:] if keep else []
        # no mic of our own: nothing to record — Dexter sends the next sentence (handle)
        self._capture = _Capture(prefill) if self.running else None
        self._armed_until = time.monotonic() + ARM_S + CAPTURE_MAX_S
        self._dip(wake=True)
        self._chime()
        self._tell("Listening… say the command")

    @property
    def listening(self) -> bool:
        return self._capture is not None

    @property
    def awaiting(self) -> bool:
        """The listening window is open: the next sentence is the command, no name needed."""
        return time.monotonic() <= self._armed_until

    # ------------------------------------------------------------------ the mic
    def _listen(self) -> None:
        while not self._stop.is_set():
            try:
                self.mic = self.mic or pick_mic()
                if not self.mic:
                    raise RuntimeError("no microphone found")
                self._read(self.mic)
            except Exception as e:
                console.log(f"[yellow]voice control: {e}; trying again in 15 s[/]")
                self.mic = None
            self._stop.wait(15)

    def _read(self, mic: str) -> None:
        console.log(f"voice control: listening on {mic} — say “{', '.join(w.capitalize() for w in wake_words())}, …”")
        seg = Segmenter(self._on_speech, on_cancel=self._undip, on_window=self._on_window, on_early=self._on_early,
                        window_s=WINDOW_S, hop_s=HOP_S)
        self._proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "dshow", "-audio_buffer_size", "50", "-i", f"audio={mic}",
             "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NOWINDOW)
        out = self._proc.stdout
        while not self._stop.is_set():
            data = out.read(FRAME * 2 * 5)  # 150 ms
            if not data:
                break
            self.feed(np.frombuffer(data[: len(data) // 2 * 2], np.int16), seg)
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def feed(self, pcm: np.ndarray, seg: Segmenter | None) -> None:
        """Mic audio: to the command being recorded, else to the segmenter that looks for the name."""
        for i in range(0, len(pcm) - FRAME + 1, FRAME):
            frame = pcm[i:i + FRAME]
            self._frames_seen += 1
            self._recent.append(frame)
            cap = self._capture
            if cap is None:
                if seg is not None:
                    seg.feed(frame)
                continue
            state = cap.feed(frame)
            if state is None:
                continue
            self._capture = None
            # the windows read next still hold the command just recorded (and may "hear" the name in it)
            self._windows_quiet_until = self._frames_seen + int(WINDOW_S / 0.03)
            self._trace(f"capture       {cap.n * 0.03:4.1f}s after the chime: {state}"
                        + (f" ({cap.prefilled * 0.03:.1f}s from before it)" if cap.prefilled else ""))
            if state == "done" or cap.prefilled:  # a quick talker may have said it all before the chime
                try:
                    self._heard.put_nowait(("command", np.concatenate(cap.frames)))
                except queue.Full:
                    self._undip()
            else:
                self._log("(silence)", "no command came — listening for the name again")
                self._undip()

    def _on_speech(self, pcm: np.ndarray) -> None:
        try:
            self._heard.put_nowait(("sentence", pcm))
        except queue.Full:
            self._undip()

    def _on_window(self, pcm: np.ndarray) -> None:
        if self._frames_seen >= self._windows_quiet_until:
            try:
                self._heard.put_nowait(("window", pcm))
            except queue.Full:
                pass  # behind already: the next window overlaps this one

    def _on_early(self, pcm: np.ndarray) -> None:
        """The first moments of a sentence: off the mic thread, does it open with "Dexter"? Then the
        music dips so Dexter hears the rest clean."""
        if not self._early_busy.acquire(blocking=False):
            return  # still checking the last one
        threading.Thread(target=self._early_check, args=(pcm,), daemon=True, name="mastgaane-voice-early").start()

    def _early_check(self, pcm: np.ndarray) -> None:
        try:
            if mentions_dexter(self.hear(pcm, fast=True, primed=False, early=True)[0], anywhere=False):
                self._dip(wake=True)
        except Exception as e:
            console.log(f"[yellow]voice control: {e}[/]")
        finally:
            self._early_busy.release()

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                kind, pcm = self._heard.get(timeout=1)
            except queue.Empty:
                continue
            end = self._frames_seen  # roughly where this audio ended
            try:
                if kind == "window":
                    self.screen(pcm, end)
                elif kind == "sentence":
                    self.sentence(pcm, end)
                else:
                    self.command(pcm)
            except Exception as e:
                console.log(f"[yellow]voice control: {e}[/]")
            finally:
                if self._heard.empty() and self._capture is None:
                    self._undip()

    # ------------------------------------------------------------------ the music dips
    def _dip(self, wake: bool = True) -> None:
        """Dip the music (a name was heard) until the command is done — at most DIP_MAX_S."""
        try:
            p = self._player()
            if p.now()["state"] != "playing":
                return
            p.duck()
        except Exception:
            return
        self._dipped_at = time.monotonic()
        timer = threading.Timer(DIP_MAX_S, self._undip_if_stale, args=(self._dipped_at,))
        timer.daemon = True  # must not hold up a shutdown
        timer.start()

    def _undip(self) -> None:
        if self._dipped_at:
            self._dipped_at = 0.0
            try:
                self._player().duck(None)
            except Exception:
                pass

    def _undip_if_stale(self, since: float) -> None:
        if self._dipped_at == since and self._capture is None:
            self._undip()

    # ------------------------------------------------------------------ understanding
    def _whisper(self, fast: bool = False):
        """Whisper on the CPU: VOICE_WHISPER_MODEL for commands, VOICE_SCREEN_MODEL (smaller) to look for
        the name."""
        name = settings.voice_screen_model if fast else settings.voice_whisper_model
        if name not in self._models:
            from faster_whisper import WhisperModel

            # two threads: the name is looked for every 1.5 s over music and must not take the PC
            self._models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=2)
        return self._models[name]

    def _early_model(self):
        """Its own copy of the fast model: the Dexter check runs beside a full transcription."""
        if self._early is None:
            from faster_whisper import WhisperModel

            self._early = WhisperModel(settings.voice_screen_model, device="cpu", compute_type="int8", cpu_threads=1)
        return self._early

    def hear(self, pcm: np.ndarray, fast: bool = False, primed: bool = True, keep: bool = False,
             early: bool = False) -> tuple[str, float]:
        """(text, the highest no-speech probability among its segments). `primed` = the wake words as
        Whisper's prompt: it then spells them right, even over a song — and on plain music may write them
        back (see `_echo`). Never music words in the prompt: they came back as "Bhajan. Bhajan. Bhajan…" and
        played bhajans (caught in testing). One greedy pass, a few words at most: on music Whisper's retries and run-on lyrics
        took ~3 s per 3 s window (caught in testing), and a command is short. `early` = the Dexter check's own
        copy of the fast model (it runs beside the worker)."""
        audio = pcm.astype(np.float32) / 32768
        model = self._early_model() if early else self._whisper(fast)
        segs, _ = model.transcribe(
            audio, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False,
            initial_prompt=prompt() if primed else None,
            temperature=0.0, compression_ratio_threshold=None, log_prob_threshold=None, without_timestamps=True,
            max_new_tokens=40, no_speech_threshold=None if keep else 0.6)
        segs = list(segs)
        text = " ".join(s.text.strip() for s in segs).strip()
        if _babble(text):  # Whisper looping on music ("Bhajan. Bhajan. Bhajan…"): nobody said that
            return "", 1.0
        return text, max((s.no_speech_prob for s in segs), default=1.0)

    def transcribe(self, pcm: np.ndarray, fast: bool = False, primed: bool = True, keep: bool = False) -> str:
        """`keep` = never drop a stretch as "no speech": for audio known to hold you (a name was heard) —
        a quiet command over the dipped song came back empty otherwise (caught in testing)."""
        return self.hear(pcm, fast, primed, keep)[0]

    def sentence(self, pcm: np.ndarray, end: int | None = None) -> str | None:
        """A sentence with quiet around it (a quiet room, or headphones): is it "Bharti, …"?"""
        if self._capture is not None or self._frames_seen < self._windows_quiet_until:
            return None  # its windows were read while it was being said, and acted on
        text, nsp = self.hear(pcm, fast=True)
        self._debug("sentence", pcm, text, nsp)
        if _echo(text, nsp) and not self._confirmed(pcm):
            return None
        asked = split_wake(text)
        if asked is None:
            if text:
                self._log(text, "not for me")
            return None
        return self._on_name(asked, text, pcm, None)

    def screen(self, pcm: np.ndarray, end: int | None = None) -> str | None:
        """A window of continuous sound (the music on speakers): the name may come anywhere in it, after
        whatever the song was singing ("…isn't it? Bharti, next song")."""
        if self._capture is not None or self._frames_seen < self._windows_quiet_until:
            return None
        text, nsp = self.hear(pcm, fast=True)
        self._debug("music window", pcm, text, nsp)
        # (no Silero gate: it heard the songs' singers as often as not — 75 of 205 echoes — and a quiet
        # caller is the one it misses)
        asked = find_wake(text)
        if asked is None:
            if mentions_dexter(text):
                self._dip(wake=True)  # "Dexter, …" over the music: he hears it better, he does the rest
            return None
        # Whisper writes its prompt into the song too: "Bharti.", "Hello, Bharti.", "Bharti is here." A name
        # with no command after it opens the listening window only if a second, unprimed listen hears it.
        if not _commanded(asked) and not _sure_call(text) and not self._confirmed(pcm):
            return None
        done = self._on_name(asked, text, pcm, end)
        if done or self._capture is not None:  # the next window still holds these words
            self._windows_quiet_until = self._frames_seen + int(WINDOW_S / 0.03)
        return done

    def _confirmed(self, pcm: np.ndarray) -> bool:
        """A lone name Whisper was unsure of — on plain music it writes its prompt ("Bharti.") back: heard
        again without the prompt, is the name there in some form ("Marty", "Bharat", "party")? Live, real
        calls came with a high no-speech score as often as echoes did, and dropping them all left
        "Bharti" deaf (17 Oct)."""
        text, nsp = self.hear(pcm, fast=True, primed=False, keep=True)
        real = has_name(text)
        self._debug("second listen", pcm, text, nsp, "the name is there: a call" if real else "no name: Whisper's echo")
        return real

    def _on_name(self, asked: str, text: str, pcm: np.ndarray | None, end: int | None) -> str | None:
        """A name was heard; `asked` is what followed it. A command → done; nothing (or a word, the rest
        still coming) → the listening window opens; anything else (a lyric that happened to hold a name)
        → ignored."""
        host = self._host_talking()
        words = [w for w in _tokens(asked) if w not in REST_FILLER and w not in _LEAD]
        if not words:
            if host:
                self._trace("the host is talking: no listening window")
                return None  # her "…FM…" / "…रेडियो…": no listening window, no chime
            self._log(text, "listening for the command")
            self.arm(prefill_from=end)
            return None
        cmd = parse(asked)
        if cmd is not None and not confident(asked, cmd):
            cmd = None  # a lyric that happened to hold a name
        if cmd is not None and cmd.action in QUICK:
            if host and cmd.action not in WHILE_HOST_TALKS:
                self._trace(f"the host is talking: {cmd.action} waits")
                return None
            return self._do(cmd, text)
        if host:
            self._trace("the host is talking: only stop / pause / next")
            return None
        if (cmd is None or cmd.action == "play") and pcm is not None:
            # the full model reads it again: the names in a wish, and words the fast one ran together
            # ("Bharti. Jazeelsunao." — caught in testing)
            full = self.transcribe(pcm, keep=True)
            self._debug("full model", pcm, full)
            again = (find_wake(full) if end is not None else split_wake(full))
            better = parse(again) if again else None
            if better is not None and confident(again, better) and (better.action != "play" or playable(better.focus)):
                cmd, text = better, full
                if cmd.action in QUICK:
                    return self._do(cmd, text)
        if cmd is not None and cmd.action == "play":
            focus = playable(cmd.focus)
            if focus:
                cmd.focus = focus
                return self._do(cmd, text)
        if len(words) <= 1 and pcm is not None:  # "Bharti, gha…" — the rest is still being said
            self._log(text, "listening for the rest")
            self.arm(prefill_from=end)
            return None
        self._log(text, "not understood")
        return None

    def command(self, pcm: np.ndarray) -> str | None:
        """What was said after the name, recorded with the music down. The name may be said again. No
        prompt: primed with the name, a quiet "Pause." over the dipped song came back "Bharti." (caught in
        testing) — and the command does not need the name spelt."""
        text = self.transcribe(pcm, primed=False, keep=True)
        self._debug("command", pcm, text)
        asked = split_wake(text)
        asked = text if asked is None else asked
        if not asked.strip() or _only_names(text):
            self._log(text or "(nothing)", "no command heard")
            return None
        cmd = parse(asked)
        if cmd is not None and cmd.action == "play" and len(cmd.focus.split()) > 4:
            cmd = None  # a wish is a few words; a long run is a lyric or Whisper rambling
        focus = playable(cmd.focus) if cmd is not None and cmd.action == "play" else None
        if cmd is None or (cmd.action == "play" and focus is None):
            self._log(text, "not understood")
            self._tell(f"Didn't catch that: “{asked}”")
            return None
        if focus:
            cmd.focus = focus
        return self._do(cmd, text)

    def handle(self, text: str) -> str | None:
        """A sentence already written down — Dexter hands over what he heard starting with our name, or
        the next one once the name alone opened the window (POST /api/voice/heard), and the tests."""
        if not text or _echo(text, 0.0):
            return None
        armed = time.monotonic() <= self._armed_until
        asked = split_wake(text)
        if asked is None and not armed:
            return None
        self._armed_until = 0.0
        asked = text if asked is None else asked
        words = [w for w in _tokens(asked) if w not in REST_FILLER and w not in _LEAD]
        if not words:
            self._log(text, "listening for the command")
            self.arm()
            return None
        try:
            cmd = parse(asked)
            if cmd is not None and not armed and not confident(asked, cmd):
                # Dexter now also hands over a "Bharti" from inside a long capture over the music ("…lyrics.
                # Marty, I don't like…"): what follows the name must sound like a command, quietly
                self._log(text, "not a command")
                return None
            if cmd is not None and cmd.action not in WHILE_HOST_TALKS and self._host_talking():
                return None
            focus = playable(cmd.focus) if cmd is not None and cmd.action == "play" else None
            if cmd is None or (cmd.action == "play" and focus is None):
                self._log(text, "not understood")
                done = f"Didn't catch that: “{asked}”"
                self._tell(done)
                return done
            if focus:
                cmd.focus = focus
            return self._do(cmd, text)
        finally:
            if not self.running:  # our own mic's worker brings the music back up; without it, here
                self._capture = None
                self._undip()

    def _do(self, cmd: Command, text: str) -> str:
        """Chime, act, say so — once: the same command again within DEDUP_S was heard twice."""
        sig, at = (cmd.action, cmd.focus), time.monotonic()
        if self._last_done[0] == sig and at - self._last_done[1] < DEDUP_S:
            return "already done"
        self._last_done = (sig, at)
        self._capture = None
        self._chime()  # first: you hear at once that it was understood
        done = self.execute(cmd)
        self._log(text, done)
        console.log(f"voice control: {self.last}")
        self._tell(done)
        return done

    def _host_talking(self) -> bool:
        """The host is talking, or was until a moment ago (her words are still in the mic's windows)."""
        try:
            p = self._player()
            return p.now()["phase"] == "link" or time.monotonic() - getattr(p, "link_ended_at", -1e9) < HOST_TAIL_S
        except Exception:
            return False

    def execute(self, cmd: Command) -> str:
        from .tray import status_line

        p = self._player()
        if cmd.action == "next":
            p.skip()
            return "Next song"
        if cmd.action == "pause":
            p.pause()
            return "Paused"
        if cmd.action == "resume":
            state = p.now()["state"]
            p.resume() if state == "paused" else p.play()
            return "Playing"
        if cmd.action == "stop":
            p.stop()
            return "Stopped"
        if cmd.action == "like":
            p.like()
            return "Liked — more like this"
        if cmd.action == "dislike":
            p.dislike()
            return "Never again — skipping"
        if cmd.action == "now":
            return status_line(p.now())
        if cmd.action == "mute":
            p.mute(True)
            return "Muted — say “Bharti, unmute” for the sound"
        if cmd.action == "unmute":
            p.mute(False)
            return "Sound back on"
        now = p.play(cmd.focus)
        return f"Playing {now.get('wish') or cmd.focus}"

    # ------------------------------------------------------------------ feedback
    def _debug(self, kind: str, pcm: np.ndarray, heard: str, nsp: float | None = None, note: str = "") -> None:
        """VOICE_DEBUG: every transcription, missed ones too, with its loudness and no-speech score, to
        data/voice_debug.log — and the audio of each one with a name or a command in it, to
        data/voice_debug/ (the last DEBUG_CLIPS), so a miss can be heard again."""
        if not settings.voice_debug:
            return
        nsp_s = f" nsp {nsp:.2f}" if nsp is not None else ""
        clip = ""
        if kind in ("command", "second listen", "full model") or has_name(heard):
            clip = self._save_clip(f"{time.strftime('%H%M%S')}_{kind.replace(' ', '-')}", pcm)
        self._trace(f"{kind:13} {len(pcm) / RATE:4.1f}s {_db(pcm):6.1f} dBFS{nsp_s} | {heard}"
                    + (f"  → {note}" if note else "") + (f"  [{clip}]" if clip else ""))

    def _save_clip(self, name: str, pcm: np.ndarray) -> str:
        import wave

        folder = settings.data_dir / "voice_debug"
        try:
            folder.mkdir(exist_ok=True)
            path = folder / f"{name}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
                w.writeframes(pcm.astype("<i2").tobytes())
            for old in sorted(folder.glob("*.wav"), key=lambda q: q.stat().st_mtime)[:-DEBUG_CLIPS]:
                old.unlink(missing_ok=True)
            return path.name
        except OSError:
            return ""

    def _trace(self, line: str) -> None:
        """VOICE_DEBUG: one line in data/voice_debug.log — what was heard, or what was decided and why."""
        if not settings.voice_debug:
            return
        try:
            with open(settings.data_dir / "voice_debug.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass

    def _log(self, heard: str, did: str) -> None:
        self.history.append({"at": time.strftime("%H:%M:%S"), "heard": heard, "did": did})
        self.last = f"“{heard}” → {did}"

    def _tell(self, msg: str) -> None:
        if self._notify:
            try:
                self._notify(msg)
            except Exception:
                pass

    def _chime(self) -> None:
        """A two-note "heard you" (VOICE_CHIME=false to silence it)."""
        if not settings.voice_chime:
            return
        try:
            path = settings.data_dir / "voice_chime_v2.wav"  # v2: louder
            if not path.exists():
                import wave

                t = np.arange(int(RATE * 0.09)) / RATE
                fade = np.minimum(1, np.minimum(t, t[::-1]) * 60)
                notes = [np.sin(2 * np.pi * f * t) * fade * 0.45 for f in (880, 1320)]
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
                    w.writeframes((np.concatenate(notes) * 32767).astype("<i2").tobytes())
            p = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=_NOWINDOW)
            from .player import _set_level

            _set_level(p.pid, 1.0, wait_s=1.0)  # Windows may remember a dipped ffplay volume
        except Exception:
            pass


#: wake words Whisper (English mode) spells right unprompted; priming them only adds to its echo
_ENGLISH_WAKE = {"radio", "fm"}


def prompt() -> str:
    """Whisper's priming: the wake words it would not spell right by itself ("Bharti."), so it does, even
    over a song. Never a command: fed only music, Whisper writes its prompt back, and an echoed "Bharti,
    what's playing?" was obeyed in testing. Not "Radio" / "FM" either: a real "Radio." came back as
    "Radio, Fm." — the prompt's next word tacked on (caught in testing)."""
    names = [w for w in wake_words() if w not in _ENGLISH_WAKE] or wake_words()[:1] or ["bharti"]
    return ", ".join(w.capitalize() for w in names) + "."


def _babble(text: str) -> bool:
    """Whisper looping (it does on music): a word three times running, or a long run of few words."""
    toks = _tokens(text)
    if any(toks[i] == toks[i + 1] == toks[i + 2] for i in range(len(toks) - 2)):
        return True
    return len(toks) >= 8 and len(set(toks)) <= len(toks) // 3


def _only_names(text: str) -> bool:
    """Nothing but wake words ("Bharti, Radio."): Whisper's echo of its prompt on music, or a bare call."""
    toks = [t for t in _tokens(text) if t not in _LEAD]
    if not toks:
        return False
    i = 0
    while i < len(toks):  # "f m" is one name
        n = next((n for n in (1, 2, 3) if i + n <= len(toks) and _name_score(toks[i:i + n])), 0)
        if not n:
            return False
        i += n
    return True


def _echo(text: str, nsp: float) -> bool:
    """Whisper writing its prompt back (it does on plain music): nothing but the names it was primed with
    ("Bharti."), said more than once, or once where Whisper itself thinks there was likely no speech. A
    name it was not primed with ("Fm.") cannot be an echo — it was heard."""
    if not _only_names(text):
        return False
    primed = {_norm(w) for w in _tokens(prompt())}
    names = [t for t in _tokens(text) if t not in _LEAD]
    if any(_wake_of(t) not in primed for t in names):
        return False  # a name Whisper was not primed with: heard, not echoed
    return len(names) >= 2 or nsp >= ECHO_NSP


def _sure_call(text: str) -> bool:
    """Only names, one Whisper was not primed with among them ("Fm."): heard, not its prompt written back."""
    return _only_names(text) and not _echo(text, 1.0)


def _commanded(asked: str) -> bool:
    """Does a command the player would act on follow the name?"""
    cmd = parse(asked) if asked.strip() else None
    return cmd is not None and confident(asked, cmd) and (cmd.action != "play" or playable(cmd.focus) is not None)


def _wake_of(token: str) -> str:
    """Which wake word a token spells ("bharati" → "bharti", "fm" → "fm"); the token itself if none."""
    t = _norm(token)
    for w in wake_words():
        forms = SPELLINGS.get(_norm(w), (_norm(w),))
        if t in forms or (len(t) >= 4 and any(len(f) >= 4 and difflib.SequenceMatcher(None, t, f).ratio() >= 0.8
                                                for f in forms)) or lexicon.heard_name(t, _norm(w)):
            return _norm(w)
    return t


def mentions_dexter(text: str, anywhere: bool = True) -> bool:
    """ "Dexter, …" — at the start, or (`anywhere`, a window of music) after whatever was playing."""
    toks = [t for t in _tokens(text) if t not in _LEAD]
    spots = toks if anywhere else toks[:1]
    return any(difflib.SequenceMatcher(None, _norm(t), n).ratio() >= 0.8 for t in spots for n in DIP_FOR)
