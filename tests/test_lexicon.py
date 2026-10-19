import pytest

from test_player import db  # noqa: F401  (fixture)


@pytest.mark.parametrize("word", ["bharti", "bharati", "bhaarati", "bharathi", "marthi", "marti", "marty", "smarty",
                                  "smartie", "barty", "bardi", "varti", "bhartee", "bhaarthee", "bhartis"])
def test_how_whisper_mishears_bharti(word):
    from mastgaane.lexicon import heard_name

    assert heard_name(word)


@pytest.mark.parametrize("word", ["party", "pretty", "bright", "brought", "bharat", "marte", "martha", "aarti",
                                  "bhatti", "bhakti", "smart", "mark", "worthy", "thirty"])
def test_words_that_only_look_like_bharti(word):
    from mastgaane.lexicon import heard_name

    assert not heard_name(word)


def test_skeleton_keeps_what_an_accent_keeps():
    from mastgaane.lexicon import score, skeleton

    assert skeleton("Hawa Mahal") == skeleton("how a mehal") == "hwml"
    assert score("jagjeet singh", "Jagjit Singh") > 0.9
    assert score("last time", "Ram Stuti") < 0.76


@pytest.fixture()
def songs(db):  # noqa: F811
    from mastgaane import lexicon

    with db.session() as s:
        for i, (title, artist) in enumerate([("Shree Hanuman Chalisa", "Hariharan"),
                                             ("Pal Pal Dil Ke Paas", "Kishore Kumar"), ("Pal Pal", "Talwiinder"),
                                             ("Tu Hain Toh", "Mohit Chauhan"), ("Paras Nath", "Anup Jalota")]):
            s.add(db.Song(title=title, artist=artist, norm_key=f"l{i}", status="approved", yt_video_id=f"lvid{i:07d}"))
        s.commit()
    lexicon.forget()
    yield db
    lexicon.forget()


@pytest.mark.parametrize("said, meant", [
    ("leharman chalisa", "Hanuman Chalisa"),  # live, 17 Oct
    ("hanuman chalisa", "Hanuman Chalisa"),
    ("jagjeet singh", "Jagjit Singh"),
    ("kishor kumar", "Kishore Kumar"),
    ("pal pal dil ke pass", "Pal Pal Dil Ke Paas"),  # the whole of it, not "Pal Pal"
    ("hawa mehal", "Hawa Mahal"),
])
def test_a_misheard_wish_finds_what_was_meant(songs, said, meant):
    from mastgaane.lexicon import correct_wish

    assert correct_wish(said) == meant


@pytest.mark.parametrize("said", ["present", "last time", "don't want", "the rules", "kali over",
                                  "the food for today", "i will put the camera in the air to get here"])
def test_chatter_names_nothing(songs, said):
    """Each of these "named" a song before the limits (from a day of the room's chatter as Whisper wrote it)."""
    from mastgaane.lexicon import correct_wish

    assert correct_wish(said) is None


def test_playable_falls_back_to_the_lexicon(songs, monkeypatch):
    from mastgaane import config
    from mastgaane.voice_control import playable

    monkeypatch.setattr(config.settings, "voice_wake_words", "bharti,radio,fm")
    assert playable("leharman chalisa") == "Hanuman Chalisa"
    assert playable("kali over") is None
