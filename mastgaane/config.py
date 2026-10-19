from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # env_ignore_empty: Dexter starts `serve` with its own environment, where an unset key is an empty
    # GEMINI_API_KEY= — that must not blank out the key in our .env
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore",
                                      env_ignore_empty=True)

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
    llm_model: str = "gemma4:e4b-it-qat"
    # `mastgaane library` labels thousands of songs: the 12B model, whatever LLM_MODEL is — the 4B one
    # called every Bengali song Hindi (17 Oct)
    library_llm_model: str = "gemma-4-12b:latest"
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
    # MastGaane FM announcer: hi-IN-SwaraNeural (female), hi-IN-MadhurNeural (male) or mr-IN-AarohiNeural (female,
    # Marathi, reads Hindi well). en-IN-Neerja* and ur-IN-* voices give no audio for Devanagari text.
    jockey_voice: str = "hi-IN-SwaraNeural"
    # the host's speech engine: auto = Gemini TTS when GEMINI_API_KEY is set (an emotional, designed Indian
    # woman's voice; free tier), else edge-tts with JOCKEY_VOICE | gemini | edge. edge-tts also covers any
    # Gemini failure (quota, network).
    jockey_tts: str = "auto"
    gemini_api_key: str = ""
    # used in turn: when one has spent its free day (10 links), the next speaks
    gemini_tts_model: str = "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts"
    gemini_tts_rpm: int = 3  # requests a minute each model allows (free tier: 3)
    # when Gemini is the voice but cannot speak (its free day is used up): quiet = no link, the music
    # flows on | edge = JOCKEY_VOICE speaks instead
    jockey_fallback: str = "quiet"
    # the host talks before every Nth song (programmes, new finds, caught songs and the first one always);
    # 1 = before every item
    jockey_talk_every: int = 2
    # Start the show by itself as soon as `serve` is up (AUTOPLAY=true): MastGaane as a radio that is
    # simply on - the hour's programme or mix, Hanuman Chalisa first thing in the morning. Off by
    # default so a logon start does not play music unasked; Dexter's "play ..." starts it anyway.
    autoplay: bool = False
    # the first thing MastGaane plays each morning (5–12, once a day, unless you asked for something):
    # a YouTube Music search for it; "" = no opener
    morning_opener: str = "Hanuman Chalisa Hariharan"
    # after the opener, the first picks of a morning session (5-12) are bhajans - the owner's wish,
    # 18 Oct 2026: "in the morning I will prefer a few bhajans". 0 = just the hour's usual mix
    morning_bhajans: int = 3
    # "" = the voice designed for MastGaane (data/gemini_voice.json, made on first use); or a prebuilt
    # Gemini voice (Sulafat, Achernar, Vindemiatrix…) or another designed voice_… id
    gemini_tts_voice: str = ""
    # who writes the jockey's links: resident = only an Ollama model already in memory (else templates),
    # always = LLM_MODEL (may load it), never = templates only
    jockey_llm_links: str = "resident"
    ytmusic_auth_file: Path = ROOT / "browser.json"
    spotify_enabled: bool = False
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8788/callback"
    apple_enabled: bool = True
    apple_dev_token: str = ""  # MusicKit developer token (Apple Developer Program) — optional

    # Listen (Shazam inside MastGaane): part of a microphone's name ("Brio"), or "" = every real mic
    # records at once and the loudest is used
    listen_mic: str = ""
    listen_seconds: int = 10

    # `mastgaane serve` shows an icon in the Windows tray (pause / next / stop the music, quit)
    tray: bool = True

    # Voice commands ("Bharti, agla gaana"): Dexter is the ear — he hands over every sentence that starts
    # with a wake word (POST /api/voice/heard) — and voice_control = MastGaane's own mic as well. Off by
    # default: live (17 Oct) its CPU Whisper invented "Bharti" every few seconds over songs and Hawa Mahal,
    # while Dexter (GPU Whisper, owner-voice check) handed over only real calls.
    voice_control: bool = False
    voice_wake_words: str = "bharti,radio,fm"  # any of: bharti, radio, fm, mastgaane (or your own word)
    voice_mic: str = ""  # part of a mic's name; "" = LISTEN_MIC, else the loudest real mic
    voice_whisper_model: str = "small"  # same as Dexter's CPU model
    voice_screen_model: str = "base"  # looks for the wake word in music heard through the speakers
    voice_chime: bool = True  # a soft two-note "heard you"
    voice_debug: bool = False  # write every transcription (misses too) to data/voice_debug.log

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
