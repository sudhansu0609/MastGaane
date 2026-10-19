from test_player import db, fake_ffplay, wait_for  # noqa: F401  (db is a fixture)


def test_status_line():
    from mastgaane.tray import status_line

    base = {"state": "playing", "phase": "main", "kind": "song", "title": "Ranjish Hi Sahi", "artist": "Mehdi Hassan",
            "programme": "", "message": ""}
    assert status_line(base) == "▶ Ranjish Hi Sahi — Mehdi Hassan"
    assert status_line({**base, "state": "paused"}) == "⏸ Ranjish Hi Sahi — Mehdi Hassan"
    assert status_line({**base, "muted": True}) == "🔇 Ranjish Hi Sahi — Mehdi Hassan"
    assert status_line({**base, "phase": "link"}).endswith("(intro)")
    assert status_line({**base, "title": ""}) == "MastGaane — starting…"
    assert status_line({**base, "state": "stopped"}) == "MastGaane — not playing"
    assert status_line({**base, "state": "stopped", "message": "Nothing to play yet."}) == "Nothing to play yet."
    ep = {**base, "kind": "episode", "title": "Episode 12", "artist": "", "programme": "Hawa Mahal"}
    assert status_line(ep) == "▶ Hawa Mahal: Episode 12"
    assert status_line({**ep, "title": "Hawa Mahal 1987"}) == "▶ Hawa Mahal 1987"


def test_icon_image():
    from mastgaane.tray import icon_image

    img = icon_image(64)
    assert img.size == (64, 64) and img.getpixel((5, 32))[3] > 0 and img.getpixel((0, 0))[3] == 0


def test_tray_menu_drives_the_pc_player(db, monkeypatch):
    from mastgaane.player import Player
    from mastgaane.tray import Tray

    fake_ffplay(monkeypatch, 30)
    p = Player(lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(p, "play", lambda focus="", talk=False, links="", _play=p.play: _play(focus, talk=False))
    quits = []
    t = Tray(lambda: p, lambda: "http://127.0.0.1:1", lambda: quits.append(1))
    assert t._toggle_text(None) == "Play MastGaane FM" and not t._running(None)
    t._toggle()  # left click on a stopped player: play
    assert wait_for(lambda: p.now()["phase"] == "main")
    assert t._toggle_text(None) == "Pause" and t._on_song(None)
    t._toggle()
    assert p.now()["state"] == "paused" and t._toggle_text(None) == "Resume"
    t._toggle()
    assert p.now()["state"] == "playing"
    t._toggle_mute()
    assert p.now()["muted"] and t._muted(None) and p.now()["state"] == "playing"
    t._toggle_mute()
    assert not p.now()["muted"] and not t._muted(None)
    t._do("stop")()
    assert wait_for(lambda: p.now()["state"] == "stopped")
    t._quit()
    assert quits == [1]


def test_shutdown_route_is_local_only(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane.web import app as web

    called = []
    monkeypatch.setattr(web, "_player", None)
    monkeypatch.setattr(web, "_shutdown", None)
    local = TestClient(web.app, client=("127.0.0.1", 50000))
    assert local.post("/api/shutdown").status_code == 503  # not started by `mastgaane serve`
    web.set_shutdown(lambda: called.append(1))
    assert TestClient(web.app, client=("192.168.1.9", 50000)).post("/api/shutdown").status_code == 403
    assert called == []
    assert local.post("/api/shutdown").json()["ok"] and called == [1]
