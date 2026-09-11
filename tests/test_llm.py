import pytest
from pydantic import BaseModel

from mastgaane import config
from mastgaane.resolver import llm as L


class Out(BaseModel):
    keep: bool
    genre: str


def test_extract_json_handles_fences_and_prose():
    assert L._extract_json('```json\n{"keep": true, "genre": "ghazal"}\n```') == {"keep": True, "genre": "ghazal"}
    assert L._extract_json('Sure! {"keep": false, "genre": "other"} hope that helps') == {"keep": False, "genre": "other"}


def test_provider_order_and_fallback(monkeypatch):
    monkeypatch.setattr(config.settings, "llm_provider", "ollama")
    monkeypatch.setattr(config.settings, "llm_fallback", True)
    monkeypatch.setitem(L._status, "ollama", True)
    monkeypatch.setitem(L._status, "openai", False)
    monkeypatch.setitem(L._status, "claude", True)
    assert L.providers() == ["ollama", "claude"]

    calls = []

    def bad(system, user, out):
        calls.append("ollama")
        raise L.LLMUnavailable("boom")

    def good(system, user, out):
        calls.append("claude")
        return out(keep=True, genre="sufi")

    monkeypatch.setitem(L._BACKENDS, "ollama", (lambda: True, bad))
    monkeypatch.setitem(L._BACKENDS, "claude", (lambda: True, good))
    v = L.complete_json("s", "u", Out)
    assert v.genre == "sufi" and calls == ["ollama", "claude"]


def test_none_provider_raises(monkeypatch):
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    assert L.providers() == []
    with pytest.raises(L.LLMUnavailable):
        L.complete_json("s", "u", Out)


def test_ollama_request_shape(monkeypatch):
    """The Ollama call must use constrained JSON output (format=schema) and our model."""
    import httpx

    monkeypatch.setattr(config.settings, "llm_model", "qwen2.5:7b")
    seen = {}

    def fake_post(url, json, timeout):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"message": {"content": '{"keep": true, "genre": "ghazal"}'}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(L.httpx, "post", fake_post)
    v = L._ollama_json("sys", "usr", Out)
    assert v.keep and v.genre == "ghazal"
    assert seen["url"].endswith("/api/chat")
    assert seen["body"]["model"] == "qwen2.5:7b"
    assert seen["body"]["format"]["properties"].keys() == {"keep", "genre"}
    assert seen["body"]["stream"] is False
