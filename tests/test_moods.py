from types import SimpleNamespace

from mastgaane import moods
from mastgaane.jockey import resolve_focus


def song(title="", genre="", year=None, lyrics="", mood=""):
    return SimpleNamespace(title=title, artist="", genre=genre, year=year, lyrics_snippet=lyrics, mood=mood)


def test_contract_word_lists():
    assert moods.MOODS["sad"] == ("sad", "udaas", "gham", "dard", "dukhi", "judaai", "melancholic")
    assert moods.MOODS["romantic"] == ("romantic", "pyaar", "ishq", "mohabbat", "love")
    assert moods.MOODS["energetic"] == ("energetic", "upbeat", "josh", "masti", "dance", "party", "peppy")
    assert moods.MOODS["peaceful"] == ("peaceful", "calm", "sukoon", "shaant", "soft", "relaxing")
    assert moods.MOODS["happy"] == ("happy", "khush", "cheerful")
    assert moods.MOODS["devotional"] == ("devotional", "bhakti")
    assert moods.MOODS["nostalgic"] == ("nostalgic", "yaadein")
    assert moods.TIMES == {"morning": ("morning", "subah"), "afternoon": ("afternoon", "dopahar"),
                           "evening": ("evening", "shaam"), "night": ("night", "raat", "late night", "midnight")}


def test_mood_of_words():
    assert moods.mood_of_words("play something sad") == "sad"
    assert moods.mood_of_words("kuch udaas gaane") == "sad"
    assert moods.mood_of_words("romantic songs") == "romantic"
    assert moods.mood_of_words("something energetic") == "energetic"
    assert moods.mood_of_words("sukoon wale gaane") == "peaceful"
    assert moods.mood_of_words("play some music") is None
    assert moods.mood_of_words("sadhana sargam") is None  # whole words only


def test_time_of_words():
    assert moods.time_of_words("morning songs") == "morning"
    assert moods.time_of_words("late night") == "night"
    assert moods.time_of_words("shaam ke gaane") == "evening"
    assert moods.time_of_words("ghazals") is None


def test_guess_mood():
    assert moods.guess_mood(song("Hothon Se Chhu Lo Tum", genre="ghazal", year=1981)) == "sad|romantic"
    assert moods.guess_mood(song("Hanuman Chalisa", genre="devotional")) == "devotional"
    assert moods.guess_mood(song("Dum Mast Qalandar", genre="sufi")) == "energetic"
    assert "sad" in moods.guess_mood(song("Teri Yaad Aayi", genre="film"))
    assert "romantic" in moods.guess_mood(song("Dil Diyan Gallan", genre="film"))
    assert "energetic" in moods.guess_mood(song("Dhoom Machale", genre="film"))
    assert "peaceful" in moods.guess_mood(song("Chandni Raat", genre="film"))
    assert "nostalgic" in moods.guess_mood(song("Something", genre="film", year=1962))
    assert moods.guess_mood(song("Kaanch Hi Baans Ke", genre="folk", year=2015)) == "any"


def test_stored_mood_wins_over_the_guess():
    assert moods.song_moods(song("Dil Diyan Gallan", mood="happy")) == {"happy"}
    assert moods.song_moods(song("Dil Diyan Gallan")) == {"romantic"}
    assert moods.song_moods(song("x", mood="any")) == set()


def test_resolve_focus_moods_times_and_fuzzy_programmes():
    assert resolve_focus("chaya geet") == ("programme", "chhaya-geet")
    assert resolve_focus("hawa mehal") == ("programme", "hawa-mahal")
    assert resolve_focus("jaimaala") == ("programme", "jaimala")
    assert resolve_focus("something energetic") == ("mood", "energetic")
    assert resolve_focus("something sad") == ("mood", "sad")
    assert resolve_focus("romantic songs") == ("mood", "romantic")
    assert resolve_focus("morning songs") == ("time", "morning")
    assert resolve_focus("late night") == ("time", "night")
    # the older wishes are untouched
    assert resolve_focus("play some ghazals") == ("genre", "ghazal")
    assert resolve_focus("songs by Jagjit Singh") == ("text", "jagjit singh")
    assert resolve_focus("play some music") == ("any", "")


def test_time_to_wish():
    assert moods.time_to_wish("night", 15) == ("programme", "chhaya-geet")
    assert moods.time_to_wish("morning", 15) == ("programme", "sangeet-sarita")
    assert moods.time_to_wish("evening", 19) == ("programme", "jaimala")


def test_time_to_wish_without_a_programme_is_a_mood(monkeypatch):
    from mastgaane import jockey

    monkeypatch.setattr(jockey, "slot", lambda now=None: None)
    assert moods.time_to_wish("morning", 8) == ("mood", "devotional")
    assert moods.time_to_wish("afternoon", 8) == ("mood", "happy")
    assert moods.time_to_wish("evening", 8) == ("mood", "romantic")
    assert moods.time_to_wish("night", 8) == ("mood", "sad")
