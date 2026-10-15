import json
from pathlib import Path

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from mastgaane import config, db

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    db._engine = None
    from fastapi.testclient import TestClient

    from mastgaane.db import session
    from mastgaane.harvester.parse import extract_items
    from mastgaane.harvester.store import upsert_reel
    from mastgaane.resolver.pipeline import resolve_pending
    from mastgaane.web.app import app

    payload = json.loads((Path(__file__).parent / "fixtures" / "graphql_clips_home.json").read_text(encoding="utf-8"))
    with session() as s:
        for it in extract_items(payload):
            upsert_reel(s, it, run_id=None, watched=True)
    resolve_pending(deep=False)
    yield TestClient(app)
    db._engine = None


def test_pages_with_data(client):
    html = client.get("/").text
    assert "Tum Itna Jo Muskura Rahe Ho" in html and "Jagjit Singh" in html
    assert "@ghazal.lovers" in html
    assert "Tum Itna" in client.get("/reels").text
    assert "Tum Itna" in client.get("/playlist").text  # approved → in queue


def test_status_and_edit_flow(client):
    from sqlmodel import select

    from mastgaane.db import Song, session

    with session() as s:
        song = s.exec(select(Song)).one()
        sid = song.id
    r = client.post(f"/songs/{sid}/status", data={"status": "rejected", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303
    with session() as s:
        assert s.get(Song, sid).status == "rejected"
    client.post(f"/songs/{sid}/edit", data={"title": "Tum Itna Jo Muskura Rahe Ho", "artist": "Jagjit Singh & Chitra Singh", "back": "/"})
    with session() as s:
        song = s.get(Song, sid)
        assert song.artist.startswith("Jagjit") and song.yt_video_id is None
    assert "Jagjit Singh &amp; Chitra Singh" in client.get("/?status=rejected").text


def test_radio_page_never_waits_on_the_directory(client, tmp_path, monkeypatch):
    from mastgaane import radio

    monkeypatch.setattr(radio, "CACHE", tmp_path / "radio_stations.json")
    monkeypatch.setattr(radio, "discover", lambda: [radio.Station("Test Folk FM", "https://example.test/s", "Bhojpuri & folk")])
    html = client.get("/radio").text
    assert "Vividh Bharati" in html and "Radio Kesari" in html
    radio.refresh()
    assert "Test Folk FM" in client.get("/radio").text


def test_discover_page_lists_pending_songs_with_a_video(client):
    from mastgaane.db import Song, session

    with session() as s:
        s.add(Song(title="Kelwa Ke Paat Par", artist="Sharda Sinha", norm_key="kelwa|sharda", source_tier=4, yt_video_id="qUeuWi81PmM", status="pending"))
        s.add(Song(title="No Video Yet", artist="x", norm_key="novideo|x", status="pending"))
        s.commit()
    html = client.get("/discover").text
    assert "Kelwa Ke Paat Par" in html and "qUeuWi81PmM" in html and "No Video Yet" not in html


def test_archive_filters_and_pages(client, monkeypatch):
    from mastgaane import archive as A

    hm = A.PROGRAMMES["hawa-mahal"]
    assert A._matches("05 01 2021 HAWAMAHAL BUS STOP", hm)
    assert A._matches("हवा महल नाटिका - छुटकारा", hm)
    assert not A._matches("Majestic Hawa Mahal Jaipur palace tour", hm)
    assert A._seconds("14:56") == 896 and A._seconds("1:07:36") == 4056
    monkeypatch.setattr(A, "episodes", lambda slug, refresh=False: [A.Episode("yt", "axyuY6yzeuI", "Hawa Mahal - Bus Stop", "12:28")])
    html = client.get("/archive?p=hawa-mahal").text
    assert "Bus Stop" in html and "Chhaya Geet" in html
    assert client.get("/archive/yt/not-an-id.audio").status_code == 400
    assert client.get("/archive/ia/..%2Fetc.json").status_code in (400, 404)
