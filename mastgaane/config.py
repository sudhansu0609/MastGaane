from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Harvester
    chrome_path: str = "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"
    chrome_profile_dir: Path = ROOT / "chrome-profile"
    chrome_debug_port: int = 9555
    headless: bool = False
    max_reels: int = 150
    max_minutes: int = 25

    # Resolver
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5"
    audd_api_key: str = ""
    whisper_model: str = "small"
    auto_approve_min_confidence: float = 0.8

    # LLM backend (see resolver/llm.py): auto | ollama | openai | claude
    llm_provider: str = "auto"
    llm_model: str = "qwen2.5:7b"
    llm_base_url: str = ""  # "" → ollama http://127.0.0.1:11434 / openai http://127.0.0.1:1234/v1
    llm_api_key: str = ""
    llm_fallback: bool = True
    llm_autostart: bool = True  # start `ollama serve` if it is installed but not running
    llm_keep_alive: str = "10m"
    llm_timeout: float = 120.0
    llm_verbose: bool = False

    # Playlists
    playlist_name: str = "MastGaane - Late Night"
    ytmusic_enabled: bool = True
    # MastGaane FM announcer: hi-IN-SwaraNeural (female) or hi-IN-MadhurNeural (male)
    jockey_voice: str = "hi-IN-SwaraNeural"
    ytmusic_auth_file: Path = ROOT / "browser.json"
    spotify_enabled: bool = False
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8788/callback"
    apple_enabled: bool = True
    apple_dev_token: str = ""  # MusicKit developer token (Apple Developer Program) — optional

    # Misc
    late_night_start: int = 23
    late_night_end: int = 4
    timezone: str = "Asia/Kolkata"

    data_dir: Path = ROOT / "data"
    reports_dir: Path = ROOT / "reports"
    rules_file: Path = ROOT / "rules.yaml"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mastgaane.db"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.reports_dir.mkdir(parents=True, exist_ok=True)
settings.audio_dir.mkdir(parents=True, exist_ok=True)
