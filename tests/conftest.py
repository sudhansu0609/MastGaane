import pytest


@pytest.fixture(autouse=True)
def _no_network_by_default(monkeypatch):
    """A test's first pick of the morning must not search YouTube Music for the morning opener, and no
    test may reach Gemini with the key in .env. Tests that want either set them back."""
    from mastgaane import config

    monkeypatch.setattr(config.settings, "morning_opener", "")
    monkeypatch.setattr(config.settings, "gemini_api_key", "")
