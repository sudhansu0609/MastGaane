from datetime import datetime, timedelta

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


def test_the_usual_mix_leans_to_the_hour(db, monkeypatch):
    from mastgaane import db as D, jockey, moods

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(jockey, "TIME_SHARE", 1.0)
    assert moods.time_of_hour(7) == "morning" and moods.time_of_hour(23) == "night" and moods.time_of_hour(2) == "night"
    night = datetime(2026, 10, 17, 23, 30)
    genres = {jockey.pick(n_picked=0, now=night).genre for _ in range(15)}
    assert genres == {"ghazal"}  # ghazals are sad/romantic: the night's songs
    with D.session() as s:
        s.add(D.Song(title="Aisi Lagi Lagan", artist="Anup Jalota", norm_key="bh1", genre="devotional",
                     status="pending", yt_video_id="bhajan00001"))
        s.commit()
    morning = jockey.pick(n_picked=0, now=datetime(2026, 10, 17, 7, 0))
    assert morning.title == "Aisi Lagi Lagan" and morning.discovery  # no kept bhajan yet: a newly found one
    assert jockey.pick(n_picked=0, now=night, focus="folk").genre == "folk"  # a wish beats the hour
    monkeypatch.setattr(jockey, "TIME_SHARE", 0.0)
    assert len({jockey.pick(n_picked=0, now=night).genre for _ in range(30)}) > 1


def test_the_morning_opens_with_hanuman_chalisa_once(db, monkeypatch):
    import json

    from mastgaane import config, db as D, jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(config.settings, "morning_opener", "Hanuman Chalisa Hariharan")
    with D.session() as s:
        hc = D.Song(title="Shree Hanuman Chalisa", artist="Hariharan", norm_key="hc", genre="devotional",
                    mood="devotional", status="approved", yt_video_id="8MGrTEUgBwY")
        s.add(hc)
        s.commit()
        s.refresh(hc)
    (config.settings.data_dir / "morning_opener.json").write_text(json.dumps({"query": "Hanuman Chalisa Hariharan", "song_id": hc.id}))
    morning = datetime(2026, 10, 17, 6, 40)
    first = jockey.pick(n_picked=0, now=morning)
    assert first.opener and first.title == "Shree Hanuman Chalisa"
    link = jockey.template_link(first, first=True, now=morning)
    assert link.startswith("सुप्रभात") and "Shree Hanuman Chalisa" in link and "जय बजरंगबली" in link
    assert not jockey.pick(n_picked=0, now=morning, focus="ghazals").opener  # a wish comes first
    assert not jockey.pick(n_picked=1, now=morning).opener  # only the start of a session
    assert not jockey.pick(n_picked=0, now=datetime(2026, 10, 17, 20, 0)).opener  # mornings only
    from datetime import timezone

    today = datetime.now()
    played_at = today.replace(hour=6, minute=45).astimezone(timezone.utc).replace(tzinfo=None)
    with D.session() as s:
        s.add(D.Play(kind="song", ref=first.ref, outcome="full", at=played_at))
        s.commit()
    assert jockey.opener_due(today.replace(hour=9)) is None  # once a day, even after a restart
    assert jockey.opener_due((today + timedelta(days=1)).replace(hour=7)) is not None  # and again tomorrow
    monkeypatch.setattr(config.settings, "morning_opener", "")
    assert jockey.opener_due(morning) is None


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
    it.artist = ""  # an upload that credits no singer
    for _ in range(10):
        text = template_link(it)
        assert "Kelwa Ke Paat Par" in text and "आवाज़ में" not in text and "लेकर आए" not in text


def test_the_host_feels_the_song():
    from mastgaane.jockey import SONG_LINKS, Item, emotion_of, template_link

    def song(**kw):
        return Item(**{"ref": "song:1", "kind": "song", "title": "Ranjish Hi Sahi", "artist": "Mehdi Hassan", "audio": "/x", **kw})

    assert emotion_of(song(mood="sad|romantic")) == "wistful"
    assert emotion_of(song(mood="energetic")) == "excited"
    assert emotion_of(song(mood="any")) == "warm"
    assert emotion_of(song(mood="any", discovery=True)) == "cheerful"
    assert emotion_of(song(mood="sad", caught=True)) == "excited"
    assert emotion_of(Item(ref="yt:x", kind="episode", title="t", programme="jaimala", audio="/a")) == "nostalgic"
    for _ in range(20):  # every sad template names the singer and the song
        text = template_link(song(mood="sad"))
        assert "Mehdi Hassan" in text and "Ranjish Hi Sahi" in text
        assert any(text == t.format(singer="Mehdi Hassan", title="Ranjish Hi Sahi") for t in SONG_LINKS["wistful"])


def test_each_sentence_gets_its_own_tone():
    from mastgaane.jockey import _delivery, _pieces

    assert _pieces("हाय… ये गीत दिल को छू जाता है। Jagjit Singh… Hothon Se") == [
        "हाय…", "ये गीत दिल को छू जाता है।", "Jagjit Singh…", "Hothon Se"]
    assert _pieces("Wait... what?!") == ["Wait…", "what?!"]
    assert _pieces("no punctuation at all") == ["no punctuation at all"]
    assert len(_pieces("एक। " * 20)) == 8  # long LLM output is capped, the rest rides in the last piece

    sad = _delivery("कुछ दर्द ऐसे होते हैं… सुनिए रंजिश ही सही।", "wistful", "hi-IN-SwaraNeural", 21)
    happy = _delivery("अरे वाह! सुनिए दमादम मस्त कलंदर!", "excited", "hi-IN-SwaraNeural", 21)
    assert all(r < 0 and h < 0 for _, r, h, _ in sad)  # slower and lower
    assert all(r > 0 and h > 0 for _, r, h, _ in happy)  # quicker and brighter
    assert sad[0][1] < sad[1][1]  # slowing into the '…'
    q = _delivery("याद है? सुनिए।", "warm", "hi-IN-SwaraNeural", 21)
    assert q[0][2] > q[1][2]  # a question rises
    late = _delivery("सुनिए।", "warm", "hi-IN-SwaraNeural", 23)[0]
    early = _delivery("सुनिए।", "warm", "hi-IN-SwaraNeural", 21)[0]
    assert late[3] < early[3]  # softer after 11 pm
    male = _delivery("अरे वाह!", "excited", "hi-IN-MadhurNeural", 21)[0]
    female = _delivery("अरे वाह!", "excited", "hi-IN-SwaraNeural", 21)[0]
    assert 0 < male[2] < female[2]  # a deeper voice moves fewer Hz for the same lift
    for _, r, h, v in sad + happy:
        assert -30 <= r <= 30 and -30 <= h <= 30 and -30 <= v <= 20


def test_voice_cache_key_follows_the_delivery(tmp_path, monkeypatch):
    from mastgaane import config, jockey

    monkeypatch.setattr(config.settings, "gemini_api_key", "")  # the edge-tts path, even with a key in .env
    monkeypatch.setattr(jockey, "VOICE_DIR", tmp_path)
    said = []

    async def fake_say(parts, voice):
        said.append(parts)
        return b"mp3"

    monkeypatch.setattr(jockey, "_say", fake_say)
    a = jockey.speak("सुनिए।", "wistful", now=datetime(2026, 10, 16, 21))
    b = jockey.speak("सुनिए।", "excited", now=datetime(2026, 10, 16, 21))
    assert a != b and (tmp_path / f"{a}.mp3").read_bytes() == b"mp3"
    assert jockey.speak("सुनिए।", "wistful", now=datetime(2026, 10, 16, 21)) == a and len(said) == 2  # cached


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


def _play(D, song, outcome, at, mood=""):
    with D.session() as s:
        s.add(D.Play(kind="song", ref=f"song:{song.id}", title=song.title, artist=song.artist, genre=song.genre,
                     outcome=outcome, at=at, mood=mood or song.mood))
        s.commit()


def test_another_upload_of_a_song_just_played_is_a_repeat(db, monkeypatch):
    from sqlmodel import select

    from mastgaane import db as D, jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    with D.session() as s:
        for t, k in (("Hum Tere Shahar Me Aaye Hai", "h1"), ("Hum Tere Shahar Mein Aaye Hain", "h2")):
            s.add(D.Song(title=t, artist="Ghulam Ali", norm_key=k, genre="ghazal", status="approved", yt_video_id=f"{k}vid000000"))
        s.commit()
        first = s.exec(select(D.Song).where(D.Song.norm_key == "h1")).one()
    _play(D, first, "full", D.utcnow())  # live, 17 Oct: both uploads within the hour
    for _ in range(30):
        assert "Shahar" not in jockey.pick().title


def test_one_upload_per_song_in_the_pool():
    from types import SimpleNamespace as S

    from mastgaane.jockey import _fresh

    pool = [S(title="Darmiyaan", artist="Raghav Kaushik"), S(title="Darmiyaan - Unplugged", artist="Raghav Kaushik"),
            S(title="Tum Ho", artist="Mohit Chauhan"), S(title="Tum Hi Ho", artist="Arijit Singh")]
    assert [x.title for x in _fresh(pool, [])] == ["Darmiyaan", "Tum Ho", "Tum Hi Ho"]
    assert [x.title for x in _fresh(pool, [("Darmiyaan (Unplugged)", "Raghav Kaushik")])] == ["Tum Ho", "Tum Hi Ho"]


def test_the_library_plays_and_your_songs_keep_a_share(db, monkeypatch):
    from mastgaane import db as D, jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(jockey, "TIME_SHARE", 0.0)
    with D.session() as s:
        names = [f"{x} {y}" for x in ("Baharon", "Chandni", "Dilruba", "Gulshan", "Jhoom", "Kashti") for y in
                 ("Phool", "Raat", "Saathi", "Tara", "Vaada", "Zameen", "Bagiya", "Doli", "Ghata", "Mehfil")]
        for i, name in enumerate(names):
            s.add(D.Song(title=name, artist="Mohammed Rafi", norm_key=f"lib{i}", genre="film", status="library",
                         yt_video_id=f"lib{i:08d}"))
        s.commit()
    monkeypatch.setattr(jockey, "FAVOURITES_SHARE", 1.0)
    assert all(jockey.pick(n_picked=1).title.startswith("Kept") for _ in range(10))
    monkeypatch.setattr(jockey, "FAVOURITES_SHARE", 0.0)
    got = [jockey.pick(n_picked=1).title for _ in range(40)]
    assert sum(not t.startswith("Kept") for t in got) > 25


def test_the_hour_learns_what_you_like_then(db):
    from sqlmodel import select

    from mastgaane import db as D, jockey

    with D.session() as s:
        sad = s.exec(select(D.Song).where(D.Song.norm_key == "k3")).one()
        sad.mood = "sad"
        dance = s.exec(select(D.Song).where(D.Song.norm_key == "k0")).one()
        dance.mood = "energetic"
        s.commit()
        s.refresh(sad), s.refresh(dance)
    night = datetime.now().replace(hour=23, minute=0).astimezone().astimezone(__import__("datetime").timezone.utc)
    night = night.replace(tzinfo=None)
    for _ in range(10):
        _play(D, sad, "full", night)
    for _ in range(5):
        _play(D, dance, "skip", night)
    part = jockey.hour_taste()["night"]
    assert part["mood:sad"] == 10 and part["mood:energetic"] == -5
    assert jockey._hour_weight(sad, part) > 2 > 1 > jockey._hour_weight(dance, part)
    assert jockey._hour_weight(sad, jockey.hour_taste().get("morning", {})) == 1.0  # learned at night only


def test_the_morning_starts_with_a_few_bhajans(db, monkeypatch):
    """The owner's wish (18 Oct 2026): after the opener, the first picks of a morning session are bhajans;
    then the usual mix. Only mornings, only when there are bhajans to play, never over a spoken wish."""
    from mastgaane import config, db as D, jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    monkeypatch.setattr(jockey, "TIME_SHARE", 0.0)
    monkeypatch.setattr(config.settings, "morning_opener", "")
    monkeypatch.setattr(config.settings, "morning_bhajans", 3)
    morning, night = datetime(2026, 10, 18, 7, 30), datetime(2026, 10, 18, 22, 0)
    with D.session() as s:
        for i in range(3):
            s.add(D.Song(title=f"Bhajan {i}", artist="Anup Jalota", norm_key=f"bj{i}", genre="devotional",
                         status="approved", yt_video_id=f"bhajan0000{i}"))
        s.commit()
    for n in (1, 2, 3):
        assert jockey.pick(n_picked=n, now=morning).genre == "devotional", n
    assert jockey.pick(n_picked=1, now=morning, focus="ghazals").genre == "ghazal"  # a wish comes first
    assert {jockey.pick(n_picked=1, now=night).genre for _ in range(12)} != {"devotional"}  # mornings only
    monkeypatch.setattr(config.settings, "morning_bhajans", 0)
    assert {jockey.pick(n_picked=1, now=morning).genre for _ in range(12)} != {"devotional"}


def test_horror_stories_at_night_and_on_request():
    from mastgaane.archive import PROGRAMMES, _matches
    from mastgaane.jockey import Item, resolve_focus, slot, template_link

    assert slot(datetime(2026, 10, 19, 23, 15)) == "khooni-monday"  # Monday night
    assert slot(datetime(2026, 10, 23, 23, 15)) == "khooni-monday"  # Friday night
    assert slot(datetime(2026, 10, 24, 23, 15)) == "radio-horror"  # Saturday night
    assert slot(datetime(2026, 10, 25, 0, 30)) == "radio-horror"
    assert slot(datetime(2026, 10, 21, 23, 15)) == "chhaya-geet"  # other nights as before
    assert resolve_focus("koi horror story sunao") == ("programme", "khooni-monday")
    assert resolve_focus("भूत की कहानी") == ("programme", "khooni-monday")
    assert resolve_focus("english horror story") == ("programme", "radio-horror")
    assert resolve_focus("adbhut gaane") != ("programme", "khooni-monday")
    # a request word is not a title filter: only the show's own episodes are kept
    km = PROGRAMMES["khooni-monday"]
    assert _matches("Haunted Apsara Talkies | Horror Story in Hindi | Khooni Monday E350", km)
    assert not _matches("Paanch Chhote Bhoot Kood Rahe Hain | Hindi Rhymes", km)
    assert _matches("Inner Sanctum 46-09-23 The Dead Laugh", PROGRAMMES["radio-horror"])
    it = Item(ref="yt:x", kind="episode", title="Haunted Apsara Talkies | Horror Story in Hindi | सच्ची कहानी | Khooni Monday E350🔥",
              programme="khooni-monday", programme_name="Khooni Monday", audio="/x")
    text = template_link(it)
    assert "Haunted Apsara Talkies" in text and "Khooni Monday E350" not in text and "🔥" not in text
