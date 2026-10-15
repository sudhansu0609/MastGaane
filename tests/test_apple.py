import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from mastgaane import config, db as D

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "reports_dir", tmp_path)
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    D._engine = None
    with D.session() as s:
        s.add(D.Song(title="Woh Humse Khafa Hain", artist="Udit Narayan,  Shreya Ghoshal", norm_key="a", status="approved", yt_video_id="vid00000001"))
        s.add(D.Song(title="Dillagi", artist="Khalid Khan", norm_key="b", status="approved", yt_video_id="vid00000002"))
        s.commit()
    yield D
    D._engine = None


def _hit(tid, title, artist):
    return {"trackId": tid, "trackName": title, "artistName": artist, "trackViewUrl": f"https://music.apple.com/in/song/{tid}"}


def test_names_and_titles_must_both_agree():
    from mastgaane.playlists.apple import artists_agree, titles_agree

    assert artists_agree("Udit Narayan,  Shreya Ghoshal", "Udit Narayan & Shreya Ghoshal")
    assert artists_agree("Farhan Akhtar, Shankar-Ehsaan-Loy, Javed Akhtar", "Shankar-Ehsaan-Loy, Farhan Akhtar & Javed Akhtar")
    assert not artists_agree("Khalid Khan", "Rahat Fateh Ali Khan, Ustad Nusrat Fateh Ali Khan & Salim-Sulaiman")
    assert titles_agree("Mann Kunto Maula - Violin", "Mann Kunto Maula - Violin Version (Instrumental)")
    assert not titles_agree("Dillagi", "Tere Bina")


def test_search_skips_same_title_by_someone_else(monkeypatch):
    from mastgaane.playlists import apple

    calls = []

    def fake(params):
        calls.append(params["term"])
        if params["term"] == "Dillagi Khalid Khan":
            return [_hit(1, "Dillagi", "Rahat Fateh Ali Khan")]
        return [_hit(1, "Dillagi", "Rahat Fateh Ali Khan"), _hit(2, "Dillagi", "Khalid Khan")]

    monkeypatch.setattr(apple, "_itunes", fake)
    link = apple.itunes_search("Dillagi", "Khalid Khan")
    assert link.id == "2" and calls == ["Dillagi Khalid Khan", "Dillagi"]  # second try: title alone

    monkeypatch.setattr(apple, "_itunes", lambda params: [_hit(1, "Dillagi", "Rahat Fateh Ali Khan")])
    assert apple.itunes_search("Dillagi", "Khalid Khan") is None


def test_resolve_missing_stores_id_and_apple_names(db, monkeypatch):
    from sqlmodel import select

    from mastgaane.playlists import apple

    hits = {"Woh Humse Khafa Hain": apple.AppleLink("123", "https://music.apple.com/in/song/123", "Woh Humse Khafa Hain", "Udit Narayan & Shreya Ghoshal")}
    monkeypatch.setattr(apple, "itunes_search", lambda t, a: hits.get(t))
    assert apple.resolve_missing() == 1
    with db.session() as s:
        songs = {x.title: x for x in s.exec(select(db.Song)).all()}
    assert songs["Woh Humse Khafa Hain"].apple_id == "123"
    lines = apple.AppleMusicAdapter.import_lines(sorted(songs.values(), key=lambda x: x.id))
    assert lines == ["Woh Humse Khafa Hain - Udit Narayan & Shreya Ghoshal", "Dillagi - Khalid Khan"]  # unmatched: our own text


def test_fm_apple_mode_only_with_a_token(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane import config, jockey
    from mastgaane.playlists import apple
    from mastgaane.web.app import app

    c = TestClient(app)
    monkeypatch.setattr(config.settings, "apple_dev_token", "")
    assert 'data-mode="apple"' not in c.get("/fm").text
    monkeypatch.setattr(config.settings, "apple_dev_token", "eyJ.test.token")
    page = c.get("/fm").text
    assert 'data-mode="apple"' in page and '"eyJ.test.token"' in page

    with db.session() as s:
        song = s.get(db.Song, 1)
        song.apple_id = "123"
        assert jockey._song_item(song, discovery=False).apple_id == "123"

    monkeypatch.setattr(apple, "resolve_missing", lambda: 0)
    r = c.get("/apple?resolve=1", follow_redirects=False)
    assert r.status_code == 303 and "background" in r.headers["location"]
