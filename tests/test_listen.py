import shutil
import wave

import numpy as np
import pytest

from test_player import fake_ffplay, plays, wait_for

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

# trimmed from a real Shazam reply (2026-10-16)
SHAZAM_TRACK = {
    "title": "Arz Kiya Hai (Coke Studio Bharat)",
    "subtitle": "Anuv Jain",
    "url": "https://www.shazam.com/track/834891809/arz-kiya-hai-coke-studio-bharat",
    "images": {"coverart": "https://is1-ssl.mzstatic.com/a/400x400cc.jpg", "coverarthq": "https://is1-ssl.mzstatic.com/a/800x800cc.jpg"},
    "sections": [{"type": "SONG", "metadata": [{"title": "Album", "text": "Arz Kiya Hai - Coke Studio Bharat - Single"},
                                               {"title": "Label", "text": "Universal Music India Pvt Ltd."},
                                               {"title": "Released", "text": "2025"}]}],
    "hub": {"type": "APPLEMUSIC", "actions": [{"name": "apple", "type": "applemusicplay", "id": "1831237608"},
                                              {"name": "apple", "type": "uri", "uri": "https://audio-ssl.itunes.apple.com/a.m4a"}]},
}
YT = "ytxxxxxxxxx"


def _offline(text, *a, **k):
    raise RuntimeError("no internet in tests")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from mastgaane import config, db as D, jockey, listen

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    monkeypatch.setattr(config.settings, "audd_api_key", "")
    monkeypatch.setattr(config.settings, "listen_mic", "")
    monkeypatch.setattr(jockey, "prefetch_audio", lambda item: None)
    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(jockey, "speak", _offline)
    monkeypatch.setattr(listen, "_youtube_id", lambda title, artist: YT)
    monkeypatch.setattr(listen, "_classify_later", lambda song_id, source: None)
    D._engine = None
    with D.session() as s:
        for i in range(3):
            s.add(D.Song(title=f"Ghazal {i}", artist="Jagjit Singh", norm_key=f"g{i}", genre="ghazal", language="urdu",
                         status="approved", yt_video_id=f"gvid{i:07d}"))
        s.commit()
    yield D
    D._engine = None


def test_reads_a_shazam_reply():
    from mastgaane.listen import from_shazam

    h = from_shazam(SHAZAM_TRACK)
    assert (h.title, h.artist, h.year, h.apple_id) == ("Arz Kiya Hai", "Anuv Jain", 2025, "1831237608")
    assert h.album.startswith("Arz Kiya Hai") and h.cover_url.endswith("800x800cc.jpg")


def test_apple_ids_from_links():
    from mastgaane.listen import apple_id_from_url as f

    assert f("https://music.apple.com/in/album/arz-kiya-hai/1831237607?i=1831237608") == "1831237608"
    assert f("https://music.apple.com/in/song/arz-kiya-hai/1831237608") == "1831237608"
    assert f("https://music.apple.com/song/1831237608") == "1831237608"
    assert f("") == ""


def test_only_real_mics_listen(monkeypatch):
    from mastgaane import config, listen

    names = ["Headset Microphone (Oculus Virtual Audio Device)", "Microphone (Brio 100)",
             "Microphone (Realtek USB Audio)", "Headset (IPL08)", "Stereo Mix (Realtek Audio)"]
    monkeypatch.setattr(listen, "_dshow_audio", lambda: names)
    monkeypatch.setattr(config.settings, "listen_mic", "")
    assert listen.mics() == ["Microphone (Brio 100)", "Microphone (Realtek USB Audio)"]
    monkeypatch.setattr(config.settings, "listen_mic", "ipl08")  # picked by hand: even the Bluetooth headset
    assert listen.mics() == ["Headset (IPL08)"]


def test_save_keeps_the_song_and_logs_the_catch(db):
    from sqlmodel import select

    from mastgaane import listen

    heard = listen.from_shazam(SHAZAM_TRACK)
    out = listen.save(heard.title, heard.artist, "mic", heard)
    assert out["found"] and out["created"] and out["playable"]
    assert out["summary"] == "That's Arz Kiya Hai by Anuv Jain. Saved to your collection — MastGaane FM plays it next."
    with db.session() as s:
        song = s.get(db.Song, out["song_id"])
        assert song.status == "approved" and song.keep and song.source_tier == listen.TIER_HEARD
        assert (song.yt_video_id, song.apple_id, song.year) == (YT, "1831237608", 2025)
        assert song.apple_url.endswith("/song/1831237608")
        song.status = "rejected"
        s.commit()

    again = listen.save("Arz Kiya Hai", "Anuv Jain", "phone")
    assert not again["created"] and again["song_id"] == out["song_id"]
    assert "Already in your collection" in again["summary"]
    with db.session() as s:
        song = s.get(db.Song, out["song_id"])
        assert song.status == "approved" and song.times_seen == 2  # caught again = wanted again
        assert [c.source for c in s.exec(select(db.Catch)).all()] == ["mic", "phone"]
    first = listen.recent()[0]
    assert (first["title"], first["source"], first["yt"]) == ("Arz Kiya Hai", "📱 phone", YT)


@needs_ffmpeg
def test_a_phone_recording_is_tried_in_windows(db, tmp_path, monkeypatch):
    from mastgaane import listen

    take = tmp_path / "take.wav"
    t = np.arange(30 * listen.RATE) / listen.RATE
    listen._write_wav(take, 0.3 * np.sin(2 * np.pi * 440 * t))
    clips = []

    def shazam(clip):
        clips.append(clip)
        with wave.open(str(clip)) as w:
            assert w.getnframes() <= listen.WINDOW_S * listen.RATE
        return SHAZAM_TRACK if len(clips) == 2 else None

    monkeypatch.setattr(listen, "shazam_track", shazam)
    out = listen.catch("phone", upload=take)
    assert out["found"] and out["title"] == "Arz Kiya Hai" and len(clips) == 2
    assert not any(p.exists() for p in clips)  # recordings are gone once named


def test_silence_is_a_miss_not_a_guess(db, tmp_path, monkeypatch):
    from mastgaane import listen

    quiet = listen._write_wav(tmp_path / "quiet.wav", np.zeros(5 * listen.RATE))
    monkeypatch.setattr(listen, "record_mics", lambda seconds, folder: [(quiet, listen.level_db(quiet), "Microphone (Brio 100)")])
    monkeypatch.setattr(listen, "shazam_track", lambda clip: pytest.fail("silence must not reach Shazam"))
    out = listen.catch("mic", 5)
    assert not out["found"] and "heard almost nothing" in out["summary"]

    listen._busy.acquire()
    try:
        with pytest.raises(listen.ListenError, match="already listening"):
            listen.catch("mic", 5)
    finally:
        listen._busy.release()


def test_fm_plays_a_fresh_catch_first_and_once(db):
    from mastgaane import jockey, listen

    out = listen.save("Arz Kiya Hai", "Anuv Jain", "mic")
    item = jockey.next_item(n_picked=3, talk=True, use_llm=True)
    assert item.ref == f"song:{out['song_id']}" and item.caught
    assert "पहचाना" in item.link_text or "नई पसंद" in item.link_text
    assert jockey.pick(exclude=[item.ref]).ref != item.ref  # already queued on the client
    assert jockey.pick(focus="ghazals").title.startswith("Ghazal")  # a wish still rules
    jockey.record("song", item.ref, "skip", title=item.title)
    with db.session() as s:
        assert jockey.fresh_catch(s, set()) is None  # heard since it was caught


def test_player_puts_a_caught_song_on(db, monkeypatch):
    from mastgaane import jockey, listen
    from mastgaane.player import Player

    fake_ffplay(monkeypatch, 30)
    out = listen.save("Arz Kiya Hai", "Anuv Jain", "mic")
    with db.session() as s:
        song = s.get(db.Song, out["song_id"])
    item = jockey.announce(jockey.song_item(song, caught=True), talk=False)
    p = Player(lambda: "http://127.0.0.1:1")
    assert p.play_item(item, now=False)["state"] == "stopped"  # "next" never switches the speakers on

    assert p.play_item(item, now=True)["state"] == "playing"
    assert wait_for(lambda: p.now()["title"] == "Arz Kiya Hai" and p.now()["phase"] == "main")

    p.play("ghazals", talk=False)
    assert wait_for(lambda: p.now()["title"].startswith("Ghazal") and p.now()["phase"] == "main")
    p.play_item(item, now=False)
    assert p.now()["upcoming"] == "Arz Kiya Hai"
    p.skip()
    assert wait_for(lambda: p.now()["title"] == "Arz Kiya Hai" and p.now()["phase"] == "main")
    assert len(plays(db, "skip")) == 1  # only the ghazal the listener skipped
    p.stop()
    assert wait_for(lambda: p.now()["state"] == "stopped")


@needs_ffmpeg
def test_listen_routes(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane import listen
    from mastgaane.web import app as web

    monkeypatch.setattr(web, "_player", None)
    c = TestClient(web.app)

    # a phone recording (any container; only the sound is used) — real ffmpeg, so before fake_ffplay
    take = listen._write_wav(tmp_path / "take.wav", 0.3 * np.sin(np.arange(8 * listen.RATE) / 10))
    monkeypatch.setattr(listen, "shazam_track", lambda clip: SHAZAM_TRACK)
    r = c.post("/api/listen/file", files={"file": ("take.mov", take.read_bytes(), "video/quicktime")}, data={"play": "none"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Arz Kiya Hai" and r.json()["created"]

    fake_ffplay(monkeypatch, 30)
    r = c.post("/api/listen/save", json={"title": "Arz Kiya Hai", "artist": "Anuv Jain",
                                          "apple_url": "https://music.apple.com/in/album/x/1831237607?i=1831237608"}).json()
    assert r["found"] and not r["created"] and "player" not in r  # the player is off: "next" waits for FM
    with db.session() as s:
        assert s.get(db.Song, r["song_id"]).apple_id == "1831237608"
    page = c.get("/listen")
    assert page.status_code == 200 and "Arz Kiya Hai" in page.text and "/api/listen/save" in page.text

    assert c.post("/api/listen", json={"source": "radio"}).status_code == 400
    assert c.post("/api/listen/save", json={"title": "Later", "play": "later"}).status_code == 400
    with db.session() as s:
        from sqlmodel import select

        assert s.exec(select(db.Song).where(db.Song.title == "Later")).first() is None  # refused before saving

    r = c.post("/api/listen/save", json={"title": "Arz Kiya Hai", "artist": "Anuv Jain", "play": "now"}).json()
    assert r["player"] == "now" and r["summary"].endswith("playing it now.")
    assert wait_for(lambda: c.get("/api/player/now").json()["title"] == "Arz Kiya Hai")

    c.post("/api/player/stop")
    assert wait_for(lambda: c.get("/api/player/now").json()["state"] == "stopped")


def test_the_mic_order_of_preference():
    from mastgaane.listen import preferred

    names = ["Microphone (Brio 100)", "Microphone (Yeti Stereo Microphone)", "Headset (IPL08)"]
    assert preferred(names, "yeti,brio") == ["Microphone (Yeti Stereo Microphone)"]
    assert preferred(names[:1] + names[2:], "yeti,brio") == ["Microphone (Brio 100)"]
    assert preferred(names, "brio") == ["Microphone (Brio 100)"] and preferred(names, "fifine") == []
