"""The FM host's voice through Gemini TTS: a native Hindi woman's voice, told how to feel.

edge-tts (the free Microsoft voices) can only be nudged in rate, pitch and volume. Gemini TTS takes
the feeling in words ("soft and slow, aching and wistful") and speaks Hindi with it. GEMINI_TTS_VOICE
picks the voice: one of Google's native Hindi voices (GET /v1beta/voices?language_code=hi-IN, e.g.
hi-in-podcaster-9, a 25-year-old radio host from Delhi), a prebuilt one (Sulafat…), or "" for one
designed from VOICE_DESCRIPTION (saved in data/gemini_voice.json).

Free tier, per model: 3 requests a minute and 10 a day. GEMINI_TTS_MODEL lists the models to use in
turn (the 3.8 Flash and Flash-Lite both take the native Hindi voices and annotations); when one has
used its day, the next speaks. Any failure raises GeminiTTSError; the caller decides what happens then.
Needs GEMINI_API_KEY (https://aistudio.google.com/apikey).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections import defaultdict, deque

import httpx
from rich.console import Console

from .config import settings

console = Console()
BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 40.0
MAX_WAIT_S = 30.0  # longest wait for a free request slot before giving up on this link
FALLBACK_VOICE = "Sulafat"  # prebuilt "Warm" female voice, if designing one is not allowed
VOICE_DESCRIPTION = ("A young Indian woman in her mid-twenties, a late-night Hindi FM radio host: a sweet, warm, "
                     "melodious voice with a natural Hindustani accent, expressive and full of feeling.")

# how she says it, per feeling (jockey.emotion_of)
STYLE = {
    "excited": "bright, bubbly and excited, smiling as she speaks, quick and lively",
    "cheerful": "cheerful and sunny, smiling, light and playful",
    "warm": "warm, friendly and relaxed, like talking to an old friend",
    "nostalgic": "fond and dreamy, gently nostalgic and a little slower, remembering the old radio days",
    "tender": "tender, intimate and warm, soft and close to the microphone",
    "calm": "calm, soothing and unhurried, soft and peaceful",
    "wistful": "soft and slow, aching and wistful, with a gentle sigh, like introducing a sad ghazal at midnight",
}


class GeminiTTSError(RuntimeError):
    pass


_cool: dict[str, float] = {}  # model → until when it rests (its day used up, or it failed)
_sent: dict[str, deque] = defaultdict(deque)  # model → when its last requests went out
_pace = threading.Lock()
_design_failed = False  # designing a voice was refused once: use the prebuilt one for this run


def models() -> list[str]:
    return [m.strip() for m in settings.gemini_tts_model.split(",") if m.strip()]


def enabled() -> bool:
    """Gemini is the host's voice (a key is set and JOCKEY_TTS is not edge) — even while it rests."""
    return (settings.jockey_tts or "auto").lower() != "edge" and bool(settings.gemini_api_key)


def available() -> bool:
    """Enabled, and some model is not resting."""
    now = time.monotonic()
    return enabled() and any(_cool.get(m, 0.0) <= now for m in models())


def style(emotion: str, late: bool = False) -> str:
    s = "A Hindi FM radio host speaking natural, flowing Hindi, " + STYLE.get(emotion, STYLE["warm"])
    return s + (", hushed because it is late at night" if late else "")


def retry_after(msg: str) -> float | None:
    """Seconds in Google's "Please retry in 5h32m48s" / "retry in 23.4s"; None when it does not say."""
    m = re.search(r"retry in ((?:\d+(?:\.\d+)?[hms])+)", msg)
    if not m:
        return None
    unit = {"h": 3600, "m": 60, "s": 1}
    return sum(float(n) * unit[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)([hms])", m.group(1)))


class _Limited(GeminiTTSError):
    """This model is out of requests for now; another may not be."""


def _wait_for_a_slot(model: str) -> None:
    """Keep each model under GEMINI_TTS_RPM: wait for a slot if it is near (the next link is usually made
    while a song plays), else give up on this model for this link."""
    with _pace:
        sent, now = _sent[model], time.monotonic()
        while sent and now - sent[0] >= 60:
            sent.popleft()
        if len(sent) >= max(1, settings.gemini_tts_rpm):
            wait = 60.5 - (now - sent[0])
            if wait > MAX_WAIT_S:
                raise _Limited(f"{model}: {len(sent)} requests in the last minute; next slot in {wait:.0f}s")
            time.sleep(max(0.0, wait))
            sent.popleft()
        sent.append(time.monotonic())


def _post(path: str, body: dict) -> dict:
    model = body.get("model") or body.get("voice", {}).get("model", "")
    _wait_for_a_slot(model)
    try:
        r = httpx.post(f"{BASE}/{path}", json=body, timeout=TIMEOUT,
                       headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        _cool[model] = time.monotonic() + 120
        raise GeminiTTSError(f"Gemini TTS unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        if r.status_code == 429:  # "retry in 23.4s" = the minute's limit, "5h32m48s" = the day's
            _cool[model] = time.monotonic() + (retry_after(msg) or 3600) + 2
            raise _Limited(f"{model} 429: {msg}")
        _cool[model] = time.monotonic() + 120
        raise GeminiTTSError(f"Gemini TTS {r.status_code}: {msg}")
    return r.json()


def _voice_file():
    return settings.data_dir / "gemini_voice.json"


def design_voice(description: str = VOICE_DESCRIPTION) -> dict:
    """Design the host's voice from a description; saves {id, description, model} and returns it (with a
    `sample` WAV in bytes when Google sends one)."""
    model = models()[0]
    d = _post("voices", {"store": True, "voice": {
        "model": model, "type": "prompted", "display_name": "MastGaane FM host",
        "gender": "female", "language_code": "hi-IN", "prompted": {"input": description}}})
    if not str(d.get("id", "")).startswith("voice_"):
        raise GeminiTTSError(f"Gemini did not return a voice id: {str(d)[:200]}")
    saved = {"id": d["id"], "description": description, "model": model}
    _voice_file().write_text(json.dumps(saved, indent=2), encoding="utf-8")
    sample = (d.get("sample_audio") or {}).get("data")
    return {**saved, "sample": base64.b64decode(sample) if sample else None}


def voice() -> str:
    """GEMINI_TTS_VOICE, else the designed voice (designed now if there is none yet), else a prebuilt one."""
    global _design_failed
    if settings.gemini_tts_voice:
        return settings.gemini_tts_voice
    f = _voice_file()
    if f.exists():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            if saved.get("id"):
                return saved["id"]
        except (ValueError, OSError):
            pass
    if _design_failed:
        return FALLBACK_VOICE
    try:
        return design_voice()["id"]
    except GeminiTTSError as e:
        _design_failed = True  # designing is optional; a prebuilt voice may still speak
        _cool.pop(models()[0], None)
        console.log(f"[yellow]gemini tts: could not design a voice ({e}); using {FALLBACK_VOICE}[/]")
        return FALLBACK_VOICE


def synthesize(text: str, emotion: str, late: bool = False, voice_id: str | None = None) -> bytes:
    """WAV (24 kHz mono) of the text, said with the feeling, by the first model that has requests left."""
    vid = voice_id or voice()
    why = []
    for model in models():
        if _cool.get(model, 0.0) > time.monotonic():
            why.append(f"{model} resting")
            continue
        try:
            d = _post("interactions", {
                "model": model,
                "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                           "annotations": [{"type": "speech_metadata", "style": style(emotion, late)}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": vid}]},
            })
        except _Limited as e:
            why.append(str(e)[:120])
            continue
        audio = [c for s in d.get("steps", []) if s.get("type") == "model_output"
                 for c in s.get("content", []) if c.get("type") == "audio" and c.get("data")]
        if not audio:
            raise GeminiTTSError(f"{model} sent no audio")
        return base64.b64decode(audio[-1]["data"])
    raise GeminiTTSError("no Gemini TTS model has requests left: " + "; ".join(why))
