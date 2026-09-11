from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from .. import ports
from ..config import settings
from ..db import PlaylistItem, Reel, Song, session

app = FastAPI(title="MastGaane")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/reports", StaticFiles(directory=str(settings.reports_dir)), name="reports")

#: The port uvicorn really bound. A wish until `serve` tells us (see ports.py);
#: everything that prints a URL for the phone reads it from here rather than
#: assuming the default (GUARDIAN_PLAN.md section 11).
_bound_port = ports.DEFAULT_PORT


def set_bound_port(port: int) -> None:
    global _bound_port
    _bound_port = int(port)


def bound_port() -> int:
    return _bound_port


@app.get("/health")
def health():
    """Who is on this port, so a scanner cannot mistake us for another app."""
    return {"app": ports.APP_ID, "status": "ok", "port": _bound_port, "pid": os.getpid()}


def render(request: Request, name: str, **kw):
    return templates.TemplateResponse(request, name, {"settings": settings, **kw})


@app.get("/")
def songs(request: Request, status: Optional[str] = None, genre: Optional[str] = None, q: Optional[str] = None):
    with session() as s:
        stmt = select(Song).order_by(Song.last_seen.desc())
        if status:
            stmt = stmt.where(Song.status == status)
        if genre:
            stmt = stmt.where(Song.genre == genre)
        rows = s.exec(stmt.limit(300)).all()
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in r.title.lower() or ql in r.artist.lower()]
        reels_by_song: dict[int, list[Reel]] = {}
        for r in s.exec(select(Reel).where(Reel.song_id.in_([x.id for x in rows]))).all():  # type: ignore[attr-defined]
            reels_by_song.setdefault(r.song_id, []).append(r)
        counts = {st: len(s.exec(select(Song).where(Song.status == st)).all()) for st in ("pending", "approved", "rejected")}
    return render(request, "songs.html", songs=rows, reels_by_song=reels_by_song, status=status, genre=genre, q=q or "", counts=counts)


@app.get("/reels")
def reels(request: Request, status: Optional[str] = None, watched: Optional[int] = None):
    with session() as s:
        stmt = select(Reel).order_by(Reel.seen_at.desc())
        if status:
            stmt = stmt.where(Reel.resolve_status == status)
        if watched:
            stmt = stmt.where(Reel.watched == True)  # noqa: E712
        rows = s.exec(stmt.limit(300)).all()
        songs = {x.id: x for x in s.exec(select(Song).where(Song.id.in_([r.song_id for r in rows if r.song_id]))).all()}  # type: ignore[attr-defined]
    return render(request, "reels.html", reels=rows, songs=songs, status=status, watched=watched)


@app.get("/playlist")
def playlist(request: Request):
    with session() as s:
        rows = s.exec(select(Song).where(Song.status == "approved").order_by(Song.first_seen.desc())).all()
        items = s.exec(select(PlaylistItem)).all()
    ids = [x.yt_video_id for x in rows if x.yt_video_id]
    yt_pl = next((i.playlist_id for i in items if i.target == "ytmusic"), None)
    sp_pl = next((i.playlist_id for i in items if i.target == "spotify"), None)
    return render(request, "playlist.html", songs=rows, yt_ids=ids, yt_playlist=yt_pl, sp_playlist=sp_pl)


# ----------------------------------------------------------------------------- Apple Music
def _lan_ip() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("8.8.8.8", 80))
            return sk.getsockname()[0]
    except Exception:
        return "127.0.0.1"


@app.get("/apple")
def apple_page(request: Request, resolve: int = 0):
    """Phone page: approved songs with Apple Music links (+ Shortcut recipe)."""
    from ..playlists.apple import AppleMusicAdapter, itunes_search

    adapter = AppleMusicAdapter()
    with session() as s:
        rows = s.exec(select(Song).where(Song.status == "approved").order_by(Song.first_seen.desc())).all()
        if resolve:  # resolve missing links on demand (iTunes Search API, no key)
            for song in rows:
                if not song.apple_url:
                    link = itunes_search(song.title, song.artist)
                    if link:
                        song.apple_url = link.url
            s.commit()
            for song in rows:
                s.refresh(song)
    # The port we are actually on, not the one we usually take.
    port = request.url.port or bound_port()
    return render(request, "apple.html", songs=rows, lan_url=f"http://{_lan_ip()}:{port}", musickit=adapter.musickit_ready(), dev_token=bool(settings.apple_dev_token))


@app.get("/apple/import.txt")
def apple_import_txt():
    from fastapi.responses import PlainTextResponse

    from ..playlists.apple import AppleMusicAdapter

    with session() as s:
        rows = s.exec(select(Song).where(Song.status == "approved").order_by(Song.first_seen)).all()
    return PlainTextResponse("\n".join(AppleMusicAdapter.import_lines(rows)) + "\n")


@app.get("/apple/auth")
def apple_auth(request: Request):
    """MusicKit JS page — only useful with APPLE_DEV_TOKEN (Apple Developer Program)."""
    return render(request, "apple_auth.html", dev_token=settings.apple_dev_token)


@app.post("/apple/token")
def apple_token(token: str = Form(...)):
    from ..playlists.apple import USER_TOKEN_FILE

    USER_TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    return RedirectResponse("/apple?msg=Apple+Music+connected", status_code=303)


@app.post("/songs/{song_id}/status")
def set_status(song_id: int, status: str = Form(...), back: str = Form("/")):
    with session() as s:
        song = s.get(Song, song_id)
        if song:
            song.status = status
            if status in ("approved", "rejected"):
                song.keep = status == "approved"
            s.commit()
    return RedirectResponse(back, status_code=303)


@app.post("/songs/{song_id}/edit")
def edit_song(song_id: int, title: str = Form(...), artist: str = Form(""), back: str = Form("/")):
    from ..resolver.common import norm_key

    with session() as s:
        song = s.get(Song, song_id)
        if song:
            song.title, song.artist, song.norm_key = title.strip(), artist.strip(), norm_key(title, artist)
            song.yt_video_id = song.spotify_uri = song.apple_url = None  # re-search on next sync
            for pi in s.exec(select(PlaylistItem).where(PlaylistItem.song_id == song_id)).all():
                s.delete(pi)
            s.commit()
    return RedirectResponse(back, status_code=303)


@app.post("/sync")
def do_sync(back: str = Form("/playlist")):
    from ..playlists.sync import sync

    sync()
    return RedirectResponse(back, status_code=303)


@app.post("/spotify/play")
def spotify_play():
    from ..playlists.spotify import SpotifyAdapter

    a = SpotifyAdapter()
    msg = "Spotify not configured"
    if a.available():
        with session() as s:
            item = s.exec(select(PlaylistItem).where(PlaylistItem.target == "spotify")).first()
        msg = a.play(item.playlist_id) if item else "Sync to Spotify first"
    return RedirectResponse(f"/playlist?msg={msg}", status_code=303)
