import time

import numpy as np
import pytest

from test_player import db  # noqa: F401  (fixture)


@pytest.fixture()
def wake(monkeypatch):
    from mastgaane import config

    monkeypatch.setattr(config.settings, "voice_wake_words", "bharti,radio,fm")
    monkeypatch.setattr(config.settings, "voice_chime", False)
    monkeypatch.setattr(config.settings, "voice_debug", False)


# ----------------------------------------------------------------------------- the name
@pytest.mark.parametrize("said, asked", [
    ("Bharti, next song.", "next song"),
    ("Hey Bharati, play some ghazals", "play some ghazals"),
    ("Radio, pause.", "pause"),
    ("FM, stop.", "stop"),
    ("F.M. agla gaana", "agla gaana"),
    ("Bharthi agla gaana chalao", "agla gaana chalao"),
    ("भारती, गाना बंद करो", "गाना बंद करो"),
    ("Bharti", ""),
    # how Whisper misheard the owner live (17 Oct), and how it may
    ("Marthi.", ""),
    ("Bhaarati, Leharman, Chalisa.", "leharman chalisa"),
    ("Marty, next song", "next song"),
    ("Smartie, next song", "next song"),
    ("Bar tea, agla gaana", "agla gaana"),
    ("Bharathi, stop", "stop"),
])
def test_wake_words(wake, said, asked):
    from mastgaane.voice_control import split_wake

    assert split_wake(said) == asked


@pytest.mark.parametrize("said", ["We had a party last night", "Play some ghazals", "Mastgaane, next song",
                                  "Thank you.", "The radio station was closed today for repairs",
                                  "Next song, Bharti.",  # the name comes first
                                  "Hari, Deepa, Netsi", "Femme, stop",
                                  # near "Bharti" by letters, but everyday words and lyrics
                                  "Party all night", "Pretty song", "Bharat mata ki jai", "Hum tum pe marte hain",
                                  "Aarti sunao", "Bhakti sangeet", "Bright lights", "Smart TV band karo"])
def test_not_for_us(wake, said):
    from mastgaane.voice_control import split_wake

    assert split_wake(said) is None


def test_mastgaane_can_be_a_wake_word_again(wake, monkeypatch):
    from mastgaane import config
    from mastgaane.voice_control import split_wake

    monkeypatch.setattr(config.settings, "voice_wake_words", "bharti,mastgaane")
    assert split_wake("Must gaane, next song") == "next song"


def test_over_music_the_name_can_come_anywhere(wake):
    from mastgaane.voice_control import find_wake

    assert find_wake("isn't it? Bharti, next song.") == "next song"
    assert find_wake("and stage and that's Bharti.") == ""
    assert find_wake("All right, let's do it inside.") is None


def test_a_possessive_is_not_a_call(wake):
    """Live: a video's "How do you feel about Bharti's studio?" played Coke Studio."""
    from mastgaane.voice_control import find_wake, split_wake

    assert find_wake("How do you feel about Bharti's studio?") is None
    assert split_wake("Bharti's studio") is None


def test_a_word_every_other_title_has_is_no_wish(db, wake):
    from mastgaane.voice_control import playable

    with db.session() as s:
        s.add(db.Song(title="Arz Kiya Hai | Coke Studio Bharat", artist="Anuv Jain", norm_key="cs", status="approved",
                      yt_video_id="csvid000001"))
        s.commit()
    assert playable("studio") is None
    assert playable("arz kiya hai") == "arz kiya hai"


def test_a_misheard_name_counts_only_where_a_call_would_be(wake):
    from mastgaane.voice_control import find_wake

    assert find_wake("and stage and that's Marty, next song.") == "next song"
    assert find_wake("so calm down Marthi") == ""
    assert find_wake("he is such a smarty pants") is None  # inside a lyric


def test_a_second_listen_finds_the_name_in_any_form(wake):
    from mastgaane.voice_control import has_name

    assert all(has_name(t) for t in ("Bharti.", "Party.", "Marty", "Bharat", "Hey Bhakti!", "I mark the smarty one"))
    assert not any(has_name(t) for t in ("", "Thank you.", "I'm going to slide.", "Hello there",
                                         "I'm not a part of it", "What part of the store", "a smart one"))


def test_a_lone_name_whisper_was_unsure_of_is_heard_again(wake, monkeypatch):
    from mastgaane import voice_control as vc

    v = vc.VoiceControl(player=lambda: None)
    heard = iter([("Bharti.", 0.6), ("Marty.", 0.5)])  # primed (unsure), then unprimed
    monkeypatch.setattr(v, "hear", lambda *a, **k: next(heard))
    armed = []
    monkeypatch.setattr(v, "arm", lambda prefill_from=None: armed.append(prefill_from))
    monkeypatch.setattr(v, "_host_talking", lambda: False)
    v.sentence(np.zeros(16000, np.int16))
    assert armed == [None]

    heard = iter([("Bharti.", 0.6), ("I'm going to slide.", 0.5)])  # unprimed: no name — the prompt's echo
    armed.clear()
    v.sentence(np.zeros(16000, np.int16))
    assert armed == []


@pytest.mark.parametrize("heard, nsp, unprimed, opens", [
    # live on the Yeti, 17 Oct: the song, primed — the unprimed listen hears no name, no listening window
    ("Bharti.", 0.18, "I'm so glad I got this", False),
    ("Hello, Bharti.", 0.55, "I know you've got", False),
    ("Bharti is here.", 0.45, "Thank you very much", False),
    ("I'm not a part of it, Bharti.", 0.3, "I'm not a part of it", False),  # "part" is not the name
    ("Bharti.", 0.04, "Papa, doctor has called.", False),  # Hawa Mahal: Whisper as sure as of a real call
    ("Hey Bharti.", 0.3, "Hey Bhakti!", True),  # unsure, but heard again in some form
])
def test_over_music_a_name_without_a_command_is_heard_again(db, wake, heard, nsp, unprimed, opens):
    p, vc = control()
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: (heard, nsp) if primed else (unprimed, 0.5)
    vc.screen(np.zeros(48000, np.int16), end=1000)
    assert vc.listening is opens


@pytest.mark.parametrize("text, nsp, echo", [
    ("Bharti, Bharti.", 0.2, True),  # the prompt ("Bharti.") written back, more than once
    ("Bharti, Radio.", 0.2, False),  # "Radio" was not in the prompt: heard, not echoed
    ("Fm.", 0.54, False),  # caught in testing: a real "FM." with a so-so no-speech score
    ("Bharti.", 0.7, True),  # one name, but Whisper itself thinks there was no speech
    ("Bharti.", 0.08, False),  # a real call
    ("Bharti, Stop.", 0.9, False),  # not names only: never an echo
])
def test_prompt_echoes(wake, text, nsp, echo):
    from mastgaane.voice_control import _echo

    assert _echo(text, nsp) is echo


@pytest.mark.parametrize("asked, action, focus", [
    ("next song", "next", ""), ("skip", "next", ""), ("agla gaana chalao", "next", ""), ("change this song", "next", ""),
    ("play the next song", "next", ""), ("aglah gana chalaw", "next", ""),
    ("pause", "pause", ""), ("ruko", "pause", ""),
    ("stop", "stop", ""), ("gaana band karo", "stop", ""), ("stop the music", "stop", ""),
    ("resume", "resume", ""), ("play", "resume", ""), ("chalao", "resume", ""),
    ("i like this song", "like", ""), ("badhiya hai", "like", ""),
    ("i don't like this", "dislike", ""), ("never play this again", "dislike", ""), ("hatao ise", "dislike", ""),
    ("what's playing", "now", ""), ("ye kaun sa gaana hai", "now", ""),
    ("play some ghazals", "play", "ghazals"), ("hawa mahal sunao", "play", "hawa mahal"),
    ("jagjit singh ke gaane chalao", "play", "jagjit singh"), ("play something sad", "play", "sad"),
    ("jagjit singh's song", "play", "jagjit singh"),
    ("purane gaane lagao", "play", "purane"), ("bhajan sunao", "play", "bhajan"), ("gajal sunao", "play", "gajal"),
    ("mute", "mute", ""), ("mute karo", "mute", ""), ("awaaz band karo", "mute", ""), ("aawaz band kar do", "mute", ""),
    ("sound off", "mute", ""), ("muted", "mute", ""), ("silence", "mute", ""),
    ("unmute", "unmute", ""), ("un mute", "unmute", ""), ("awaaz kholo", "unmute", ""), ("awaaz chalu karo", "unmute", ""),
    ("sound on", "unmute", ""), ("unmute and play ghazals", "play", "ghazals"),
    ("khamoshi sunao", "play", "khamoshi"),  # a song, not "khamosh" (quiet)
])
def test_commands(asked, action, focus):
    from mastgaane.voice_control import parse

    cmd = parse(asked)
    assert cmd is not None and (cmd.action, cmd.focus) == (action, focus), asked


def test_caught_in_the_room_simulation(db, wake):
    """"Chazil sunao" became "chal sunao" (a piece of "kya chal raha") and then the mood "calm"; a real
    "Radio." came back "Radio, Fm." with the prompt's next word tacked on."""
    from mastgaane.voice_control import canon, parse, playable, prompt

    assert canon("Chazil Sunao") == "chazil sunao" and canon("aglah gana chalaw") == "agla gaana chalao"
    cmd = parse("Chazil Sunao")
    from mastgaane import jockey

    assert cmd.action == "play" and jockey.resolve_focus(playable(cmd.focus)) == ("genre", "ghazal")
    assert jockey.resolve_focus(playable(parse("Fazil sunau").focus)) == ("genre", "ghazal")  # was "film"
    assert jockey.resolve_focus(playable(parse("film songs chalao").focus)) == ("genre", "film")
    assert prompt() == "Bharti."  # Radio and FM are English: Whisper needs no help, and echoes less


def test_garbled_wishes_keep_the_word_they_know(db):
    from mastgaane.voice_control import playable

    assert playable("k s m gajal") and playable("ek sad gazal") and playable("kuch bhakti")
    assert playable("Jagjeet Singh") == "Jagjit Singh"
    assert playable("qwertyuiop") is None


# ----------------------------------------------------------------------------- hearing
def test_segmenter_cuts_a_sentence_and_drops_long_sounds():
    from mastgaane.voice_control import FRAME, RATE, Segmenter

    got, events = [], []
    seg = Segmenter(got.append, on_start=lambda: events.append("start"), on_cancel=lambda: events.append("cancel"))
    rng = np.random.default_rng(0)
    quiet = lambda s: (rng.normal(0, 30, int(RATE * s))).astype(np.int16)  # noqa: E731
    voice = lambda s: (np.sin(np.arange(int(RATE * s)) * 2 * np.pi * 220 / RATE) * 6000).astype(np.int16)  # noqa: E731
    seg.feed(quiet(2.0))
    seg.feed(voice(1.5))
    seg.feed(quiet(1.0))
    assert len(got) == 1 and 1.5 <= len(got[0]) / RATE <= 2.6 and events == ["start"]
    seg.feed(voice(10.0))  # music in the room: never a sentence
    seg.feed(quiet(1.0))
    assert len(got) == 1 and events == ["start", "start", "cancel"]
    seg.feed(voice(0.15))  # a knock
    seg.feed(quiet(1.0))
    assert len(got) == 1 and events[-1] == "cancel"
    assert FRAME == 480


def test_the_early_check_sees_the_start_of_each_sentence():
    from mastgaane.voice_control import RATE, Segmenter

    early, got = [], []
    seg = Segmenter(got.append, on_early=early.append, early_s=1.2)
    voice = (np.sin(np.arange(int(RATE * 3)) * 2 * np.pi * 220 / RATE) * 6000).astype(np.int16)
    quiet = np.random.default_rng(1).normal(0, 30, RATE).astype(np.int16)
    seg.feed(quiet)
    seg.feed(voice)
    seg.feed(quiet)
    assert len(early) == 1 and 1.3 <= len(early[0]) / RATE <= 1.7 and len(got) == 1  # pre-roll + 1.2 s
    seg.feed(voice[: RATE // 2])  # a short sound never reaches the check
    seg.feed(quiet)
    assert len(early) == 1


class FakePlayer:
    def __init__(self, phase="main", state="playing"):
        self.calls, self.phase, self.state = [], phase, state
        self.link_ended_at = -1e9

    def now(self):
        return {"state": self.state, "phase": self.phase, "kind": "song", "title": "Dil Ka Rishta", "artist": "Udit",
                "programme": "", "message": "", "wish": ""}

    def __getattr__(self, name):
        if name in ("skip", "pause", "resume", "stop", "like", "dislike", "duck", "mute"):
            return lambda *a, **k: self.calls.append((name, *a)) or self.now()
        if name == "play":
            return lambda focus="", **k: self.calls.append(("play", focus)) or {**self.now(), "wish": focus}
        raise AttributeError(name)


def control(player=None, heard=None, own_mic=True):
    """A VoiceControl on a fake player; `heard` = {(fast, primed): text} stands in for Whisper. `own_mic`:
    as if its mic were on (VOICE_CONTROL) — the tests feed the audio themselves; off, Dexter is the ear."""
    from mastgaane.voice_control import VoiceControl

    p = player or FakePlayer()
    vc = VoiceControl(lambda: p)
    vc.running = own_mic
    if heard is not None:
        vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: (
            heard.get((fast, primed), heard.get("any", "")), 0.05)
    return p, vc


def acted(p):
    return [c for c in p.calls if c[0] != "duck"]


# ----------------------------------------------------------------------------- the name, then the command
def test_one_breath_command_is_done_at_once(db, wake):
    p, vc = control(heard={"any": "Bharti, next song."})
    assert vc.sentence(np.zeros(8000, np.int16)) == "Next song" and acted(p) == [("skip",)]
    assert not vc.listening


def test_the_name_alone_opens_the_listening_window(db, wake):
    p, vc = control(heard={"any": "Bharti."})
    assert vc.sentence(np.zeros(8000, np.int16)) is None
    assert vc.listening and p.calls == [("duck",)]  # dipped, waiting for the command
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: ("gajal sunao", 0.05)
    assert vc.command(np.zeros(16000, np.int16)) == "Playing gajal" and acted(p) == [("play", "gajal")]
    assert not vc.listening


def test_the_command_may_repeat_the_name(db, wake):
    p, vc = control(heard={"any": "Bharti, stop."})
    assert vc.command(np.zeros(16000, np.int16)) == "Stopped"


def test_a_window_of_music_with_the_name_inside(db, wake):
    p, vc = control(heard={"any": "a lot to do in my mind, isn't it? Bharti, stop."})
    assert vc.screen(np.zeros(48000, np.int16), end=1000) == "Stopped" and ("stop",) in p.calls
    assert vc.screen(np.zeros(48000, np.int16), end=1050) is None  # the overlapping window: not again


def test_the_name_at_a_windows_end_waits_for_the_rest(db, wake):
    p, vc = control(heard={"any": "and that's it. Bharti, gha"})
    assert vc.screen(np.zeros(48000, np.int16), end=1000) is None and vc.listening


def test_a_lyric_holding_a_name_is_ignored(db, wake):
    p, vc = control(heard={"any": "I heard it on the radio all night long and the stars were bright"})
    assert vc.screen(np.zeros(48000, np.int16), end=1000) is None
    assert not vc.listening and acted(p) == []


def test_a_prompt_echo_never_becomes_a_command(db, wake):
    """Caught live: on plain music the primed model wrote back "Bharti, Radio.", that opened the
    listening window, and a lyric ("The best night of the day") changed the song."""
    # heard again without the prompt, there is no name in it: the prompt's echo
    p, vc = control(heard={(True, True): "Bharti, Bharti.", (True, False): "I'm going to slide."})
    assert vc.screen(np.zeros(48000, np.int16), end=1000) is None and vc.sentence(np.zeros(8000, np.int16)) is None
    assert not vc.listening and p.calls == []
    # one name, "no speech" says Whisper, and nothing without the prompt
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: ("Bharti." if primed else "", 0.74)
    assert vc.screen(np.zeros(48000, np.int16), end=2000) is None and not vc.listening


def test_the_name_said_twice_is_a_call_not_a_wish(db, wake):
    """ "Bharti… Bharti!" heard again (unprimed) as a name: the window opens; the second name is not a
    wish (it was played as the genre "bhakti")."""
    p, vc = control(heard={(True, True): "Bharti, Bharti.", (True, False): "Marty, Marty."})
    assert vc.sentence(np.zeros(8000, np.int16)) is None
    assert vc.listening and acted(p) == []


def test_while_the_host_talks(db, wake):
    """Her greeting and intros may sound like our names: only stop / pause / next, and no window opens."""
    p, vc = control(player=FakePlayer(phase="link"), heard={"any": "FM mein aapka swagat hai"})
    assert vc.screen(np.zeros(48000, np.int16), end=1000) is None and acted(p) == []
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: ("Bharti.", 0.05)
    assert vc.screen(np.zeros(48000, np.int16), end=2000) is None and not vc.listening
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: ("Bharti, Hawa Mahal sunao.", 0.05)
    assert vc.screen(np.zeros(48000, np.int16), end=3000) is None and acted(p) == []
    vc.hear = lambda pcm, fast=False, primed=True, keep=False, early=False: ("Bharti, stop!", 0.05)
    assert vc.screen(np.zeros(48000, np.int16), end=4000) == "Stopped"
    p2 = FakePlayer()
    p2.link_ended_at = time.monotonic()  # she just stopped: her words are still in the windows
    _, vc2 = control(player=p2, heard={"any": "Bharti."})
    assert vc2.screen(np.zeros(48000, np.int16), end=1000) is None and not vc2.listening


def test_heard_twice_done_once(db, wake):
    """Dexter hands over what he heard; our own mic heard it too."""
    p, vc = control(heard={"any": "Bharti, next song."})
    assert vc.sentence(np.zeros(8000, np.int16)) == "Next song"
    assert vc.handle("Bharti, next song.") == "already done" and acted(p) == [("skip",)]
    vc._last_done = (vc._last_done[0], vc._last_done[1] - 10)
    assert vc.handle("Bharti, next song.") == "Next song" and acted(p) == [("skip",), ("skip",)]


def test_dexter_handing_over_just_the_name(db, wake):
    p, vc = control()  # our own mic on as well: it records the command
    assert vc.handle("Bharti.") is None and vc.listening and vc.awaiting


def test_dexter_is_the_ear(db, wake):
    """No mic of our own (VOICE_CONTROL off): "Bharti" alone opens the window — the chime, the music down —
    and the next sentence Dexter sends needs no name; then the music comes back up."""
    p, vc = control(own_mic=False)
    assert vc.handle("Bharti.") is None and vc.awaiting and not vc.listening  # nothing to record
    assert ("duck",) in p.calls
    assert vc.handle("agla gaana") == "Next song" and acted(p) == [("skip",)]
    assert not vc.awaiting and p.calls[-1] == ("duck", None)
    assert vc.handle("agla gaana") is None  # the window is shut: a sentence without the name is not ours


def test_heard_route_works_without_our_own_mic(db, wake, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    p, vc = control(own_mic=False)
    monkeypatch.setattr(web, "_voice", vc)
    local = TestClient(web.app, client=("127.0.0.1", 50000))
    assert local.post("/api/voice/heard", json={"text": "Bharti"}).json() == {"did": None, "listening": True}
    assert local.post("/api/voice/heard", json={"text": "mute karo"}).json()["did"].startswith("Muted")
    assert local.get("/api/voice").json()["ears"] == "Dexter"


def test_handle_runs_commands_and_ignores_the_rest(db, wake):
    told = []
    p, vc = control()
    vc._notify = told.append
    assert vc.handle("Bharti, play some ghazals") == "Playing ghazals" and p.calls[-1] == ("play", "ghazals")
    assert vc.handle("Bharti, what's playing?").startswith("▶ Dil Ka Rishta")
    n = len(p.calls)
    assert vc.handle("I was just saying the next song is nice") is None and len(p.calls) == n
    assert vc.handle("Bharti, play qwertyuiop").startswith("Didn't catch") and len(p.calls) == n
    vc._armed_until = time.monotonic() + 5  # the tray's "Speak a command": no name needed
    assert vc.handle("stop") == "Stopped" and p.calls[-1] == ("stop",)
    assert vc.handle("stop") is None  # once only
    assert told[-1] == "Stopped"


def test_mute_and_unmute_by_voice(db, wake):
    p, vc = control()
    assert vc.handle("Bharti, awaaz band karo").startswith("Muted") and p.calls[-1] == ("mute", True)
    assert vc.handle("Bharti, unmute") == "Sound back on" and p.calls[-1] == ("mute", False)
    p.phase = "link"  # her own voice is the noise: mute is obeyed while she talks
    assert vc.handle("Bharti, mute").startswith("Muted") and p.calls[-1] == ("mute", True)


# ----------------------------------------------------------------------------- recording the command
def _frames(db_level: float, seconds: float, seed=0):
    from mastgaane.voice_control import FRAME, RATE

    amp = 10 ** (db_level / 20) * 32768
    x = np.random.default_rng(seed).normal(0, amp, int(RATE * seconds)).astype(np.int16)
    return [x[i:i + FRAME] for i in range(0, len(x) - FRAME + 1, FRAME)]


def _loud_is_speech(monkeypatch):
    """Silero stands in as "loud frames are speech": the unit tests check the decisions around it (the
    room simulation in the PROGRESS notes ran the real one)."""
    from mastgaane import voice_control

    def speech(cap):
        dbs = [20 * np.log10(max(float(np.sqrt(np.mean((f.astype(np.float32) / 32768) ** 2))), 1e-6)) for f in cap.frames]
        loud = [i for i, d in enumerate(dbs) if d > -45]
        return [{"start": loud[0] * voice_control.FRAME, "end": (loud[-1] + 1) * voice_control.FRAME}] if loud else []

    monkeypatch.setattr(voice_control._Capture, "speech", speech)


def test_the_capture_records_until_you_stop(db, wake, monkeypatch):
    from mastgaane import voice_control

    monkeypatch.setattr(voice_control, "CHIME_GUARD_S", 0.0)
    _loud_is_speech(monkeypatch)
    p, vc = control()
    vc.arm()
    for f in _frames(-60, 0.4) + _frames(-30, 1.2, 1) + _frames(-60, 1.0, 2):
        vc.feed(f, seg=None)
    assert not vc.listening and vc._heard.get_nowait()[0] == "command"


def test_whisper_looping_on_music_is_nothing():
    from mastgaane.voice_control import _babble

    assert _babble("Bhajan. Bhajan. Bhajan. Bhajan.") and _babble("I'm sorry. I'm sorry. I'm sorry. I'm sorry.")
    assert not _babble("Bharti, next song.") and not _babble("play some old jagjit singh ghazals please")


def test_silence_after_the_name_closes_the_window(db, wake, monkeypatch):
    from mastgaane import voice_control

    monkeypatch.setattr(voice_control, "CHIME_GUARD_S", 0.0)
    monkeypatch.setattr(voice_control, "ARM_S", 0.3)
    _loud_is_speech(monkeypatch)
    p, vc = control()
    vc.arm()
    vc._dipped_at = time.monotonic()
    end = time.monotonic() + 1.0
    while vc.listening and time.monotonic() < end:
        for f in _frames(-60, 0.15):
            vc.feed(f, seg=None)
        time.sleep(0.05)
    assert not vc.listening and vc._heard.empty() and p.calls[-1] == ("duck", None)
    assert vc.history[-1]["did"].startswith("no command came")


# ----------------------------------------------------------------------------- the music dips
@pytest.mark.parametrize("said, dexter", [
    ("Dexter, play some", True), ("Hey Dexter", True), ("Let's talk about Saadet.", False), ("Party time", False),
])
def test_dexter_dips_the_music_too(wake, said, dexter):
    from mastgaane.voice_control import mentions_dexter

    assert mentions_dexter(said, anywhere=False) is dexter


def test_a_name_dips_until_handled(db, wake, monkeypatch):
    from mastgaane import voice_control

    p, vc = control()
    vc._dip()
    assert p.calls == [("duck",)]
    vc._undip()
    assert p.calls == [("duck",), ("duck", None)]
    vc._undip()  # nothing dipped: nothing to undo
    assert len(p.calls) == 2
    p.state = "paused"
    vc._dip()
    assert len(p.calls) == 2  # nothing to dip while nothing plays
    p.state = "playing"
    monkeypatch.setattr(voice_control, "DIP_MAX_S", 0.05)
    vc._dip()
    time.sleep(0.3)
    assert p.calls[-1] == ("duck", None)  # never left dipped


def test_one_sentence_heard_twice_skips_once(db, monkeypatch):
    """The player's own guard: Dexter and MastGaane's ears both hear "next song"."""
    from mastgaane.player import Player
    from test_player import fake_ffplay, wait_for

    fake_ffplay(monkeypatch, 30)
    p = Player(lambda: "http://127.0.0.1:1")
    p.play("ghazals", talk=False)
    assert wait_for(lambda: p.now()["phase"] == "main")
    calls = []
    monkeypatch.setattr(p, "_command", lambda cmd: calls.append(cmd) or p.now())
    p.skip()
    p.skip()
    assert calls == ["skip"]
    p._last_cmd["skip"] -= 10  # a real second "next" later on
    p.skip()
    assert calls == ["skip", "skip"]
    monkeypatch.undo()
    p.stop()


def test_duck_comes_back_by_itself(db):
    from mastgaane.player import Player

    p = Player(lambda: "http://127.0.0.1:1")
    p.duck(0.2, seconds=0.05)
    assert p._duck == 0.2
    time.sleep(0.3)
    assert p._duck is None
    p.duck(0.2, seconds=0.05)
    p.duck(0.3)  # a later duck takes over: the old timer must not undo it
    time.sleep(0.3)
    assert p._duck == 0.3
    p.duck(None)


def test_heard_route_is_local_only(db, wake, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    p, vc = control()
    vc.running = True
    monkeypatch.setattr(web, "_voice", vc)
    local = TestClient(web.app, client=("127.0.0.1", 50000))
    assert local.post("/api/voice/heard", json={"text": "Bharti, next song"}).json()["did"] == "Next song"
    assert TestClient(web.app, client=("192.168.1.9", 50000)).post("/api/voice/heard", json={"text": "x"}).status_code == 403
    assert local.get("/api/voice").json()["history"][0]["did"] == "Next song"
