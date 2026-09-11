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
