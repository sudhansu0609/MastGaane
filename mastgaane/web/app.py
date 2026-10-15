from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import select

from .. import ports
from ..config import settings
from ..db import PlaylistItem, Reel, Song, session, set_manual_status

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


@app.get("/discover")
def discover_page(request: Request):
    with session() as s:
        rows = s.exec(select(Song).where(Song.status == "pending", Song.yt_video_id.is_not(None)).order_by(Song.first_seen.desc())).all()
    return render(request, "playlist.html", songs=rows, yt_ids=[x.yt_video_id for x in rows], discover=True)


@app.get("/archive")
def archive_page(request: Request, p: Optional[str] = None, era: Optional[str] = None, refresh: int = 0):
    from collections import Counter

    from .. import archive

    eps = archive.episodes(p, refresh=bool(refresh)) if p in archive.PROGRAMMES else []
    eras = Counter(archive.decade_of(e.year) for e in eps)
    shown = [e for e in eps if archive.decade_of(e.year) == era] if era else eps
    labels = [d[0] for d in archive.DECADES] + ["year unknown"]
    return render(request, "archive.html", programmes=archive.PROGRAMMES, current=p if p in archive.PROGRAMMES else None,
                  episodes=shown, total=len(eps), eras=[(x, eras[x]) for x in labels if eras[x]], era=era)


@app.get("/archive/ia/{identifier}.json")
def archive_ia(identifier: str):
    from .. import archive

    try:
        return archive.ia_files(identifier)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/archive/yt/{video_id}.audio")
def archive_yt(video_id: str):
    from .. import archive

    try:
        path = archive.yt_audio(video_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"could not fetch audio: {str(e)[:200]}")
    return FileResponse(path, media_type=archive.MEDIA_TYPES.get(path.suffix, "application/octet-stream"))


# ----------------------------------------------------------------------------- MastGaane FM
@app.get("/fm")
def fm_page(request: Request):
    return render(request, "fm.html")


@app.get("/jockey/next")
def jockey_next(exclude: str = "", n: int = 0, previous: str = "", talk: int = 1, llm: int = 1):
    from .. import jockey

    item = jockey.next_item([x for x in exclude.split(",") if x], n_picked=n, previous=previous[:200], talk=bool(talk), use_llm=bool(llm))
    if item is None:
        raise HTTPException(404, "nothing to play yet — approve some songs or run `mastgaane discover`")
    return item.model_dump()


class PlayEvent(BaseModel):
    kind: str
    ref: str
    outcome: str
    title: str = ""
    artist: str = ""
    genre: str = ""
    programme: str = ""
    listened_s: float = 0.0
    duration_s: float = 0.0


@app.post("/jockey/event")
def jockey_event(ev: PlayEvent):
    from .. import jockey

    if ev.outcome not in jockey.OUTCOME_SCORE or ev.kind not in ("song", "episode"):
        raise HTTPException(400, "bad event")
    jockey.record(**ev.model_dump())
    return {"ok": True}


@app.get("/jockey/voice/{key}.mp3")
def jockey_voice(key: str):
    from .. import jockey

    path = jockey.VOICE_DIR / f"{key}.mp3"
    if not re.fullmatch(r"[0-9a-f]{20}", key) or not path.exists():
        raise HTTPException(404, "no such announcement")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/jockey/audio/{video_id}")
def jockey_audio(video_id: str):
    return archive_yt(video_id)


@app.get("/naya")
def naya_page(request: Request):
    from .. import digest

    return render(request, "naya.html", d=digest.latest())


@app.post("/naya/build")
def naya_build():
    from .. import digest

    digest.build()
    return RedirectResponse("/naya", status_code=303)


@app.post("/naya/rule")
def naya_rule(list_name: str = Form(...), artist: str = Form(...)):
    from .. import digest

    try:
        added = digest.add_to_rules(list_name, artist)
    except ValueError as e:
        raise HTTPException(400, str(e))
    msg = f"{artist} added to {list_name}" if added else f"{artist} was already in rules.yaml"
    return RedirectResponse(f"/naya?msg={msg}", status_code=303)


# ----------------------------------------------------------------------------- Apple Music
def _lan_ip() -> str:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("8.8.8.8", 80))
            return sk.getsockname()[0]
    except Exception:
        return "127.0.0.1"


@app.get("/radio")
def radio_page(request: Request):
    from .. import radio

    groups: dict[str, list] = {}
    for st in radio.stations():
        groups.setdefault(st.group, []).append(st)
    return render(request, "radio.html", groups=list(groups.items()))


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
            set_manual_status(song, status)
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
