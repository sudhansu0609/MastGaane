from datetime import datetime

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
        for i in range(6):
            s.add(D.Song(title=f"Kept {i}", artist="Sharda Sinha" if i < 3 else "Jagjit Singh", norm_key=f"k{i}",
                         genre="folk" if i < 3 else "ghazal", status="approved", yt_video_id=f"vid{i:08d}"))
        s.add(D.Song(title="New One", artist="Chandan Tiwari", norm_key="new", genre="folk", status="pending", yt_video_id="newvid00000", source_tier=4))
        s.commit()
    yield D
    D._engine = None


def test_schedule_slots():
    from mastgaane.jockey import slot

    assert slot(datetime(2026, 10, 14, 20, 30)) == "geetmala"  # Wednesday 8 pm
    assert slot(datetime(2026, 10, 15, 20, 30)) == "hawa-mahal"
    assert slot(datetime(2026, 10, 15, 22, 0)) == "chhaya-geet"
    assert slot(datetime(2026, 10, 15, 11, 0)) is None


def test_pick_mixes_discoveries_and_never_repeats(db, monkeypatch):
    from mastgaane import jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    assert jockey.pick(n_picked=3).discovery  # every 4th pick is a new find
    seen = set()
    for i in range(6):
        it = jockey.pick(exclude=list(seen), n_picked=0)
        assert it and it.ref not in seen and it.kind == "song"
        seen.add(it.ref)
        jockey.record("song", it.ref, "full", artist=it.artist, genre=it.genre)
    assert jockey.pick(n_picked=0).discovery  # approved pool exhausted by recent plays → falls back to finds


def test_programme_slot_inserts_episode_once(db, monkeypatch):
    from mastgaane import archive, jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: "hawa-mahal")
    monkeypatch.setattr(archive, "episodes", lambda slug, refresh=False: [archive.Episode("yt", "axyuY6yzeuI", "Hawa Mahal - Bus Stop", "12:28")])
    it = jockey.pick()
    assert it.kind == "episode" and it.programme == "hawa-mahal" and it.audio == "/archive/yt/axyuY6yzeuI.audio"
    jockey.record("episode", it.ref, "full", programme="hawa-mahal")
    assert jockey.pick().kind == "song"  # already had today's Hawa Mahal


def test_like_and_dislike_teach_taste_and_set_status(db):
    from mastgaane import jockey

    jockey.record("song", "song:7", "like", artist="Chandan Tiwari", genre="folk")
    jockey.record("song", "song:4", "dislike", artist="Jagjit Singh", genre="ghazal")
    with db.session() as s:
        assert s.get(db.Song, 7).status == "approved" and s.get(db.Song, 7).classify_reason.startswith(db.MANUAL)
        assert s.get(db.Song, 4).status == "rejected"
    t = jockey.taste()
    assert t["artist"]["chandan tiwari"] > 0 > t["artist"]["jagjit singh"]


def test_template_links_are_hindi():
    from mastgaane.jockey import Item, template_link

    it = Item(ref="song:1", kind="song", title="Kelwa Ke Paat Par", artist="Sharda Sinha", audio="/x")
    text = template_link(it, first=True, now=datetime(2026, 10, 15, 22, 0))
    assert text.startswith("शुभ रात्रि") and "Sharda Sinha" in text and "Kelwa Ke Paat Par" in text


def test_digest_suggests_rules_and_writes_them_safely(db, tmp_path, monkeypatch):
    from mastgaane import config, digest, jockey
    from mastgaane.resolver import classify

    rules = tmp_path / "rules.yaml"
    rules.write_text("# my comment\nfolk_artists:\n  - Sharda Sinha\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "rules_file", rules)
    classify.rules.cache_clear()
    for _ in range(2):
        jockey.record("song", "song:7", "like", artist="Chandan Tiwari", genre="folk")
    d = digest.build(use_llm=False)
    assert d["plays"] == 2 and d["likes"] == 2
    assert {"artist": "Chandan Tiwari", "list": "folk_artists"}.items() <= d["rule_suggestions"][0].items()
    assert digest.latest()["plays"] == 2
    assert digest.add_to_rules("folk_artists", "Chandan Tiwari")
    assert not digest.add_to_rules("folk_artists", "chandan tiwari")
    text = rules.read_text(encoding="utf-8")
    assert text.startswith("# my comment") and '- "Chandan Tiwari"' in text
    assert "Chandan Tiwari" in classify.rules()["folk_artists"]
    with pytest.raises(ValueError):
        digest.add_to_rules("allowed_languages", "x")
    classify.rules.cache_clear()


def test_fm_routes(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane import jockey
    from mastgaane.web.app import app

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(jockey, "prefetch_audio", lambda item: None)
    c = TestClient(app)
    assert "MastGaane FM" in c.get("/fm").text
    it = c.get("/jockey/next?talk=0").json()
    assert it["ref"].startswith("song:") and it["audio"].startswith("/jockey/audio/")
    assert c.post("/jockey/event", json={"kind": "song", "ref": it["ref"], "outcome": "full", "listened_s": 200}).json()["ok"]
    assert c.post("/jockey/event", json={"kind": "song", "ref": it["ref"], "outcome": "bogus"}).status_code == 400
    assert c.get("/jockey/voice/../../x.mp3").status_code == 404
    assert "Naya kya hai" in c.get("/naya").text


def test_years_from_titles():
    from mastgaane.archive import decade_of, year_of

    assert year_of("CHHAYA GEET MAMATA SINGH 16 6 15") == 2015
    assert year_of("Chhaya Geet 091216") == 2016
    assert year_of("Jaimala 21-08-98") == 1998
    assert year_of("SD Burman Presents Jaimala - P1 - 1972") == 1972
    assert year_of("भूले बिसरे गीत: विविध भारती 31.03.2024") == 2024
    assert year_of("HAWAMAHAL BUS STOP") is None and year_of("Season 49") is None
    assert [decade_of(y) for y in (1972, 1998, 2010, 2024, None)] == ["before 1996", "1996–2005", "2006–2015", "2016–now", "year unknown"]


def test_episode_picks_rotate_through_eras(db, monkeypatch):
    from mastgaane import archive, jockey

    eps = [archive.Episode("yt", f"new{i:08d}", f"Jaimala {i}", "40:00", 2024) for i in range(30)]
    eps.append(archive.Episode("yt", "old00000001", "Jaimala 1998", "40:00", 1998))
    monkeypatch.setattr(archive, "episodes", lambda slug, refresh=False: eps)
    years = {jockey._episode_item("jaimala", set()).year for _ in range(40)}
    assert 1998 in years  # one old episode among 30 new ones still comes up
    assert "1998" in jockey.template_link(jockey.Item(ref="yt:x", kind="episode", title="t", programme="jaimala", programme_name="Jaimala", audio="/a", year=1998))


def test_archive_page_filters_by_era(db, monkeypatch):
    from fastapi.testclient import TestClient

    from mastgaane import archive
    from mastgaane.web.app import app

    monkeypatch.setattr(archive, "episodes", lambda slug, refresh=False: [
        archive.Episode("yt", "aaaaaaaaaaa", "Jaimala new", "40:00", 2024),
        archive.Episode("ia", "jaimala-1998", "Jaimala old", "", 1998)])
    c = TestClient(app)
    html = c.get("/archive?p=jaimala").text
    assert "1996–2005 (1)" in html and "Jaimala new" in html and "Jaimala old" in html
    html = c.get("/archive?p=jaimala&era=1996–2005").text
    assert "Jaimala old" in html and "Jaimala new" not in html
