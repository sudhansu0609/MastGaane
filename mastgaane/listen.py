"""Listen — Shazam inside MastGaane: hear a song, name it, keep it in the collection, play it.

Where the sound comes from:
- mic      — a microphone on this PC. ffmpeg records every real mic at once and the loudest take
             wins (Windows' default mic on this PC is a silent virtual device). Bluetooth hands-free
             mics ("Headset (…)") are left alone: opening one drops the headphones into call quality.
- pc       — what this PC itself is playing (loopback of the default speaker): a reel, a video.
- phone    — any audio or video file; the /listen page records one with the phone camera
             (`file` is the same from the command line).
- shortcut — the iPhone's own Shazam action already named it; only the saving happens here.

Shazam names it (AudD too when AUDD_API_KEY is set). The song is saved as approved — the listener
picked it — matched to YouTube so the players can play it, and MastGaane FM plays it next
(jockey.pick puts fresh catches first). Recordings live in a temp folder that is deleted at once.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rich.console import Console

from .config import settings
from .db import MANUAL, Catch, Song, get_song_by_key, session, set_manual_status, utcnow
from .resolver.common import clean_title, norm_key
from .resolver.fingerprint import audd_result, shazam_track

console = Console()
TIER_HEARD = 5
NOT_A_MIC = re.compile(r"virtual|oculus|stereo mix|cable|voicemeeter|steam streaming|^headset \(", re.I)
SILENCE_DB = -70.0  # RMS below this is a dead, unplugged or muted input (a quiet room reads -50…-90 on the Brio)
FAINT_DB = -50.0  # a miss quieter than this gets "move nearer" advice
RATE = 44100
WINDOW_S, STEP_S = 12, 10  # long phone takes are tried in overlapping windows
MAX_UPLOAD_S = 45
APPLE_STOREFRONT = "in"
_NOWINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_busy = threading.Lock()  # one recording at a time: two would fight over the same mics


class ListenError(RuntimeError):
    pass


@dataclass
class Heard:
    title: str
    artist: str
    album: str = ""
    year: int | None = None
    cover_url: str = ""
    shazam_url: str = ""
    apple_id: str = ""
    via: str = "shazam"


# ----------------------------------------------------------------------------- recording
def _dshow_audio() -> list[str]:
    try:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                             creationflags=_NOWINDOW).stderr
    except (OSError, subprocess.TimeoutExpired):
        return []
    return re.findall(r'"([^"]+)" \(audio\)', err)


def preferred(names: list[str], want: str) -> list[str]:
    """LISTEN_MIC / VOICE_MIC: a part of a mic's name, or several in order of preference ("yeti,brio": the
    Blue Yeti, else the Brio 100) — the first that is plugged in."""
    for w in (w.strip().lower() for w in want.split(",")):
        hit = [n for n in names if w and w in n.lower()]
        if hit:
            return hit
    return []


def mics() -> list[str]:
    """The microphones `mic` listens with: LISTEN_MIC's match, else every real one."""
    names = _dshow_audio()
    want = settings.listen_mic.strip().lower()
    if want:
        return preferred(names, want)
    return [n for n in names if not NOT_A_MIC.search(n)]


def level_db(path: Path) -> float:
    """RMS loudness of a 16-bit wav in dBFS (-inf for digital silence)."""
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    return 20 * float(np.log10(rms)) if rms > 0 else float("-inf")


def record_mics(seconds: int, folder: Path) -> list[tuple[Path, float, str]]:
    """Record every mic at once; (wav, dBFS, name) loudest first."""
    names = mics()
    if not names:
        which = f"matching LISTEN_MIC={settings.listen_mic!r}" if settings.listen_mic.strip() else "on this PC"
        raise ListenError(f"no microphone {which}")
    procs = []
    for i, name in enumerate(names):
        out = folder / f"mic{i}.wav"
        p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "dshow", "-i", f"audio={name}",
                              "-t", str(seconds), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(out)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=_NOWINDOW)
        procs.append((name, out, p))
    takes = []
    for name, out, p in procs:
        try:
            _, err = p.communicate(timeout=seconds + 20)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            continue
        if out.exists() and out.stat().st_size > 1000:
            takes.append((out, level_db(out), name))
        elif err:
            console.log(f"[yellow]listen: {name}: {err.decode(errors='replace').strip()[:200]}[/]")
    return sorted(takes, key=lambda t: t[1], reverse=True)


def _write_wav(path: Path, mono: np.ndarray) -> Path:
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return path


def record_pc(seconds: int, folder: Path) -> Path:
    """What the default speaker is playing right now (WASAPI loopback)."""
    import warnings

    try:
        import soundcard as sc

        speaker = sc.default_speaker()
        loop = sc.get_microphone(id=str(speaker.id), include_loopback=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "data discontinuity" while nothing is playing
            data = loop.record(samplerate=RATE, numframes=RATE * seconds)
    except Exception as e:
        raise ListenError(f"could not capture the PC's sound: {e}") from e
    return _write_wav(folder / "pc.wav", data.mean(axis=1) if data.ndim == 2 else data)


def normalise(src: Path, folder: Path) -> Path:
    """Any audio or video file → mono 16-bit wav (the first MAX_UPLOAD_S seconds)."""
    out = folder / "upload.wav"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-t", str(MAX_UPLOAD_S),
                        "-vn", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(out)],
                       capture_output=True, timeout=120, creationflags=_NOWINDOW)
    if r.returncode != 0 or not out.exists():
        raise ListenError("that file has no sound ffmpeg can read")
    return out


def windows(path: Path) -> list[Path]:
    """The recording itself if short, else overlapping WINDOW_S-second slices of it."""
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if len(a) <= (WINDOW_S + 3) * RATE:
        return [path]
    out = []
    for i, start in enumerate(range(0, len(a) - 4 * RATE, STEP_S * RATE)):
        out.append(_write_wav(path.with_name(f"{path.stem}.w{i}.wav"), a[start:start + WINDOW_S * RATE].astype(np.float32) / 32768))
    return out


# ----------------------------------------------------------------------------- naming
def _year(v) -> int | None:
    m = re.search(r"\b(19[3-9]\d|20\d\d)\b", str(v or ""))
    return int(m.group(1)) if m else None


def from_shazam(t: dict) -> Heard:
    meta = {m.get("title"): m.get("text", "") for sec in t.get("sections") or [] if sec.get("type") == "SONG"
            for m in sec.get("metadata") or []}
    apple = next((a.get("id") for a in (t.get("hub") or {}).get("actions") or [] if a.get("type") == "applemusicplay"), "")
    imgs = t.get("images") or {}
    return Heard(title=clean_title(t["title"]), artist=t.get("subtitle", ""), album=meta.get("Album", ""),
                 year=_year(meta.get("Released")), cover_url=imgs.get("coverarthq") or imgs.get("coverart") or "",
                 shazam_url=t.get("url", ""), apple_id=str(apple or ""), via="shazam")


def from_audd(r: dict) -> Heard:
    am = r.get("apple_music") or {}
    art = ((am.get("artwork") or {}).get("url") or "").replace("{w}", "600").replace("{h}", "600")
    return Heard(title=clean_title(r["title"]), artist=r.get("artist", ""), album=r.get("album", ""),
                 year=_year(r.get("release_date")), cover_url=art, shazam_url=r.get("song_link", ""),
                 apple_id=str((am.get("playParams") or {}).get("id") or ""), via="audd")


def identify(path: Path) -> Heard | None:
    """Name the song in a wav: Shazam on each window until one hits, then AudD (if configured)."""
    for clip in windows(path):
        t = shazam_track(clip)
        if t:
            return from_shazam(t)
    r = audd_result(path)
    return from_audd(r) if r else None


def apple_id_from_url(url: str) -> str:
    """The catalog song id in an Apple Music link (…/album/name/123?i=456 → 456, …/song/name/456 → 456)."""
    m = re.search(r"[?&]i=(\d+)", url or "") or re.search(r"/song/(?:[^/?]+/)?(\d+)", url or "")
    return m.group(1) if m else ""


# ----------------------------------------------------------------------------- keeping
def _youtube_id(title: str, artist: str) -> str | None:
    from .playlists.youtube_music import YouTubeMusicAdapter

    try:
        t = YouTubeMusicAdapter(anonymous=True).search(title, artist)
    except Exception as e:
        console.log(f"[yellow]listen: YouTube search failed: {e}[/]")
        return None
    return t.id if t else None


def classify_song(song_id: int, source: str) -> None:
    """Genre / language, so FM wishes ("ghazals", "bhojpuri") find it. The song stays approved
    whatever the classifier thinks — the listener chose it."""
    from .resolver.classify import classify

    with session() as s:
        song = s.get(Song, song_id)
        if song is None or song.genre:
            return
        title, artist = song.title, song.artist
    try:
        v = classify(title, artist)
    except Exception as e:
        console.log(f"[yellow]listen: could not classify {title}: {e}[/]")
        return
    with session() as s:
        song = s.get(Song, song_id)
        if song is not None:
            song.genre, song.language = v.genre, v.language
            song.classify_reason = f"{MANUAL}{song.status}; heard ({source}); {v.reason}"[:500]
            s.commit()


def _classify_later(song_id: int, source: str) -> None:
    """classify_song off the request: the LLM may take ~20 s."""
    threading.Thread(target=classify_song, args=(song_id, source), daemon=True, name="mastgaane-listen-classify").start()


def save(title: str, artist: str, source: str, heard: Heard | None = None, classify: bool = True) -> dict:
    """Keep a song the listener named: approved, YouTube-matched, logged as a Catch. Returns result().
    classify=True classifies a new song in the background; False leaves it to the caller (classify_song)."""
    title, artist = clean_title(title).strip(), (artist or "").strip()
    if not title:
        raise ListenError("no song title")
    heard = heard or Heard(title=title, artist=artist, via=source)
    key = norm_key(title, artist)
    with session() as s:
        known = get_song_by_key(s, key)
        need_yt = not (known and known.yt_video_id)
    yt = _youtube_id(title, artist) if need_yt else None  # network, outside any DB transaction
    with session() as s:
        song = get_song_by_key(s, key)
        created = song is None
        if created:
            song = Song(title=title, artist=artist, norm_key=key, source_tier=TIER_HEARD, confidence=1.0,
                        year=heard.year, classify_reason=f"heard ({source})")
            s.add(song)
        else:
            song.times_seen += 1
            song.last_seen = utcnow()
            song.year = song.year or heard.year
        if yt and not song.yt_video_id:
            song.yt_video_id = yt
        if heard.apple_id and not song.apple_id:
            song.apple_id = heard.apple_id
            song.apple_url = f"https://music.apple.com/{APPLE_STOREFRONT}/song/{heard.apple_id}"
            song.apple_name = f"{title} - {artist}".strip(" -")
        set_manual_status(song, "approved")
        s.commit()
        s.refresh(song)
        s.add(Catch(song_id=song.id, source=source, title=heard.title, artist=heard.artist,
                    cover_url=heard.cover_url, shazam_url=heard.shazam_url))
        s.commit()
        out = result(song, created, heard)
    if classify and created:
        _classify_later(out["song_id"], source)
    console.log(f"[green]listen ({source}):[/] {out['summary']}")
    return out


def result(song: Song, created: bool, heard: Heard) -> dict:
    who = song.title + (f" by {song.artist}" if song.artist else "")
    kept = "Saved to your collection" if created else "Already in your collection"
    tail = " — MastGaane FM plays it next." if song.yt_video_id else ", but I could not find it on YouTube to play."
    return {"found": True, "song_id": song.id, "title": song.title, "artist": song.artist, "created": created,
            "playable": bool(song.yt_video_id), "yt": song.yt_video_id, "cover_url": heard.cover_url,
            "shazam_url": heard.shazam_url, "album": heard.album, "year": song.year, "via": heard.via,
            "summary": f"That's {who}. {kept}{tail}"}


def miss(why: str = "") -> dict:
    return {"found": False, "summary": "I couldn't recognise that song" + (f" — {why}." if why else ". Try again nearer the music.")}


# ----------------------------------------------------------------------------- the whole thing
def catch(source: str = "mic", seconds: int | None = None, upload: Path | None = None, classify: bool = True) -> dict:
    """Record (or take `upload`), identify, save. Returns result() or miss(); ListenError when busy or broken."""
    if source not in ("mic", "pc", "phone", "file"):
        raise ListenError(f"unknown source {source!r}")
    if source in ("phone", "file") and upload is None:
        raise ListenError("a recording is needed")
    seconds = max(4, min(int(seconds or settings.listen_seconds), 30))
    if not _busy.acquire(blocking=False):
        raise ListenError("already listening")
    try:
        with tempfile.TemporaryDirectory(prefix="mastgaane-listen-") as tmp:
            folder = Path(tmp)
            if source == "mic":
                takes = [(p, db) for p, db, _ in record_mics(seconds, folder) if db > SILENCE_DB][:3]
                if not takes:
                    return miss("the microphone heard almost nothing (muted? set LISTEN_MIC to pick one)")
            elif source == "pc":
                clip = record_pc(seconds, folder)
                takes = [(clip, level_db(clip))]
                if takes[0][1] <= SILENCE_DB:
                    return miss("nothing is playing on this PC")
            else:
                takes = [(normalise(upload, folder), 0.0)]
            heard = next((h for h in (identify(p) for p, _ in takes) if h), None)
    finally:
        _busy.release()
    if heard is None:
        return miss("it was very faint — move nearer the music" if takes[0][1] < FAINT_DB else "")
    return save(heard.title, heard.artist, source, heard, classify)


SOURCE_LABEL = {"mic": "🎤 PC mic", "pc": "🔊 PC sound", "phone": "📱 phone", "shortcut": "📱 Shazam", "file": "📁 file"}


def recent(limit: int = 30) -> list[dict]:
    """The latest catches, newest first, as the song stands now (it may have been edited or rejected since)."""
    from datetime import timezone

    from sqlmodel import select

    out = []
    with session() as s:
        for c in s.exec(select(Catch).order_by(Catch.at.desc()).limit(limit)).all():
            song = s.get(Song, c.song_id) if c.song_id else None
            out.append({
                "song_id": c.song_id, "title": song.title if song else c.title, "artist": song.artist if song else c.artist,
                "status": song.status if song else "", "yt": song.yt_video_id if song else None,
                "cover": c.cover_url, "shazam": c.shazam_url, "source": SOURCE_LABEL.get(c.source, c.source),
                "when": c.at.replace(tzinfo=timezone.utc).astimezone().strftime("%d %b, %H:%M"),
            })
    return out
