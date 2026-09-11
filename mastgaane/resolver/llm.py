"""LLM backend abstraction: Claude (cloud) or a local model (Ollama / LM Studio / any
OpenAI-compatible server). Every call is "system + user prompt → validated pydantic object".

Provider selection (LLM_PROVIDER in .env):
  auto     → local server if it answers, else Claude if a key exists, else None  (default)
  ollama   → Ollama native API   (LLM_BASE_URL default http://127.0.0.1:11434)
  openai   → OpenAI-compatible   (LM Studio http://127.0.0.1:1234/v1, llama.cpp, vLLM, ...)
  claude   → Anthropic API
  none     → no LLM: tier-3 guessing is skipped, classification uses rules.yaml keywords only
LLM_FALLBACK=true lets a failed local call retry on Claude (and vice versa).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from rich.console import Console

from ..config import settings

T = TypeVar("T", bound=BaseModel)
console = Console()
_status: dict[str, bool | None] = {"ollama": None, "openai": None, "claude": None}


class LLMUnavailable(RuntimeError):
    pass


# ----------------------------------------------------------------------------- helpers
def _schema(model: Type[BaseModel]) -> dict:
    s = model.model_json_schema()
    s["additionalProperties"] = False
    return s


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _ollama_base() -> str:
    return (settings.llm_base_url or "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1")


def _openai_base() -> str:
    base = (settings.llm_base_url or "http://127.0.0.1:1234/v1").rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


# ----------------------------------------------------------------------------- ollama
def _ollama_exe() -> str | None:
    cands = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]
    for c in cands:
        if c.exists():
            return str(c)
    return "ollama"  # hope it is on PATH


def ollama_alive(autostart: bool | None = None) -> bool:
    autostart = settings.llm_autostart if autostart is None else autostart
    url = f"{_ollama_base()}/api/tags"
    try:
        if httpx.get(url, timeout=2).status_code == 200:
            return True
    except Exception:
        pass
    if not autostart:
        return False
    try:  # `ollama serve` stays up after we exit; the GPU model unloads itself after idle
        subprocess.Popen([_ollama_exe(), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    for _ in range(30):
        time.sleep(1)
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                console.print("[dim]started ollama serve[/]")
                return True
        except Exception:
            continue
    return False


def _ollama_json(system: str, user: str, out: Type[T]) -> T:
    r = httpx.post(
        f"{_ollama_base()}/api/chat",
        json={
            "model": settings.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": _schema(out),  # constrained decoding → valid JSON for our schema
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
            "keep_alive": settings.llm_keep_alive,
        },
        timeout=settings.llm_timeout,
    )
    r.raise_for_status()
    return out.model_validate(_extract_json(r.json()["message"]["content"]))


# ----------------------------------------------------------------------------- openai-compatible
def openai_alive() -> bool:
    try:
        return httpx.get(f"{_openai_base()}/models", timeout=2).status_code == 200
    except Exception:
        return False


def _openai_json(system: str, user: str, out: Type[T]) -> T:
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "response_format": {"type": "json_schema", "json_schema": {"name": out.__name__, "schema": _schema(out), "strict": True}},
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key or 'local'}"}
    r = httpx.post(f"{_openai_base()}/chat/completions", json=body, headers=headers, timeout=settings.llm_timeout)
    if r.status_code == 400:  # server without json_schema support → plain JSON mode
        body["response_format"] = {"type": "json_object"}
        body["messages"][0]["content"] += f"\nAnswer ONLY with JSON matching this schema: {json.dumps(_schema(out))}"
        r = httpx.post(f"{_openai_base()}/chat/completions", json=body, headers=headers, timeout=settings.llm_timeout)
    r.raise_for_status()
    return out.model_validate(_extract_json(r.json()["choices"][0]["message"]["content"]))


# ----------------------------------------------------------------------------- claude
def claude_alive() -> bool:
    """True only if some credential exists: .env key, env var/token, or an `ant auth login` profile."""
    if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "anthropic"
    return cfg.exists() and any(cfg.iterdir())


def _claude_json(system: str, user: str, out: Type[T]) -> T:
    from .common import claude_client

    resp = claude_client().messages.parse(
        model=settings.claude_model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=out,
        output_config={"effort": "low"},
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise LLMUnavailable("claude gave no structured answer")
    return resp.parsed_output


# ----------------------------------------------------------------------------- dispatcher
_BACKENDS = {"ollama": (ollama_alive, _ollama_json), "openai": (openai_alive, _openai_json), "claude": (claude_alive, _claude_json)}


def _alive(name: str) -> bool:
    if _status[name] is None:
        _status[name] = _BACKENDS[name][0]()
    return bool(_status[name])


def providers() -> list[str]:
    """Ordered list of backends to try for this run."""
    p = settings.llm_provider.lower()
    if p in ("none", "off", ""):
        return []
    if p == "auto":
        order = [n for n in ("ollama", "openai", "claude") if _alive(n)]
    else:
        order = [p] if _alive(p) else []
        if settings.llm_fallback:
            order += [n for n in ("ollama", "openai", "claude") if n != p and _alive(n)]
    return order


def llm_available() -> bool:
    return bool(providers())


def complete_json(system: str, user: str, out: Type[T]) -> T:
    """Run the prompt on the first working provider; raise LLMUnavailable if none works."""
    errors = []
    for name in providers():
        try:
            result = _BACKENDS[name][1](system, user, out)
            console.log(f"[dim]llm:{name}[/]") if settings.llm_verbose else None
            return result
        except Exception as e:  # any backend failure → try the next provider
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            if settings.llm_verbose:
                console.log(f"[yellow]llm:{name} failed: {type(e).__name__}: {str(e)[:200]}[/]")
            # auth/config errors will not fix themselves this run; transient ones get re-probed
            _status[name] = False if isinstance(e, (PermissionError,)) or "401" in str(e) or "403" in str(e) else None
            if not settings.llm_fallback and settings.llm_provider.lower() != "auto":
                break
    raise LLMUnavailable("; ".join(errors) or "no LLM provider configured")
