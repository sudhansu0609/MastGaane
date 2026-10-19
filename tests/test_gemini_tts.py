import base64
import io
import json
import math
import shutil
import struct
import time
import wave
from collections import defaultdict, deque
from datetime import datetime

import httpx
import pytest

DAY_USED = "Rate limit exceeded (limit: 10 requests per day on Free Tier). Please retry in 5h32m48s or upgrade"


def tone_wav(seconds: float = 0.6) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000)
        n = int(24000 * seconds)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 24000))) for i in range(n)))
    return buf.getvalue()


class FakeGemini:
    """Stands in for httpx.post against generativelanguage.googleapis.com; models in `spent` have used their day."""

    def __init__(self, design_status: int = 200):
        self.design_status, self.calls, self.spent = design_status, [], set()

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json))
        req = httpx.Request("POST", url)
        if url.endswith("/voices"):
            if self.design_status >= 400:
                return httpx.Response(self.design_status, json={"error": {"message": "no"}}, request=req)
            return httpx.Response(200, json={"id": "voice_abc123", "sample_audio": {
                "mime_type": "audio/wav", "data": base64.b64encode(tone_wav(0.2)).decode()}}, request=req)
        if json["model"] in self.spent:
            return httpx.Response(429, json={"error": {"message": DAY_USED}}, request=req)
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
            {"type": "audio", "data": base64.b64encode(tone_wav()).decode()}]}]}, request=req)


@pytest.fixture()
def gem(tmp_path, monkeypatch):
    from mastgaane import config, gemini_tts, jockey

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(config.settings, "jockey_tts", "auto")
    monkeypatch.setattr(config.settings, "jockey_fallback", "quiet")
    monkeypatch.setattr(config.settings, "gemini_tts_voice", "")
    monkeypatch.setattr(config.settings, "gemini_tts_model", "flash, lite")
    monkeypatch.setattr(config.settings, "gemini_tts_rpm", 100)
    monkeypatch.setattr(gemini_tts, "_cool", {})
    monkeypatch.setattr(gemini_tts, "_sent", defaultdict(deque))
    monkeypatch.setattr(gemini_tts, "_design_failed", False)
    monkeypatch.setattr(jockey, "VOICE_DIR", tmp_path / "voice")
    fake = FakeGemini()
    monkeypatch.setattr(gemini_tts.httpx, "post", fake)
    edge = []

    async def fake_edge(parts, voice):
        edge.append(voice)
        return b"edge-mp3"

    monkeypatch.setattr(jockey, "_say", fake_edge)
    return fake, edge


def test_enabled_needs_a_key(gem, monkeypatch):
    from mastgaane import config, gemini_tts

    assert gemini_tts.enabled() and gemini_tts.models() == ["flash", "lite"]
    monkeypatch.setattr(config.settings, "jockey_tts", "edge")
    assert not gemini_tts.enabled()
    monkeypatch.setattr(config.settings, "jockey_tts", "auto")
    monkeypatch.setattr(config.settings, "gemini_api_key", "")
    assert not gemini_tts.enabled()


def test_style_carries_the_feeling():
    from mastgaane import gemini_tts

    assert "wistful" in gemini_tts.style("wistful") and "Hindi" in gemini_tts.style("wistful")
    assert gemini_tts.style("excited", late=True).endswith("late at night")
    assert gemini_tts.style("unknown") == gemini_tts.style("warm")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_speak_uses_the_designed_gemini_voice_and_caches(gem):
    from mastgaane import gemini_tts, jockey

    fake, edge = gem
    key = jockey.speak("हाय… सुनिए।", "wistful", now=datetime(2026, 10, 16, 23, 30))
    mp3 = (jockey.VOICE_DIR / f"{key}.mp3").read_bytes()
    assert mp3[:3] == b"ID3" or mp3[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert [c[0] for c in fake.calls] == ["voices", "interactions"] and not edge
    body = fake.calls[1][1]
    assert body["model"] == "flash" and body["generation_config"]["speech_config"] == [{"voice": "voice_abc123"}]
    meta = body["input"][0]["content"][0]["annotations"][0]
    assert meta["type"] == "speech_metadata" and "wistful" in meta["style"] and "late at night" in meta["style"]
    assert json.loads(gemini_tts._voice_file().read_text())["id"] == "voice_abc123"
    assert jockey.speak("हाय… सुनिए।", "wistful", now=datetime(2026, 10, 16, 23, 30)) == key
    assert len(fake.calls) == 2  # cached clip, saved voice: no new calls
    assert jockey.speak("हाय… सुनिए।", "excited", now=datetime(2026, 10, 16, 23, 30)) != key  # feeling is in the key


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_spent_model_hands_over_to_the_next(gem, monkeypatch):
    from mastgaane import config, gemini_tts, jockey

    fake, edge = gem
    monkeypatch.setattr(config.settings, "gemini_tts_voice", "hi-in-podcaster-9")
    fake.spent.add("flash")
    assert jockey.speak("सुनिए।", "warm") and not edge
    assert [c[1]["model"] for c in fake.calls] == ["flash", "lite"]
    assert gemini_tts._cool["flash"] - time.monotonic() > 5 * 3600  # rests until Google's day turns over
    jockey.speak("अगला गीत।", "warm")
    assert [c[1]["model"] for c in fake.calls] == ["flash", "lite", "lite"]  # the spent one is not asked again


def test_when_every_model_is_spent_the_host_goes_quiet(gem, monkeypatch):
    from mastgaane import config, gemini_tts, jockey

    fake, edge = gem
    monkeypatch.setattr(config.settings, "gemini_tts_voice", "hi-in-podcaster-9")
    fake.spent |= {"flash", "lite"}
    assert jockey.speak("सुनिए।", "warm") is None and not edge
    assert not gemini_tts.available() and gemini_tts.enabled()
    n = len(fake.calls)
    assert jockey.speak("अगला गीत।", "warm") is None and len(fake.calls) == n  # resting: nothing sent

    item = jockey.Item(ref="song:1", kind="song", title="Hothon Se", artist="Jagjit Singh", audio="/a")
    monkeypatch.setattr(jockey, "prefetch_audio", lambda item: None)
    out = jockey.announce(item, n_picked=0, use_llm=False)
    assert out.link_text and out.link_audio == ""  # the text still shows; no voice, no Microsoft stand-in

    monkeypatch.setattr(config.settings, "jockey_fallback", "edge")
    key = jockey.speak("सुनिए।", "warm")
    assert (jockey.VOICE_DIR / f"{key}.mp3").read_bytes() == b"edge-mp3" and len(edge) == 1  # JOCKEY_FALLBACK=edge


def test_per_minute_limit_is_paced_not_punished(gem, monkeypatch):
    from mastgaane import config, gemini_tts

    fake, edge = gem
    monkeypatch.setattr(config.settings, "gemini_tts_voice", "Sulafat")
    monkeypatch.setattr(config.settings, "gemini_tts_model", "flash")
    monkeypatch.setattr(config.settings, "gemini_tts_rpm", 1)
    monkeypatch.setattr(gemini_tts, "MAX_WAIT_S", 0.0)
    gemini_tts.synthesize("एक।", "warm")
    with pytest.raises(gemini_tts.GeminiTTSError, match="last minute"):  # no slot: give up on this link
        gemini_tts.synthesize("दो।", "warm")
    assert len(fake.calls) == 1 and gemini_tts.available()  # nothing was sent, nothing rests

    monkeypatch.setattr(config.settings, "gemini_tts_rpm", 100)

    def per_minute(url, json=None, headers=None, timeout=None):
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded (limit: 3 requests per minute on "
                                                    "Free Tier). Please retry in 23.4s or upgrade"}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(gemini_tts.httpx, "post", per_minute)
    with pytest.raises(gemini_tts.GeminiTTSError):
        gemini_tts.synthesize("तीन।", "warm")
    assert 20 < gemini_tts._cool["flash"] - time.monotonic() < 30  # back after the retry time, not hours


def test_retry_after_reads_googles_wait():
    from mastgaane.gemini_tts import retry_after

    assert retry_after(DAY_USED) == 19968
    assert retry_after("Please retry in 23.4s or upgrade") == 23.4
    assert retry_after("Please retry in 5h30m or upgrade") == 19800
    assert retry_after("quota exceeded") is None


def test_the_host_talks_like_an_rj(monkeypatch):
    from mastgaane import config, jockey

    monkeypatch.setattr(config.settings, "jockey_talk_every", 2)

    def song(**kw):
        return jockey.Item(**{"ref": "song:1", "kind": "song", "title": "t", "artist": "a", "audio": "/a", **kw})

    assert [jockey.talks_before(song(), n) for n in range(5)] == [True, False, True, False, True]
    assert jockey.talks_before(song(discovery=True), 1) and jockey.talks_before(song(caught=True), 3)
    assert jockey.talks_before(jockey.Item(ref="yt:x", kind="episode", title="t", audio="/a"), 1)
    monkeypatch.setattr(config.settings, "jockey_talk_every", 1)
    assert all(jockey.talks_before(song(), n) for n in range(5))


def test_design_refused_uses_a_prebuilt_voice_once(gem):
    from mastgaane import gemini_tts

    fake, _ = gem
    fake.design_status = 403
    assert gemini_tts.voice() == gemini_tts.FALLBACK_VOICE
    assert gemini_tts.available()  # a refused design does not rest the model
    assert gemini_tts.voice() == gemini_tts.FALLBACK_VOICE
    assert [c[0] for c in fake.calls] == ["voices"]  # not retried on every link


def test_a_chosen_voice_wins(gem, monkeypatch):
    from mastgaane import config, gemini_tts

    monkeypatch.setattr(config.settings, "gemini_tts_voice", "Achernar")
    assert gemini_tts.voice() == "Achernar" and gem[0].calls == []
