"""Fetch a short audio clip for a reel (for fingerprinting / transcription)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx

from ..config import settings
from ..db import Reel

COOKIES_FILE = settings.data_dir / "cookies.txt"


def _ffmpeg_clip(src: Path, dst: Path, seconds: int = 25) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", str(seconds), "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(dst)],
        check=True,
    )
    return dst


def _download_direct(url: str, dst: Path) -> bool:
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return False
            with open(dst, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dst.stat().st_size > 10_000
    except Exception:
        return False


def _download_ytdlp(reel_url: str, dst: Path) -> bool:
    try:
        import yt_dlp
    except ImportError:
        return False
    opts = {
        "outtmpl": str(dst.with_suffix("")) + ".%(ext)s",
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([reel_url])
    except Exception:
        return False
    for p in dst.parent.glob(dst.stem + ".*"):
        if p.suffix != ".mp3":
            p.rename(dst.with_suffix(p.suffix))
            return True
    return False


def get_audio_clip(reel: Reel, seconds: int = 25) -> Path | None:
    """Return path to a short mp3 for the reel, downloading if needed. None if unavailable."""
    out = settings.audio_dir / f"{reel.shortcode}.mp3"
    if out.exists():
        return out
    raw = settings.audio_dir / f"{reel.shortcode}.raw.mp4"
    ok = False
    if reel.video_url:
        ok = _download_direct(reel.video_url, raw)  # CDN url, valid for a few hours after harvest
    if not ok:
        raw = settings.audio_dir / f"{reel.shortcode}.raw"
        ok = _download_ytdlp(reel.url, raw)
        cands = [p for p in settings.audio_dir.glob(f"{reel.shortcode}.raw*") if p.is_file()]
        raw = cands[0] if cands else raw
    if not ok or not raw.exists():
        return None
    try:
        _ffmpeg_clip(raw, out, seconds)
    except Exception:
        return None
    finally:
        try:
            raw.unlink()
        except Exception:
            pass
    return out
