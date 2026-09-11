"""End-to-end resolve on fixture reels without network: attribution tier + rule-based classifier."""
import json
import os
from pathlib import Path

import pytest

os.environ["MASTGAANE_TEST"] = "1"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    from mastgaane import config, db

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "anthropic_api_key", "")
    monkeypatch.setattr(config.settings, "llm_provider", "none")
    db._engine = None
    yield
    db._engine = None


def test_attribution_to_song(tmp_db):
    from mastgaane.db import Reel, Song, session
    from mastgaane.harvester.parse import extract_items
    from mastgaane.harvester.store import upsert_reel
    from mastgaane.resolver.pipeline import resolve_pending
    from sqlmodel import select

    payload = json.loads((Path(__file__).parent / "fixtures" / "graphql_clips_home.json").read_text(encoding="utf-8"))
    with session() as s:
        for it in extract_items(payload):
            upsert_reel(s, it, run_id=None, watched=True)
        # same reel served again → times_seen bump, not a duplicate
        upsert_reel(s, extract_items(payload)[0], run_id=None, watched=True)
        assert len(s.exec(select(Reel)).all()) == 2

    resolve_pending(deep=False)

    with session() as s:
        songs = s.exec(select(Song)).all()
        assert len(songs) == 1
        song = songs[0]
        assert song.title == "Tum Itna Jo Muskura Rahe Ho"
        assert song.keep is True and song.status == "approved"  # always-keep artist, conf 0.9 ≥ 0.8
        assert "Jagjit" in song.classify_reason
        reels = {r.shortcode: r for r in s.exec(select(Reel)).all()}
        assert reels["C_abcDEF123"].resolve_status == "resolved" and reels["C_abcDEF123"].song_id == song.id
        assert reels["C_abcDEF123"].times_seen == 2
        assert reels["C_origAUD456"].resolve_status == "skipped"  # original audio, deep disabled
