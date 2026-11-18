"""The song catalogue in git (catalogue.py): a new computer gets every song, and only the songs."""
import gzip
import json


def _fresh_db(D, config, monkeypatch, folder):
    folder.mkdir()
    monkeypatch.setattr(config.settings, "data_dir", folder)
    D._engine = None


def test_catalogue_round_trip(tmp_path, monkeypatch):
    from datetime import datetime

    from sqlmodel import select

    from mastgaane import catalogue, config, db as D

    _fresh_db(D, config, monkeypatch, tmp_path / "pc")
    with D.session() as s:
        s.add(D.Song(title="Chupke Chupke", artist="Ghulam Ali", norm_key="chupke|ghulam", genre="ghazal",
                     language="urdu", year=1982, status="approved", yt_video_id="abcdefghijk", mood="romantic",
                     first_seen=datetime(2026, 8, 23, 1, 2, 3), lyrics_snippet="what a reel said"))
        s.add(D.Song(title="Ugi Ho Suruj Dev", artist="Sharda Sinha", norm_key="ugi|sharda", genre="folk",
                     status="library", yt_video_id="bcdefghijkl"))
        s.add(D.Song(title="Some Remix", artist="DJ", norm_key="remix|dj", status="rejected"))
        s.commit()
        ghazal = s.exec(select(D.Song).where(D.Song.norm_key == "chupke|ghulam")).one()
        s.add(D.PlaylistItem(song_id=ghazal.id, target="ytmusic", playlist_id="PL1", track_id="t1"))
        s.add(D.Reel(shortcode="REEL1", song_id=ghazal.id))
        s.commit()

    out = tmp_path / "songs.jsonl.gz"
    assert catalogue.export(out) == 3
    first = out.read_bytes()
    assert catalogue.export(out) == 3 and out.read_bytes() == first  # same songs, same file (no git churn)
    rows = [json.loads(x) for x in gzip.open(out, "rt", encoding="utf-8")]
    assert [r["norm_key"] for r in rows] == sorted(r["norm_key"] for r in rows)
    assert "lyrics_snippet" not in rows[0] and "id" not in rows[0]  # only the songs: no reel text, no ids

    _fresh_db(D, config, monkeypatch, tmp_path / "mac")
    assert catalogue.load(out) == (3, 0)
    assert catalogue.load(out) == (0, 3)  # again: nothing doubled
    with D.session() as s:
        songs = {x.norm_key: x for x in s.exec(select(D.Song)).all()}
        assert {k: v.status for k, v in songs.items()} == {"chupke|ghulam": "approved", "ugi|sharda": "library",
                                                          "remix|dj": "rejected"}
        g = songs["chupke|ghulam"]
        assert (g.yt_video_id, g.year, g.mood, g.first_seen) == ("abcdefghijk", 1982, "romantic",
                                                                 datetime(2026, 8, 23, 1, 2, 3))
        items = s.exec(select(D.PlaylistItem)).all()
        assert [(i.song_id, i.target, i.playlist_id) for i in items] == [(g.id, "ytmusic", "PL1")]
        assert s.exec(select(D.Reel)).all() == []  # the reels stay on the PC
    D._engine = None


def test_import_never_changes_a_song_it_has(tmp_path, monkeypatch):
    from sqlmodel import select

    from mastgaane import catalogue, config, db as D

    _fresh_db(D, config, monkeypatch, tmp_path / "mac")
    with D.session() as s:
        s.add(D.Song(title="Chupke Chupke", artist="Ghulam Ali", norm_key="chupke|ghulam", status="rejected"))
        s.commit()
    src = tmp_path / "songs.jsonl"
    src.write_text(json.dumps({"title": "Chupke Chupke", "artist": "Ghulam Ali", "norm_key": "chupke|ghulam",
                               "status": "approved"}) + "\n\n" + json.dumps({"title": "", "norm_key": "x"}) + "\n",
                   encoding="utf-8")
    assert catalogue.load(src) == (0, 1)
    with D.session() as s:
        assert s.exec(select(D.Song.status)).all() == ["rejected"]  # your Mac's own verdict stands
    D._engine = None
