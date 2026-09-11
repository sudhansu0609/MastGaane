"""Apple Music — two modes.

1. **No developer account (default):** each song gets an Apple Music link (iTunes Search API,
   Odesli as backup). The web UI serves `/apple` — a phone page with "Open in Apple Music"
   buttons — and `/apple/import.txt`, which an iPhone Shortcut turns into a real playlist
   (recipe: `mastgaane setup apple`).

2. **MusicKit (Apple Developer Program, $99/yr):** set APPLE_DEV_TOKEN in .env, open
   `/apple/auth` once on any browser to grant access (stores the Music User Token), and
   `sync` then creates/updates the playlist in your library directly.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import settings
from .base import Track, score_match

ODESLI = "https://api.song.link/v1-alpha.1/links"
ITUNES = "https://itunes.apple.com/search"
MUSICKIT = "https://api.music.apple.com/v1"
STOREFRONT = "in"
USER_TOKEN_FILE = settings.data_dir / "apple_user_token.txt"


@dataclass
class AppleLink:
    url: str
    title: str
    artist: str
    artwork: str = ""


def itunes_search(title: str, artist: str, limit: int = 5) -> AppleLink | None:
    """Public iTunes Search API — no key needed. Returns the best-scoring hit."""
    try:
        r = httpx.get(ITUNES, params={"term": f"{title} {artist}".strip(), "entity": "song", "country": STOREFRONT.upper(), "limit": limit}, timeout=20)
        res = r.json().get("results", [])
    except Exception:
        return None
    best = None
    for it in res:
        sc = score_match(title, artist, it.get("trackName", ""), it.get("artistName", ""))
        if best is None or sc > best[0]:
            best = (sc, it)
    if best is None or best[0] < 0.5:
        return None
    it = best[1]
    return AppleLink(url=it.get("trackViewUrl", ""), title=it.get("trackName", ""), artist=it.get("artistName", ""), artwork=(it.get("artworkUrl100") or "").replace("100x100", "300x300"))


def odesli_apple_url(url: str) -> str | None:
    """Convert a YouTube/Spotify track URL to an Apple Music URL via Odesli (song.link)."""
    try:
        r = httpx.get(ODESLI, params={"url": url, "userCountry": STOREFRONT.upper()}, timeout=20)
        if r.status_code != 200:
            return None
        return (r.json().get("linksByPlatform", {}).get("appleMusic") or {}).get("url")
    except Exception:
        return None


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
            best = None
            for s in songs:
                a = s.get("attributes", {})
                sc = score_match(title, artist, a.get("name", ""), a.get("artistName", ""))
                if best is None or sc > best[0]:
                    best = (sc, Track(id=s["id"], title=a.get("name", ""), artist=a.get("artistName", ""), url=a.get("url", "")))
            if best and best[0] >= 0.5:
                return best[1]
        link = itunes_search(title, artist)
        if link:
            return Track(id=link.url, title=link.title, artist=link.artist, url=link.url)
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
        return [f"{s.title} - {s.artist}".strip(" -") for s in songs]

    def write_import_file(self, songs) -> str:
        path = settings.reports_dir / "apple_import.txt"
        path.write_text("\n".join(self.import_lines(songs)) + "\n", encoding="utf-8")
        return str(path)
