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
    assert c.post("/api/player/bogus").status_code == 404
    c.post("/api/player/stop")
    assert wait_for(lambda: c.get("/api/player/now").json()["state"] == "stopped")
