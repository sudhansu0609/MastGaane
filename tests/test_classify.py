from mastgaane import config
from mastgaane.resolver import classify as C


def _v(**kw):
    return C.Verdict(**{"keep": True, "language": "hindi", "genre": "film", "reason": "x", **kw})


def test_gate_rejects_other_languages_and_genres(monkeypatch):
    monkeypatch.setattr(config.settings, "rules_file", config.ROOT / "does-not-exist.yaml")
    C.rules.cache_clear()
    assert C.gate(_v()).keep
    assert C.gate(_v(language="urdu", genre="ghazal")).keep
    assert not C.gate(_v(language="english")).keep
    assert not C.gate(_v(language="punjabi", genre="folk")).keep
    assert not C.gate(_v(genre="indie")).keep
    assert "language english" in C.gate(_v(language="english")).reason
    C.rules.cache_clear()


def test_rules_short_circuit_without_llm(monkeypatch):
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    monkeypatch.setattr(config.settings, "rules_file", config.ROOT / "does-not-exist.yaml")
    C.rules.cache_clear()
    assert C.classify("Chupke Chupke Raat Din", "Ghulam Ali").genre == "ghazal"
    assert not C.classify("Best comedy dialogue", "random").keep
    # no LLM + no rule: romanised-Hindi words make it hindi, otherwise unknown → rejected
    assert C.classify("Tum Hi Ho Mera Dil", "unknown", "tere bina").keep
    assert not C.classify("Perfect", "Ed Sheeran", "late night").keep
    C.rules.cache_clear()


def test_guess_language_scripts():
    assert C.guess_language("तुम इतना जो मुस्कुरा रहे हो") == "hindi"
    assert C.guess_language("چپکے چپکے") == "urdu"
    assert C.guess_language("Perfect", "Ed Sheeran") is None
