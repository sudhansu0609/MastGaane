"""Push approved songs to every enabled playlist target (idempotent)."""
from __future__ import annotations

from rich.console import Console
from sqlmodel import select

from ..config import settings
from ..db import PlaylistItem, Run, Song, session, utcnow
from .apple import AppleMusicAdapter
from .base import Track
from .spotify import SpotifyAdapter
from .youtube_music import YouTubeMusicAdapter

console = Console()

ADAPTERS = {"ytmusic": YouTubeMusicAdapter, "spotify": SpotifyAdapter, "apple": AppleMusicAdapter}


def sync(targets: list[str] | None = None) -> Run:
    targets = targets or list(ADAPTERS)
    with session() as s:
        run = Run(kind="sync")
        s.add(run)
        s.commit()
        s.refresh(run)
        approved = s.exec(select(Song).where(Song.status == "approved").order_by(Song.first_seen)).all()
        console.print(f"Syncing {len(approved)} approved songs → {targets}")
        added_total = 0
        for name in targets:
            adapter = ADAPTERS[name]()
            if not adapter.available():
                console.print(f"[dim]{name}: not configured, skipping[/]")
                continue
            try:
                playlist_id = adapter.ensure_playlist(settings.playlist_name)
            except Exception as e:
                console.print(f"[red]{name}: cannot open playlist: {e}[/]")
                continue
            done = {pi.song_id for pi in s.exec(select(PlaylistItem).where(PlaylistItem.target == name)).all()}
            for song in approved:
                if song.id in done:
                    continue
                try:
                    track = adapter.search(song.title, song.artist)
                    if track is None and name == "apple" and song.yt_video_id and not adapter.musickit_ready():
                        # iTunes search missed → cross-platform resolve from the YouTube match
                        from .apple import odesli_apple_url

                        url = odesli_apple_url(f"https://www.youtube.com/watch?v={song.yt_video_id}")
                        if url:
                            track = Track(id=url, title=song.title, artist=song.artist, url=url)
                    if track is None:
                        console.log(f"[yellow]{name}: no match for {song.title} — {song.artist}[/]")
                        continue
                    adapter.add(playlist_id, track)
                    if name == "ytmusic":
                        song.yt_video_id = track.id
                    elif name == "spotify":
                        song.spotify_uri = track.id
                    elif name == "apple":
                        song.apple_url = track.url
                    s.add(PlaylistItem(song_id=song.id, target=name, playlist_id=playlist_id, track_id=track.id))
                    s.commit()
                    added_total += 1
                    console.log(f"[green]{name}[/] + {song.title} — {song.artist}")
                except Exception as e:
                    console.log(f"[red]{name}: {song.title}: {e}[/]")
            if name == "apple" and not adapter.musickit_ready():
                path = adapter.write_import_file(approved)
                # The phone page lives wherever `serve` landed, which is not always
                # the preferred port (GUARDIAN_PLAN.md section 11).
                from .. import ports as port_ledger

                phone_port = port_ledger.preferred_port()
                running = port_ledger.running_base_url()
                if running:
                    phone_port = int(running.rsplit(":", 1)[1])
                console.print(f"Apple: links resolved; import list → {path}  (phone page: http://<pc-ip>:{phone_port}/apple)")
            else:
                console.print(f"{name} playlist: {adapter.playlist_url(playlist_id)}")
        run.finished_at = utcnow()
        run.reels_new = added_total
        run.notes = ",".join(targets)
        s.commit()
        s.refresh(run)
        return run
