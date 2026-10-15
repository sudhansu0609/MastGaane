"""Apple Music — two modes.

1. **No developer account (default):** each song gets an Apple Music link (iTunes Search API;
   title and artist must both agree). The web UI serves `/apple` — a phone page with "Open in Apple Music"
   buttons — and `/apple/import.txt`, which an iPhone Shortcut turns into a real playlist
   (recipe: `mastgaane setup apple`).

2. **MusicKit (Apple Developer Program, $99/yr):** set APPLE_DEV_TOKEN in .env, open
   `/apple/auth` once on any browser to grant access (stores the Music User Token), and
   `sync` then creates/updates the playlist in your library directly.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

import httpx

from ..config import settings
from .base import Track, _norm

ITUNES = "https://itunes.apple.com/search"
MUSICKIT = "https://api.music.apple.com/v1"
STOREFRONT = "in"
USER_TOKEN_FILE = settings.data_dir / "apple_user_token.txt"
ITUNES_GAP_S = 3.2  # Apple allows ~20 Search API calls a minute, then answers 403

_last_call = 0.0
_call_lock = threading.Lock()
_resolving = threading.Lock()


@dataclass
class AppleLink:
    id: str  # Apple Music catalog song id (= iTunes trackId) — what MusicKit plays
    url: str
    title: str
    artist: str
    artwork: str = ""


_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b|\bfeat\.?|\bft\.?|\bx\b|\bwith\b)\s*", re.I)


def _names(artist: str) -> set[str]:
    return {n for n in (_norm(p) for p in _SPLIT.split(artist or "")) if len(n) >= 4}


def artists_agree(a: str, b: str) -> bool:
    """At least one credited name is shared ("Udit Narayan,  Shreya Ghoshal" ~ "Udit Narayan & Shreya Ghoshal")."""
    return any(q in r or r in q for q in _names(a) for r in _names(b))


def titles_agree(a: str, b: str) -> bool:
    qa, qb = _norm(a), _norm(b)
    return bool(qa and qb) and (qa in qb or qb in qa)


def _confident(title: str, artist: str, r_title: str, r_artist: str) -> bool:
    """Same title AND a shared artist — a same-named song by someone else is worse than no link."""
    return titles_agree(title, r_title) and (not artist.strip() or artists_agree(artist, r_artist))


def _itunes(params: dict) -> list[dict]:
    global _last_call
    with _call_lock:
        wait = _last_call + ITUNES_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get(ITUNES, params=params, timeout=20)
            return r.json().get("results", []) if r.status_code == 200 else []
        except Exception:
            return []
        finally:
            _last_call = time.monotonic()


def itunes_search(title: str, artist: str, limit: int = 5) -> AppleLink | None:
    """Public iTunes Search API — no key needed. Tries "title artist", then the title alone."""
    terms = [f"{title} {artist}".strip()]
    if artist.strip():
        terms.append(title)
    for term in terms:
        hits = [it for it in _itunes({"term": term, "entity": "song", "country": STOREFRONT.upper(), "limit": limit if term != title else 15})
                if _confident(title, artist, it.get("trackName", ""), it.get("artistName", ""))]
        if hits:
            it = min(hits, key=lambda it: abs(len(_norm(it.get("trackName", ""))) - len(_norm(title))))
            return AppleLink(id=str(it.get("trackId", "")), url=it.get("trackViewUrl", ""), title=it.get("trackName", ""),
                             artist=it.get("artistName", ""), artwork=(it.get("artworkUrl100") or "").replace("100x100", "300x300"))
    return None


def remember(song, track: Track) -> None:
    """Store a match on the Song: the link, the catalog id (FM playback) and Apple's own names (Shortcut search)."""
    song.apple_url = track.url
    song.apple_id = track.id
    song.apple_name = f"{track.title} - {track.artist}".strip(" -")


def resolve_missing() -> int:
    """Find Apple links for approved songs that have none (slow: Apple's rate limit). One run at a time."""
    from sqlmodel import select

    from ..db import Song, session

    if not _resolving.acquire(blocking=False):
        return 0
    found = 0
    try:
        with session() as s:
            for song in s.exec(select(Song).where(Song.status == "approved", Song.apple_id.is_(None))).all():
                link = itunes_search(song.title, song.artist)
                if link:
                    remember(song, Track(id=link.id, title=link.title, artist=link.artist, url=link.url))
                    s.commit()
                    found += 1
    finally:
        _resolving.release()
    return found


def resolving() -> bool:
    return _resolving.locked()


class AppleMusicAdapter:
    name = "apple"

    # ---- mode detection -------------------------------------------------------------
    def available(self) -> bool:
        return settings.apple_enabled

    def musickit_ready(self) -> bool:
        return bool(settings.apple_dev_token) and USER_TOKEN_FILE.exists()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.apple_dev_token}", "Music-User-Token": USER_TOKEN_FILE.read_text(encoding="utf-8").strip()}

    # ---- PlaylistAdapter protocol ---------------------------------------------------
    def search(self, title: str, artist: str) -> Track | None:
        if self.musickit_ready():
            try:
                r = httpx.get(f"{MUSICKIT}/catalog/{STOREFRONT}/search", params={"term": f"{title} {artist}", "types": "songs", "limit": 5}, headers=self._headers(), timeout=20)
                songs = r.json().get("results", {}).get("songs", {}).get("data", [])
            except Exception:
                songs = []
            for song in songs:
                a = song.get("attributes", {})
                if _confident(title, artist, a.get("name", ""), a.get("artistName", "")):
                    return Track(id=song["id"], title=a.get("name", ""), artist=a.get("artistName", ""), url=a.get("url", ""))
        link = itunes_search(title, artist)
        if link:
            return Track(id=link.id, title=link.title, artist=link.artist, url=link.url)
        return None

    def ensure_playlist(self, name: str) -> str:
        if not self.musickit_ready():
            return name  # playlist lives on the phone; created by the Shortcut
        r = httpx.get(f"{MUSICKIT}/me/library/playlists", params={"limit": 100}, headers=self._headers(), timeout=20)
        r.raise_for_status()
        for pl in r.json().get("data", []):
            if pl.get("attributes", {}).get("name") == name:
                return pl["id"]
        r = httpx.post(f"{MUSICKIT}/me/library/playlists", json={"attributes": {"name": name, "description": "Late-night reels, auto-curated by MastGaane"}}, headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json()["data"][0]["id"]

    def add(self, playlist_id: str, track: Track) -> None:
        if not self.musickit_ready():
            return  # accumulated into apple_import.txt by write_import_file()
        r = httpx.post(f"{MUSICKIT}/me/library/playlists/{playlist_id}/tracks", json={"data": [{"id": track.id, "type": "songs"}]}, headers=self._headers(), timeout=20)
        r.raise_for_status()

    def playlist_url(self, playlist_id: str) -> str:
        if self.musickit_ready():
            return f"https://music.apple.com/{STOREFRONT}/library/playlist/{playlist_id}"
        return str(settings.reports_dir / "apple_import.txt")

    # ---- Shortcut import file -------------------------------------------------------
    @staticmethod
    def import_lines(songs) -> list[str]:
        """One search line per song — Apple's own names when we have a match, so the Shortcut finds that exact track."""
        return [getattr(s, "apple_name", None) or f"{s.title} - {s.artist}".strip(" -") for s in songs]

    def write_import_file(self, songs) -> str:
        path = settings.reports_dir / "apple_import.txt"
        path.write_text("\n".join(self.import_lines(songs)) + "\n", encoding="utf-8")
        return str(path)
