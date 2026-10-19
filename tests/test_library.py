"""The big collection (library.py): filters, labels, the after-2010 cap, resuming — no network, no LLM."""
from test_player import db  # noqa: F401  (fixture)


def test_junk_and_compilations():
    from mastgaane.library import compilation, junk

    assert not junk("Lag Ja Gale", 250)
    for t in ("Lag Ja Gale (Lofi)", "Tum Hi Ho - Remix", "Old Is Gold Jhankar Beats", "90s Hits Jukebox",
              "Abhi Na Jao Chhod Kar Revival", "Ye Dil Tum Bin Lagta NahinRevival", "Pyar Hua Slowed + Reverb", "Chhaila Babu With Dialogue"):
        assert junk(t), t
    assert junk("Ek Pyar Ka Nagma Hai", 30) and junk("Ek Pyar Ka Nagma Hai", 3600)  # a clip / a jukebox
    assert compilation("Magical Mohammed Rafi", "Mohammed Rafi") and compilation("Meri Awaaz Suno - Mohammed Rafi", "Mohammed Rafi")
    assert compilation("Best of R.D. Burman", "Kishore Kumar")
    assert not compilation("Hum Dono", "Mohammed Rafi") and not compilation("Roti (Original Motion Picture Soundtrack)", "Kishore Kumar")


def test_labels_are_read_line_by_line():
    from mastgaane.library import parse_labels

    out = parse_labels("Sure!\n1|hindi|film|romantic|1977|yes\n3|bengali|folk|peaceful|1970|yes\n2|hindi|film|whatever|0|no\n", 4)
    assert (out[0].language, out[0].genre, out[0].mood, out[0].year, out[0].keep) == ("hindi", "film", "romantic", 1977, True)
    assert out[1].mood == "any" and not out[1].keep and out[1].year == 0
    assert out[2].language == "bengali" and out[3] is None


class FakeYT:
    def __init__(self, tracks):
        self.tracks = tracks
        self.albums = {"alb-film": {"year": "1975", "title": "Sholay"}, "alb-new": {"year": "2015", "title": "Piku"}}

    def search(self, q, filter=None, limit=5):
        return [{"browseId": "UC1", "artist": q}] if filter == "artists" else []

    def get_artist(self, browse_id):
        return {"songs": {"browseId": "VLsongs"}, "albums": {}}

    def get_playlist(self, pid, limit=100):
        return {"tracks": self.tracks}

    def get_album(self, aid):
        return self.albums[aid]


def _t(title, vid, artist="Kishore Kumar", album=None, seconds=240):
    return {"title": title, "videoId": vid, "artists": [{"name": artist}], "album": album, "duration_seconds": seconds}


LABELS = {  # title → n|language|genre|mood|year|keep, as the LLM would answer
    "Yeh Dosti": "hindi|film|happy|1975|yes", "Mere Sapnon Ki Rani": "hindi|film|romantic|1969|yes",
    "Ami Je Ke Tomar": "bengali|film|romantic|1980|yes", "Teri Galiyan": "hindi|film|sad|2014|yes",
    "Moh Moh Ke Dhaage": "hindi|film|romantic|2015|yes", "Chaand Taare": "hindi|film|happy|1997|yes",
    "Khaike Paan": "hindi|film|energetic|1978|no", "Rang De Lal": "hindi|film|energetic|2026|yes",
}


def _labeller(calls):
    from mastgaane.library import parse_labels

    def label(rows):
        calls.append([r.title for r in rows])
        return parse_labels("\n".join(f"{i}|{LABELS[r.title]}" for i, r in enumerate(rows, 1)), len(rows))
    return label


def _setup(monkeypatch, modern_max=1):
    from mastgaane import library

    monkeypatch.setattr(library, "config", lambda: {"target": 100, "modern_max": modern_max, "per_artist": 50,
                                                    "per_playlist": 10, "artists": {"Kishore Kumar": 50}, "playlists": []})
    monkeypatch.setattr(library, "rules", lambda: {"allowed_languages": ["hindi", "urdu"],
                                                   "allowed_genres": ["film", "ghazal"], "always_skip_patterns": []})
    monkeypatch.setattr(library, "PAUSE_S", 0)


def test_build_fills_the_shelf_once(db, monkeypatch):
    from sqlmodel import select

    from mastgaane import library

    _setup(monkeypatch, modern_max=1)  # the 4 songs from 2015 there are folk: they never count; room for one
    monkeypatch.setattr(library.Shelf, "era", lambda self, source, more=(): None)  # the remake rule has its own test
    yt = FakeYT([
        _t("Yeh Dosti", "v1", album={"name": "Sholay", "id": "alb-film"}),
        _t("Mere Sapnon Ki Rani", "v2", album={"name": "Magical Kishore Kumar", "id": "alb-x"}),  # compilation
        _t("Ami Je Ke Tomar", "v3"),  # Bengali
        _t("Yeh Dosti - Remix", "v4"),  # junk, never labelled
        _t("Teri Galiyan", "v5", artist="Ankit Tiwari, Kishore Kumar"),  # 2014: the one after-2010 song allowed
        _t("Moh Moh Ke Dhaage", "v6", artist="Papon, Kishore Kumar", album={"name": "Dum Laga Ke Haisha", "id": "alb-new"}),  # over the cap
        _t("Rang De Lal", "v10", artist="Shashwat Sachdev"),  # 2026 under an old name, not theirs: a stray / remake
        _t("Chaand Taare", "v7", artist="Abhijeet"),
        _t("Khaike Paan", "v8"),  # the LLM: not as sung
        _t("Ghazal 0", "v9", artist="Jagjit Singh"),  # already in the collection (another upload)
    ])
    calls = []
    stats = library.build(label=_labeller(calls), yt=yt, log=lambda m: None)
    assert stats.kept == 4 and stats.modern == 1
    with db.session() as s:
        got = {x.title: x for x in s.exec(select(db.Song).where(db.Song.source_tier == library.TIER_LIBRARY)).all()}
    assert {t for t, x in got.items() if x.status == "library"} == {"Yeh Dosti", "Mere Sapnon Ki Rani", "Teri Galiyan",
                                                                    "Chaand Taare"}
    assert got["Yeh Dosti"].year == 1975 and got["Yeh Dosti"].mood == "happy"  # the film album's year
    assert got["Mere Sapnon Ki Rani"].year == 1969  # a compilation's year is not the song's: the LLM's
    assert got["Ami Je Ke Tomar"].status == "rejected" and "bengali" in got["Ami Je Ke Tomar"].classify_reason
    assert got["Moh Moh Ke Dhaage"].status == "rejected" and "2010" in got["Moh Moh Ke Dhaage"].classify_reason
    assert got["Rang De Lal"].status == "rejected" and "not Kishore Kumar's" in got["Rang De Lal"].classify_reason
    assert "Yeh Dosti - Remix" not in got and "Ghazal 0" not in got
    assert all("Remix" not in t and "Ghazal 0" not in t for batch in calls for t in batch)
    # a second run: everything was looked at already — no LLM call, nothing new
    calls.clear()
    assert library.build(label=_labeller(calls), yt=yt, log=lambda m: None).kept == 0 and calls == []
    assert library.summary()["songs"] >= 3


def test_an_old_composers_new_albums_are_skipped(db, monkeypatch):
    from mastgaane import library

    class YT(FakeYT):
        def get_artist(self, browse_id):
            return {"songs": {}, "albums": {"results": [{"browseId": "a-new", "year": "2016"}, {"browseId": "a-old"}]}}

        def get_album(self, aid):
            return {"a-old": {"year": "1971", "title": "Kati Patang", "tracks": [_t("Yeh Shaam Mastani", "o1")]},
                    "a-new": {"year": "2016", "title": "Wajah Tum Ho", "tracks": [_t("Dil Mein Chhupa Loonga", "n1")]}}[aid]

    rows = library.artist_rows(YT([]), "R. D. Burman", cap=10)
    assert [(r.title, r.album_year) for r in rows] == [("Yeh Shaam Mastani", 1971)]


def test_the_library_stops_at_the_target(db, monkeypatch):
    from mastgaane import library

    _setup(monkeypatch, modern_max=10)
    monkeypatch.setattr(library, "config", lambda: {"target": 9, "modern_max": 10, "per_artist": 50, "per_playlist": 10,
                                                    "artists": {"Kishore Kumar": 50}, "playlists": []})
    yt = FakeYT([_t(t, f"w{i}") for i, t in enumerate(LABELS)])
    assert library.build(label=_labeller([]), yt=yt, log=lambda m: None).kept == 1  # 8 kept songs were there


def test_a_song_from_2010_on_under_an_old_name_is_a_remake(db, monkeypatch):
    from mastgaane import library

    shelf = library.Shelf()
    shelf.years["Kavita Krishnamurthy"] = [1989, 1991, 1993, 1994, 1995, 1996, 1997, 1999, 2001]
    shelf.years["Shreya Ghoshal"] = [2002, 2005, 2009, 2011, 2012, 2014, 2015, 2017, 2019]
    c = {"modern_max": 200}
    lab = library.Label(language="hindi", genre="film", mood="happy", year=2018, keep=True)
    ilu = library.Row("Ilu Ilu", "Kavita Krishnamurthy, Manhar Udhas", "x1", source="Kavita Krishnamurthy")
    status, year, why = library._decide(ilu, lab, shelf, c, {"hindi"}, {"film"})
    assert status == "rejected" and "a remake" in why and "1990s" in why
    ghoomar = library.Row("Ghoomar", "Shreya Ghoshal, Swaroop Khan", "x2", source="Shreya Ghoshal")
    assert library._decide(ghoomar, lab, shelf, c, {"hindi"}, {"film"})[0] == "library"
    young = library.Row("Naya Gaana", "Someone New", "x3", source="Someone New")  # too few songs to tell
    assert library._decide(young, lab, shelf, c, {"hindi"}, {"film"})[0] == "library"


def test_a_singer_without_a_page_is_found_by_their_songs(db):
    from mastgaane import library

    class YT(FakeYT):
        def search(self, q, filter=None, limit=5):
            if filter == "artists":
                return [{"browseId": "UCa", "artist": "Ghulam Ali Khan"}, {"browseId": "UCb", "artist": "Ghulam Ali"}]
            return [_t("Chupke Chupke Raat Din", "g1", artist="Ghulam Ali"), _t("Other", "g2", artist="Someone")]

        def get_artist(self, browse_id):
            return {}  # a page with no songs

    assert [r.title for r in library.artist_rows(YT([]), "Ghulam Ali", cap=10)] == ["Chupke Chupke Raat Din"]


def test_a_playlists_view_count_is_not_a_singer():
    from mastgaane.library import _row

    row = _row({"title": "Om Jai Shiv Omkara", "videoId": "v", "artists": [{"name": "939K views"}]}, "playlist: x")
    assert row.artist == ""
    row = _row({"title": "Woh Din Yaad Karo", "videoId": "v", "artists": [{"name": "Lata Mangeshkar"}, {"name": "Jan 21, 2022"},
                                                                          {"name": "90's Gaane"}]}, "playlist: x")
    assert row.artist == "Lata Mangeshkar"


def test_folk_and_bhajans_are_never_cut_by_the_2010_line(db):
    """A recording of a lokgeet or a bhajan carries the upload's year: it neither counts against
    modern_max nor is a remake (live, 17 Oct: a 2016 chhath geet rejected as "the 200 songs from 2010 on")."""
    from mastgaane import library

    shelf = library.Shelf()
    shelf.modern = 200
    shelf.years["Sharda Sinha"] = [1980, 1985, 1988, 1990, 1992, 1995, 1998, 1999]
    c = {"modern_max": 200}
    folk = library.Label(language="bhojpuri", genre="folk", mood="devotional", year=2016, keep=True)
    chhath = library.Row("Ho Deenanath", "Sharda Sinha", "c1", source="Sharda Sinha")
    status, year, why = library._decide(chhath, folk, shelf, c, {"bhojpuri"}, {"folk"})
    assert status == "library" and year == 2016
    film = library.Label(language="hindi", genre="film", mood="happy", year=2016, keep=True)
    assert library._decide(chhath, film, shelf, c, {"hindi"}, {"film"})[0] == "rejected"


def test_a_modern_composers_singers_are_not_strays(db):
    """Under Vishal-Shekhar (their songs: the 2010s) a 2014 song sung by someone else is theirs, not a
    remake; under Kalyanji-Anandji (the 1980s) it is a stray."""
    from mastgaane import library

    shelf = library.Shelf()
    shelf.years["Vishal-Shekhar"] = [2008, 2010, 2011, 2012, 2013, 2014, 2016, 2018]
    shelf.years["Kalyanji-Anandji"] = [1972, 1975, 1978, 1980, 1982, 1985, 1988, 1990]
    c = {"modern_max": 200}
    lab = library.Label(language="hindi", genre="film", mood="romantic", year=2014, keep=True)
    song = library.Row("Manwa Laage", "Arijit Singh, Shreya Ghoshal", "v1", source="Vishal-Shekhar")
    assert library._decide(song, lab, shelf, c, {"hindi"}, {"film"})[0] == "library"
    stray = library.Row("Manwa Laage", "Arijit Singh, Shreya Ghoshal", "v2", source="Kalyanji-Anandji")
    assert "not Kalyanji-Anandji's" in library._decide(stray, lab, shelf, c, {"hindi"}, {"film"})[2]


def test_an_uploads_name_is_cut_to_the_song():
    from mastgaane.library import song_name

    names = {"latamangeshkar": "Lata Mangeshkar", "geetadutt": "Geeta Dutt", "noorjehan": "Noor Jehan"}
    assert song_name("Kaise Rahoon Chup [HD] Video Song : Lata Mangeshkar | Sadhana, Sanjay Khan | Inteqam", names) == \
        ("Kaise Rahoon Chup", ["Lata Mangeshkar"])
    assert song_name("Ae Dil Mujhe Bata De Video Song In COLOR | Geeta Dutt | Ashok Kumar, Shyama", names) == \
        ("Ae Dil Mujhe Bata De", ["Geeta Dutt"])
    assert song_name("Babuji Dheere Chalna (HD) - Aar Paar Song - Geeta Dutt - Guru Dutt", names)[0] == "Babuji Dheere Chalna"
    assert song_name("Lata Mangeshkar | Lag Ja Gale", names) == ("Lag Ja Gale", ["Lata Mangeshkar"])
    assert song_name('Dil Se Re (From "Dil Se")')[0] == "Dil Se Re"
    assert song_name("Tose Naina Lage audio with lyrics")[0] == "Tose Naina Lage"
    assert song_name("Dil Tera Deewana Hai Sanam, Song in Colour")[0] == "Dil Tera Deewana Hai Sanam"
    assert song_name("𝐄𝐤 𝐏𝐚𝐫𝐝𝐞𝐬𝐢 𝐌𝐞𝐫𝐚 𝐃𝐢𝐥 𝐋𝐞 𝐆𝐚𝐲𝐚")[0] == "Ek Pardesi Mera Dil Le Gaya"
    # a dash with no known singer may put the film first ("Tansen - Kahe Guman Kare"): the name stays whole
    assert song_name("Tansen - Kahe Guman Kare Ri Gori", names)[0] == "Tansen - Kahe Guman Kare Ri Gori"


def test_a_channel_is_not_a_singer(db):
    from mastgaane import library

    row = library._row({"title": "Neeli Neeli Ghata | Mukesh", "videoId": "v",
                        "artists": [{"name": "Bollywood Classics"}]}, "playlist: x")
    assert row.artist == ""
    assert library._row({"title": "Chupke Chupke", "videoId": "v", "artists": [{"name": "Pankaj Udhas | (Music Arranged By : X)"}]},
                        "x").artist == "Pankaj Udhas"
    shelf = library.Shelf()
    shelf.names = {"mukesh": "Mukesh", "ashabhosle": "Asha Bhosle"}
    r = library.Row("Neeli Neeli Ghata | Mukesh, Asha Bhosle | Hum Hindustani", "Navsaar Movies", "v")
    shelf.tidy(r)  # the channel's name: the title's singers win
    assert (r.title, r.artist) == ("Neeli Neeli Ghata", "Mukesh, Asha Bhosle")
    r = library.Row("Jaane Do Mujhe", "Asha Bhosle", "v")
    shelf.tidy(r)
    assert (r.title, r.artist) == ("Jaane Do Mujhe", "Asha Bhosle")


def test_a_podcast_is_not_a_playlist_and_a_failed_fetch_is_not_kept(db, monkeypatch, tmp_path):
    from mastgaane import library

    class YT(FakeYT):
        def search(self, q, filter=None, limit=5):
            return [{"browseId": "MPSPPLx"}, {"browseId": "VLPLy"}]

        def get_playlist(self, pid, limit=100):
            assert not pid.startswith("MPSP")
            return {"tracks": self.tracks}

    assert [r.title for r in library.playlist_rows(YT([_t("Purin Paat Per", "p1")]), "vivah geet")] == ["Purin Paat Per"]
    monkeypatch.setattr(library.settings, "data_dir", tmp_path)
    assert library._cached("playlist: x", lambda: []) == []
    assert not list((tmp_path / "library_sources").iterdir())


def test_an_upload_with_no_songs_name_is_skipped(db, monkeypatch):
    from mastgaane import library

    _setup(monkeypatch, modern_max=10)
    # both keep no song's name ("|kishorekumar"): the second crashed the run on the unique norm_key (live, 19 Oct)
    yt = FakeYT([_t("#video BEST BHOJPURI GAZAL", "z1"), _t("#video OLD BHOJPURI GAZAL", "z2"), _t("Yeh Dosti", "z3")])
    calls = []
    assert library.build(label=_labeller(calls), yt=yt, log=lambda m: None).kept == 1
    assert calls == [["Yeh Dosti"]]
