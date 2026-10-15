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


def test_bhojpuri_folk_and_ghazal_rules(monkeypatch):
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    monkeypatch.setattr(config.settings, "rules_file", config.ROOT / "does-not-exist.yaml")
    C.rules.cache_clear()
    v = C.classify("Kelwa Ke Paat Par", "Sharda Sinha")
    assert (v.keep, v.language, v.genre) == (True, "bhojpuri", "folk")
    v = C.classify("Marbo Re Sugwa Dhanukh Se", "Anuradha Paudwal", "Chhath puja special")
    assert (v.keep, v.genre) == (True, "folk")
    assert C.classify("Kal Chaudhvin Ki Raat Thi", "Chitra Singh").genre == "ghazal"
    assert C.gate(_v(language="bhojpuri", genre="folk")).keep
    assert not C.gate(_v(language="punjabi", genre="folk")).keep
    C.rules.cache_clear()


def test_manual_status_is_marked_once():
    from mastgaane.db import MANUAL, Song, set_manual_status

    song = Song(title="t", norm_key="t", classify_reason="caption not relevant")
    set_manual_status(song, "rejected")
    set_manual_status(song, "approved")
    assert (song.status, song.keep) == ("approved", True)
    assert song.classify_reason == f"{MANUAL}approved; caption not relevant"


def test_llm_only_regional_label_goes_to_review(monkeypatch):
    monkeypatch.setattr(config.settings, "rules_file", config.ROOT / "does-not-exist.yaml")
    C.rules.cache_clear()
    monkeypatch.setattr(C, "complete_json", lambda *a, **k: _v(language="magahi", genre="folk", reason="traditional folk"))
    v = C.classify("Vidhatha Talapuna", "S. P. Balasubrahmanyam")
    assert v.keep and C.needs_review(v)
    monkeypatch.setattr(C, "complete_json", lambda *a, **k: _v(language="hindi", genre="ghazal"))
    assert not C.needs_review(C.classify("Hothon Se Chhu Lo", "unknown"))
    assert not C.needs_review(C.classify("Kelwa Ke Paat Par", "Sharda Sinha"))  # a rule backs it: no review
    C.rules.cache_clear()
