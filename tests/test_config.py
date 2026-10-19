def test_an_empty_inherited_variable_does_not_blank_the_env_file(tmp_path, monkeypatch):
    """Dexter launches `mastgaane serve` with its own environment, where an unset key is GEMINI_API_KEY=""."""
    from mastgaane.config import Settings

    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=abc123\nJOCKEY_VOICE=hi-IN-SwaraNeural\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("JOCKEY_VOICE", "")
    s = Settings(_env_file=env)
    assert s.gemini_api_key == "abc123" and s.jockey_voice == "hi-IN-SwaraNeural"
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert Settings(_env_file=env).gemini_api_key == "from-env"  # a real value still wins
