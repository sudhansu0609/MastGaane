"""YouTube Music via ytmusicapi (browser-header auth, no API quota).

One-time setup:  mastgaane setup ytmusic   (pastes request headers into browser.json)
"""
from __future__ import annotations

from ..config import settings
from .base import Track, score_match


class YouTubeMusicAdapter:
    name = "ytmusic"

    def available(self) -> bool:
        return settings.ytmusic_enabled and settings.ytmusic_auth_file.exists()

    _client = None

    def _yt(self):
        if self._client is None:
            from ytmusicapi import YTMusic

            self._client = YTMusic(str(settings.ytmusic_auth_file))
        return self._client

    def search(self, title: str, artist: str) -> Track | None:
        yt = self._yt()
        best: tuple[float, Track] | None = None
        for flt in ("songs", "videos"):
            try:
                hits = yt.search(f"{title} {artist}".strip(), filter=flt, limit=5)
            except Exception:
                hits = []
            for h in hits:
                vid = h.get("videoId")
                if not vid:
                    continue
                r_artist = ", ".join(a.get("name", "") for a in h.get("artists", []) or [])
                sc = score_match(title, artist, h.get("title", ""), r_artist) + (0.05 if flt == "songs" else 0)
                t = Track(id=vid, title=h.get("title", ""), artist=r_artist, url=f"https://music.youtube.com/watch?v={vid}")
                if best is None or sc > best[0]:
                    best = (sc, t)
            if best and best[0] >= 0.9:
                break
        if best and best[0] >= 0.5:
            return best[1]
        return None

    def ensure_playlist(self, name: str) -> str:
        yt = self._yt()
        for pl in yt.get_library_playlists(limit=100) or []:
            if pl.get("title") == name:
                return pl["playlistId"]
        return yt.create_playlist(name, "Late-night reels, auto-curated by MastGaane", privacy_status="PRIVATE")

    def add(self, playlist_id: str, track: Track) -> None:
        self._yt().add_playlist_items(playlist_id, [track.id], duplicates=False)

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={playlist_id}"
