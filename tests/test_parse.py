import json
from pathlib import Path

from mastgaane.harvester.parse import extract_items
from mastgaane.harvester.reels_feed import current_shortcode, looks_musical
from mastgaane.resolver.common import clean_title, norm_key

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_clips_home_shape():
    items = {i.shortcode: i for i in extract_items(load("graphql_clips_home.json"))}
    assert set(items) == {"C_abcDEF123", "C_origAUD456"}  # photo post filtered out
    a = items["C_abcDEF123"]
    assert a.song_title == "Tum Itna Jo Muskura Rahe Ho"
    assert a.artist == "Jagjit Singh"
    assert a.audio_id == "987654321"
    assert a.owner == "ghazal.lovers"
    assert a.has_music and not a.is_original_audio
    assert a.video_url.endswith("abc.mp4?x=1")
    assert a.thumb_url.endswith("thumb.jpg")
    assert a.taken_at is not None
    b = items["C_origAUD456"]
    assert b.is_original_audio and not b.has_music
    assert b.artist == "singer.girl"


def test_legacy_shortcode_shape():
    (item,) = extract_items(load("graphql_legacy_shortcode.json"))
    assert item.shortcode == "C_legacy789"
    assert item.song_title == "Tere Ishq Nachaya"
    assert item.artist == "Abida Parveen"
    assert item.owner == "sufi.nights"
    assert "Abida" in item.caption
    assert item.has_music


def test_garbage_payloads_are_ignored():
    assert extract_items({"data": {"foo": [1, 2, {"code": 200}]}}) == []
    assert extract_items([]) == []
    assert extract_items("nope") == []


def test_musical_prefilter():
    items = {i.shortcode: i for i in extract_items(load("graphql_clips_home.json"))}
    assert looks_musical(items["C_abcDEF123"])
    assert looks_musical(items["C_origAUD456"])  # caption says "cover"
    assert not looks_musical(None)


def test_shortcode_from_url():
    assert current_shortcode("https://www.instagram.com/reels/C_abcDEF123/") == "C_abcDEF123"
    assert current_shortcode("https://www.instagram.com/reel/C_abcDEF123/?x=1") == "C_abcDEF123"
    assert current_shortcode("https://www.instagram.com/reels/") is None


def test_norm_key_dedups_variants():
    assert norm_key("Tum Itna Jo Muskura Rahe Ho (From \"Arth\")", "Jagjit Singh") == norm_key("Tum Itna Jo Muskura Rahe Ho", "Jagjit Singh, Chitra Singh")
    assert clean_title("Kesariya (Lofi Version) feat. Arijit") == "Kesariya"
