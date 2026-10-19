import subprocess
import sys
import time

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from mastgaane import config, db as D, jockey

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "reports_dir", tmp_path)
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    monkeypatch.setattr(jockey, "prefetch_audio", lambda item: None)  # no YouTube downloads in tests
    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    D._engine = None
    with D.session() as s:
        for i in range(4):
            s.add(D.Song(title=f"Ghazal {i}", artist="Jagjit Singh", norm_key=f"g{i}", genre="ghazal", language="urdu",
                         status="approved", yt_video_id=f"gvid{i:07d}", year=1985 + i))
            s.add(D.Song(title=f"Folk {i}", artist="Sharda Sinha", norm_key=f"f{i}", genre="folk", language="bhojpuri",
                         status="approved", yt_video_id=f"fvid{i:07d}", year=2015))
        s.commit()
    yield D
    D._engine = None


def fake_ffplay(monkeypatch, seconds: float):
    """ffplay stand-in: a real process that 'plays' for `seconds` (so kill / suspend are real)."""
    from mastgaane import player

    real = subprocess.Popen
    monkeypatch.setattr(player, "ffplay_path", lambda: "ffplay")
    monkeypatch.setattr(player.subprocess, "Popen",
                        lambda args, **kw: real([sys.executable, "-c", f"import time; time.sleep({seconds})"], **kw))


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def plays(db, outcome):
    from sqlmodel import select

    with db.session() as s:
        return [p for p in s.exec(select(db.Play)).all() if p.outcome == outcome]


def test_spoken_wishes_resolve():
    from mastgaane.jockey import resolve_focus

    assert resolve_focus("") == ("any", "")
    assert resolve_focus("play some ghazals") == ("genre", "ghazal")
    assert resolve_focus("hawa mahal") == ("programme", "hawa-mahal")
    assert resolve_focus("bhojpuri gaane") == ("language", "bhojpuri")
    assert resolve_focus("90s songs") == ("era", "1990s")
    assert resolve_focus("purane gaane") == ("era", "old")
    assert resolve_focus("songs by Jagjit Singh") == ("text", "jagjit singh")
    assert resolve_focus("play some music") == ("any", "")


def test_pick_honours_the_wish(db):
    from mastgaane import jockey

    assert {jockey.pick(focus="ghazals").artist for _ in range(5)} == {"Jagjit Singh"}
    assert jockey.pick(focus="sharda sinha").genre == "folk"
    assert jockey.pick(focus="old songs").year < 1990


def test_player_plays_skips_likes_and_stops(db, monkeypatch):
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 30)
    p = Player(lambda: "http://127.0.0.1:1")
    now = p.play("ghazals", talk=False)
    assert now["state"] == "playing"
    assert wait_for(lambda: p.now()["phase"] == "main")
    first = p.now()["title"]
    assert first.startswith("Ghazal")

    assert p.pause()["state"] == "paused"
    assert p.resume()["state"] == "playing"
    p.like()
    assert [x.title for x in plays(db, "like")] == [first]

    p.skip()
    assert wait_for(lambda: plays(db, "skip") and p.now()["phase"] == "main" and p.now()["title"] != first)
    assert p.now()["title"].startswith("Ghazal")

    p.play("sharda sinha", talk=False)  # a new wish switches right away, without counting as a skip
    assert not p.now()["title"].startswith("Ghazal")  # never reports the old song as playing
    assert wait_for(lambda: p.now()["artist"] == "Sharda Sinha")
    assert len(plays(db, "skip")) == 1

    p.stop()
    assert wait_for(lambda: p.now()["state"] == "stopped")


def test_mute_keeps_the_show_going_silently(db, monkeypatch):
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 30)
    p = Player(lambda: "http://127.0.0.1:1")
    held = []
    monkeypatch.setattr(p._volume, "hold", lambda pid, level: held.append(level))
    p.play("ghazals", talk=False)
    assert wait_for(lambda: p.now()["phase"] == "main")
    assert held[-1] == 1.0 and p.now()["muted"] is False

    now = p.mute()
    assert now["muted"] and now["state"] == "playing" and held[-1] == 0.0  # not paused: it plays on, unheard
    p.duck()  # a voice command, Dexter talking: still nothing
    assert held[-1] == 0.0
    p.duck(None)
    assert held[-1] == 0.0
    first = p.now()["title"]
    p.skip()  # the next song starts muted too
    assert wait_for(lambda: p.now()["phase"] == "main" and p.now()["title"] != first)
    assert held[-1] == 0.0 and p.now()["muted"]

    assert p.unmute()["muted"] is False and held[-1] == 1.0
    p.mute(None)  # the tray's toggle
    assert p.now()["muted"] and held[-1] == 0.0
    p.resume()  # asked to play: to be heard
    assert not p.now()["muted"] and held[-1] == 1.0
    p.mute()
    p.play("folk", talk=False)
    assert not p.now()["muted"]
    p.stop()
    assert wait_for(lambda: p.now()["state"] == "stopped")


def test_player_counts_a_full_listen(db, monkeypatch):
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 0.3)
    p = Player(lambda: "http://127.0.0.1:1")
    p.play("folk", talk=False)
    assert wait_for(lambda: len(plays(db, "full")) >= 1)
    p.stop()
    assert wait_for(lambda: p.now()["state"] == "stopped")


def test_player_api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    fake_ffplay(monkeypatch, 30)
    monkeypatch.setattr(web, "_player", None)
    c = TestClient(web.app)
    assert c.get("/api/player/now").json()["summary"] == "MastGaane is not playing."
    r = c.post("/api/player/play", json={"focus": "ghazals", "talk": False}).json()
    assert r["state"] == "playing" and r["focus"] == "ghazals"
    assert wait_for(lambda: c.get("/api/player/now").json()["summary"].startswith("Playing Ghazal"))
    assert c.post("/api/player/pause").json()["summary"].startswith("Paused: Ghazal")
    muted = c.post("/api/player/mute").json()
    assert muted["muted"] and muted["summary"].startswith("Muted. ")
    assert c.post("/api/player/unmute").json()["muted"] is False
    assert c.post("/api/player/bogus").status_code == 404
    c.post("/api/player/stop")
    assert wait_for(lambda: c.get("/api/player/now").json()["state"] == "stopped")


def test_player_plays_a_mood(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    fake_ffplay(monkeypatch, 30)
    monkeypatch.setattr(web, "_player", None)
    c = TestClient(web.app)
    r = c.post("/api/player/play", json={"focus": "something sad", "talk": False, "links": "never"}).json()
    assert r["wish"] == "sad songs" and r["summary"] == "MastGaane is starting — sad songs."
    assert wait_for(lambda: c.get("/api/player/now").json()["summary"].startswith("Playing Ghazal"))
    now = c.get("/api/player/now").json()
    assert now["summary"].endswith("(sad songs).") and "sad" in now["mood"]
    c.post("/api/player/like")
    assert "sad" in plays(db, "like")[0].mood
    assert c.post("/api/player/play", json={"focus": "x", "links": "sometimes"}).status_code == 400
    c.post("/api/player/stop")
    assert wait_for(lambda: c.get("/api/player/now").json()["state"] == "stopped")


def test_vocabulary_endpoint(db):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    v = TestClient(web.app).get("/api/player/vocabulary").json()
    assert set(v) == {"programmes", "artists", "moods", "times"}
    hawa = next(p for p in v["programmes"] if p["slug"] == "hawa-mahal")
    assert hawa["name"] == "Hawa Mahal" and hawa["hindi"] == "हवा महल" and "hawamahal" in hawa["aliases"]
    assert set(v["artists"]) == {"Jagjit Singh", "Sharda Sinha"}
    assert "udaas" in v["moods"] and "late night" in v["times"]


def _fake_ps(monkeypatch, models):
    import httpx

    from mastgaane.resolver import llm

    def get(url, timeout=None):
        assert url.endswith("/api/ps"), url  # nothing but the "what is loaded" probe
        return httpx.Response(200, json={"models": models}, request=httpx.Request("GET", url))

    monkeypatch.setattr(llm.httpx, "get", get)


def test_resident_links_never_load_a_model(db, monkeypatch):
    from mastgaane import config, jockey
    from mastgaane.resolver import llm

    monkeypatch.setattr(config.settings, "llm_provider", "auto")
    monkeypatch.setattr(config.settings, "jockey_llm_links", "resident")
    monkeypatch.setattr(config.settings, "jockey_talk_every", 1)  # a link before every pick
    monkeypatch.setattr(jockey, "speak", lambda text, *a, **k: "0" * 20)
    calls = []
    monkeypatch.setattr(llm, "_ollama_json", lambda *a, **k: calls.append(k) or jockey.Link(text="एक लिंक जो एलएलएम ने लिखा"))
    monkeypatch.setattr(llm, "providers", lambda: calls.append("providers") or ["ollama"])

    _fake_ps(monkeypatch, [])  # nothing loaded → template, no LLM call at all
    item = jockey.next_item(n_picked=1, talk=True, use_llm=True, focus="ghazals")
    assert item.link_text and calls == []

    _fake_ps(monkeypatch, [{"name": "gemma4:e4b-it-qat", "context_length": 4096,
                            "expires_at": "2099-01-01T00:00:00+00:00"}])
    item = jockey.next_item(n_picked=1, talk=True, use_llm=True, focus="ghazals")
    assert item.link_text == "एक लिंक जो एलएलएम ने लिखा"
    assert calls == [{"model": "gemma4:e4b-it-qat"}]  # the resident model, never the provider chain

    calls.clear()
    jockey.next_item(n_picked=1, talk=True, use_llm=True, focus="ghazals", links="never")
    assert calls == []


def test_resident_call_keeps_the_models_context_and_keep_alive(monkeypatch):
    import httpx

    from mastgaane import jockey
    from mastgaane.resolver import llm

    _fake_ps(monkeypatch, [{"name": "gemma4:e4b-it-qat", "context_length": 8192, "expires_at": "2999-01-01T00:00:00+00:00"}])
    seen = {}

    def post(url, json, timeout):
        seen.update(json)
        return httpx.Response(200, json={"message": {"content": '{"text": "नमस्ते दोस्तों"}'}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.complete_json("s", "u", jockey.Link, model="gemma4:e4b-it-qat").text == "नमस्ते दोस्तों"
    assert seen["model"] == "gemma4:e4b-it-qat" and seen["options"]["num_ctx"] == 8192 and seen["keep_alive"] == -1


def test_a_wish_nothing_matches_falls_back_to_the_mix(db, monkeypatch):
    """v0.9: 'play me Harman Chali' used to end the show with "Nothing to play". An autonomous radio
    never answers with silence: the host says the wish was not found and the usual mix goes on."""
    from mastgaane import jockey
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 30)
    spoken = []
    monkeypatch.setattr(jockey, "speak", lambda text, emotion="warm", **kw: spoken.append(text) or None)
    p = Player(lambda: "http://127.0.0.1:1")
    p.play("harman chali", talk=True)
    assert wait_for(lambda: p.now()["phase"] == "main")
    now = p.now()
    assert now["state"] == "playing" and now["title"]
    assert now["fell_back_from"] == "harman chali" and now["focus"] == ""
    assert "harman chali" in now["message"] and "usual mix" in now["message"]
    assert spoken and "harman chali" in spoken[-1] and "नहीं मिला" in spoken[-1]
    p.stop()
    assert wait_for(lambda: p.now()["state"] == "stopped")


def test_a_new_wish_clears_the_fallback_note(db, monkeypatch):
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 30)
    p = Player(lambda: "http://127.0.0.1:1")
    p.play("harman chali", talk=False)
    assert wait_for(lambda: p.now()["fell_back_from"] == "harman chali")
    p.play("ghazals", talk=False)
    assert wait_for(lambda: p.now()["artist"] == "Jagjit Singh")
    assert p.now()["fell_back_from"] == ""
    p.stop()
