"""Spotify via spotipy. Requires a Spotify Premium account (dev-mode rule since Mar 2026)
and an app at https://developer.spotify.com/dashboard with the redirect URI from .env."""
from __future__ import annotations

from ..config import settings
from .base import Track, score_match

SCOPES = "playlist-modify-private playlist-read-private user-modify-playback-state user-read-playback-state"


class SpotifyAdapter:
    name = "spotify"

    def available(self) -> bool:
        return settings.spotify_enabled and bool(settings.spotify_client_id and settings.spotify_client_secret)

    _client = None

    def _sp(self):
        if self._client is not None:
            return self._client
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        auth = SpotifyOAuth(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            scope=SCOPES,
            cache_path=str(settings.data_dir / ".spotify-cache"),
            open_browser=True,
        )
        self._client = spotipy.Spotify(auth_manager=auth)
        return self._client

    def search(self, title: str, artist: str) -> Track | None:
        sp = self._sp()
        res = sp.search(q=f"track:{title} artist:{artist}" if artist else title, type="track", limit=5)
        items = res.get("tracks", {}).get("items", [])
        if not items:
            res = sp.search(q=f"{title} {artist}", type="track", limit=5)
            items = res.get("tracks", {}).get("items", [])
        best = None
        for it in items:
            r_artist = ", ".join(a["name"] for a in it.get("artists", []))
            sc = score_match(title, artist, it.get("name", ""), r_artist)
            t = Track(id=it["uri"], title=it.get("name", ""), artist=r_artist, url=it.get("external_urls", {}).get("spotify", ""))
            if best is None or sc > best[0]:
                best = (sc, t)
        return best[1] if best and best[0] >= 0.5 else None

    def ensure_playlist(self, name: str) -> str:
        sp = self._sp()
        me = sp.current_user()["id"]
        offset = 0
        while True:
            page = sp.current_user_playlists(limit=50, offset=offset)
            for pl in page.get("items", []):
                if pl.get("name") == name:
                    return pl["id"]
            if not page.get("next"):
                break
            offset += 50
        return sp.user_playlist_create(me, name, public=False, description="Late-night reels, auto-curated by MastGaane")["id"]

    def add(self, playlist_id: str, track: Track) -> None:
        sp = self._sp()
        existing = {t["track"]["uri"] for t in sp.playlist_items(playlist_id, fields="items(track(uri))", limit=100).get("items", []) if t.get("track")}
        if track.id not in existing:
            sp.playlist_add_items(playlist_id, [track.id])

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://open.spotify.com/playlist/{playlist_id}"

    def play(self, playlist_id: str) -> str:
        """Start the playlist on the user's active Spotify device. Returns a status message."""
        sp = self._sp()
        devices = sp.devices().get("devices", [])
        if not devices:
            return "No active Spotify device. Open Spotify on any device first."
        dev = next((d for d in devices if d.get("is_active")), devices[0])
        sp.start_playback(device_id=dev["id"], context_uri=f"spotify:playlist:{playlist_id}")
        return f"Playing on {dev.get('name')}"
