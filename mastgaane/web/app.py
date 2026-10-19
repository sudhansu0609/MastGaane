from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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


#: How `serve` stops the server (None when the app runs some other way, e.g. in tests).
_shutdown = None
#: The voice-command listener `serve` started (voice_control.VoiceControl), if any.
_voice = None


def set_shutdown(fn) -> None:
    global _shutdown
    _shutdown = fn


def set_voice(vc) -> None:
    global _voice
    _voice = vc


@app.get("/api/voice")
def voice_status():
    """Voice commands without Dexter: listening or not, on which mic, the wake words, what was heard last."""
    from ..voice_control import wake_words

    vc = _voice
    return {"running": bool(vc and vc.running), "ears": "own mic and Dexter" if vc and vc.running else "Dexter",
            "listening_for_command": bool(vc and (vc.listening or vc.awaiting)),
            "mic": vc.mic if vc else None, "wake_words": wake_words(),
            "last": vc.last if vc else "", "history": list(vc.history)[::-1] if vc else []}


class HeardRequest(BaseModel):
    text: str


@app.post("/api/voice/heard")
def voice_heard(req: HeardRequest, request: Request):
    """A sentence Dexter heard that starts with one of our names ("Bharti, next song"), or the one after
    the name alone ("listening": true): Dexter does nothing else with it — he is our ear. The same command
    heard by our own mic too (VOICE_CONTROL) is done once. Only from this PC."""
    import ipaddress

    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        local = False
    if not local:
        raise HTTPException(403, "only from this PC")
    vc = _voice
    if vc is None:
        raise HTTPException(503, "voice commands are off")
    did = vc.handle(req.text[:300])
    # the name alone: the chime, the music down — Dexter sends what is said next (no name needed)
    return {"did": did, "listening": vc.awaiting}


@app.post("/api/shutdown")
def shutdown(request: Request):
    """Stop the music and the server, cleanly: ffplay ends, the port leaves the ledger and the tray icon
    goes (Dexter's "close MastGaane"). Only from this PC, even when serving the LAN."""
    import ipaddress

    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        local = False
    if not local:
        raise HTTPException(403, "only from this PC")
    if _shutdown is None:
        raise HTTPException(503, "this server was not started by `mastgaane serve`")
    if _player is not None:
        _player.stop()
    _shutdown()
    return {"ok": True, "summary": "MastGaane is closing."}


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
    mood: str = ""


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


# ----------------------------------------------------------------------------- PC player (Dexter, Alexa)
_player = None


def player():
    global _player
    if _player is None:
        from ..player import Player

        import atexit

        _player = Player(lambda: f"http://127.0.0.1:{bound_port()}")
        atexit.register(_player.stop)  # no orphan ffplay playing on after the server is gone
    return _player


def _said(now: dict) -> dict:
    """The player state plus one sentence an assistant can speak (naming the resolved wish)."""
    wish = now.get("wish") or now["focus"]
    if now["message"] and now["state"] == "stopped":
        line = now["message"]
    elif now["state"] == "stopped":
        line = "MastGaane is not playing."
    elif now["phase"] == "link" or not now["title"]:
        line = "MastGaane is starting" + (f" — {wish}." if wish else ".")
    else:
        if now["kind"] == "episode":  # episode titles usually start with the programme's name already
            named = now["title"].lower().startswith(now["programme"].lower())
            what = now["title"] if named else f"{now['programme']}: {now['title']}"
        else:
            what = now["title"] + (f" by {now['artist']}" if now["artist"] else "")
        line = ("Paused: " if now["state"] == "paused" else "Playing ") + what + (f" ({wish})" if wish else "") + "."
    if now.get("muted") and now["state"] != "stopped":
        line = "Muted. " + line
    return {**now, "summary": line}


class PlayRequest(BaseModel):
    focus: str = ""
    talk: bool = True
    links: str = ""  # who writes the links: resident | always | never ("" = JOCKEY_LLM_LINKS)


@app.post("/api/player/play")
def player_play(req: PlayRequest | None = None):
    from ..player import ffplay_path

    if not ffplay_path():
        raise HTTPException(503, "ffplay (FFmpeg) is not installed on this PC")
    req = req or PlayRequest()
    links = req.links.strip().lower()
    if links not in ("", "resident", "always", "never"):
        raise HTTPException(400, "links must be resident, always or never")
    return _said(player().play(req.focus[:120], talk=req.talk, links=links))


@app.get("/api/player/vocabulary")
def player_vocabulary(artists: int = 100):
    """The words a voice assistant should expect in a music request (Dexter's Lexicon reads this):
    programme names with their Hindi spelling and aliases, the most-kept artists, mood and time words."""
    from collections import Counter

    from .. import archive, moods

    progs = [{"slug": slug, "name": p.name, "hindi": p.hindi, "aliases": list(p.aliases)}
             for slug, p in archive.PROGRAMMES.items()]
    count: Counter = Counter()
    with session() as s:
        for song in s.exec(select(Song).where(Song.status == "approved")).all():
            for a in (song.artist or "").split(","):
                if a.strip():
                    count[a.strip()] += 1
    return {
        "programmes": progs,
        "artists": [a for a, _ in count.most_common(max(0, min(artists, 500)))],
        "moods": [w for words in moods.MOODS.values() for w in words],
        "times": [w for words in moods.TIMES.values() for w in words],
    }


class SongPlayRequest(BaseModel):
    song_id: int
    now: bool = True  # False = next, and only while the player is running


@app.post("/api/player/song")
def player_song(req: SongPlayRequest):
    from .. import jockey
    from ..player import ffplay_path

    if not ffplay_path():
        raise HTTPException(503, "ffplay (FFmpeg) is not installed on this PC")
    with session() as s:
        song = s.get(Song, req.song_id)
    if song is None or not song.yt_video_id:
        raise HTTPException(404, "no such song, or it has no YouTube match to play")
    p = player()
    item = jockey.announce(jockey.song_item(song, caught=True), n_picked=1, talk=p.talk, use_llm=False)
    return _said(p.play_item(item, now=req.now))


class DuckRequest(BaseModel):
    level: Optional[float] = 0.2  # null = back to full
    seconds: Optional[float] = None  # come back up by itself after this long


@app.post("/api/player/duck")
def player_duck(req: DuckRequest | None = None):
    """Dip the music (Dexter is listening or talking) or bring it back; it never stays down past `seconds`."""
    req = req or DuckRequest()
    level = None if req.level is None else max(0.0, min(1.0, req.level))
    seconds = min(req.seconds, 120.0) if req.seconds else (30.0 if level is not None else None)
    player().duck(level, seconds=seconds)
    return _said(player().now())


@app.post("/api/player/{action}")
def player_action(action: str):
    p = player()
    fn = {"pause": p.pause, "resume": p.resume, "next": p.skip, "skip": p.skip, "like": p.like,
          "dislike": p.dislike, "stop": p.stop, "mute": p.mute, "unmute": p.unmute}.get(action)
    if fn is None:
        raise HTTPException(404, f"unknown player action {action!r}")
    return _said(fn())


@app.get("/api/player/now")
def player_now():
    return _said(player().now())


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


# ----------------------------------------------------------------------------- Listen (Shazam inside MastGaane)
PLAY_CHOICES = ("next", "now", "none")
MAX_UPLOAD_BYTES = 300 * 1024 * 1024


def _after_catch(out: dict, play: str) -> dict:
    """Put a caught song on the PC speakers: now (starts the player) or next (only if it is playing)."""
    if play not in PLAY_CHOICES:
        raise HTTPException(400, "play must be next, now or none")
    if not out.get("found") or not out.get("playable") or play == "none":
        return out
    from .. import jockey
    from ..player import ffplay_path

    p = player()
    if not ffplay_path() or (play == "next" and p.now()["state"] == "stopped"):
        return out
    with session() as s:
        song = s.get(Song, out["song_id"])
    item = jockey.announce(jockey.song_item(song, caught=True), n_picked=1, talk=p.talk, use_llm=False)
    p.play_item(item, now=play == "now")
    out["player"] = "now" if play == "now" else "next"
    out["summary"] = out["summary"].replace("MastGaane FM plays it next.", "playing it now." if play == "now" else "it plays next on the PC.")
    return out


def _listen_error(e: Exception) -> HTTPException:
    return HTTPException(409 if "already listening" in str(e) else 503, str(e))


@app.get("/listen")
def listen_page(request: Request):
    from .. import listen

    return render(request, "listen.html", catches=listen.recent(), lan_url=f"http://{_lan_ip()}:{bound_port()}",
                  seconds=settings.listen_seconds)


class ListenRequest(BaseModel):
    source: str = "mic"  # mic (this PC's microphones) | pc (what this PC is playing)
    seconds: int = 0  # 0 = LISTEN_SECONDS
    play: str = "next"  # next | now | none — on the PC speakers


@app.post("/api/listen")
def api_listen(req: ListenRequest | None = None):
    """Record on this PC, name the song, keep it. Dexter: "what's this song?"."""
    from .. import listen

    req = req or ListenRequest()
    if req.source not in ("mic", "pc"):
        raise HTTPException(400, "source must be mic or pc")
    if req.play not in PLAY_CHOICES:
        raise HTTPException(400, "play must be next, now or none")
    try:
        out = listen.catch(req.source, req.seconds or None)
    except listen.ListenError as e:
        raise _listen_error(e)
    return _after_catch(out, req.play)


@app.post("/api/listen/file")
def api_listen_file(file: UploadFile = File(...), play: str = Form("next")):
    """A recording from the phone (audio or video — only the sound is used)."""
    import shutil
    import tempfile

    from .. import listen

    if play not in PLAY_CHOICES:
        raise HTTPException(400, "play must be next, now or none")
    suffix = re.sub(r"[^.\w]", "", Path(file.filename or "").suffix)[:10] or ".bin"
    with tempfile.TemporaryDirectory(prefix="mastgaane-upload-") as tmp:
        dst = Path(tmp) / f"take{suffix}"
        with open(dst, "wb") as f:
            shutil.copyfileobj(file.file, f, length=1 << 20)
        if dst.stat().st_size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "recording too large — 10–20 seconds is plenty")
        try:
            out = listen.catch("phone", upload=dst)
        except listen.ListenError as e:
            raise _listen_error(e)
    return _after_catch(out, play)


class SaveRequest(BaseModel):
    title: str
    artist: str = ""
    apple_url: str = ""  # the Shazam action's "Apple Music URL"
    apple_id: str = ""
    play: str = "next"


@app.post("/api/listen/save")
def api_listen_save(req: SaveRequest):
    """The iPhone's own Shazam (a Shortcut) already named it — keep it."""
    from .. import listen

    if req.play not in PLAY_CHOICES:
        raise HTTPException(400, "play must be next, now or none")
    apple_id = req.apple_id.strip() if req.apple_id.strip().isdigit() else listen.apple_id_from_url(req.apple_url)
    heard = listen.Heard(title=req.title[:200], artist=req.artist[:200], apple_id=apple_id, via="shortcut")
    try:
        out = listen.save(req.title[:200], req.artist[:200], "shortcut", heard)
    except listen.ListenError as e:
        raise HTTPException(400, str(e))
    return _after_catch(out, req.play)


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
    import threading

    from ..playlists import apple

    adapter = apple.AppleMusicAdapter()
    if resolve and not apple.resolving():  # iTunes allows ~20 searches a minute: never inside the request
        threading.Thread(target=apple.resolve_missing, daemon=True).start()
        return RedirectResponse("/apple?msg=Finding+Apple+Music+links+in+the+background+(about+3+s+per+song)+-+reload+later", status_code=303)
    with session() as s:
        rows = s.exec(select(Song).where(Song.status == "approved").order_by(Song.first_seen.desc())).all()
    # The port we are actually on, not the one we usually take.
    port = request.url.port or bound_port()
    return render(request, "apple.html", songs=rows, lan_url=f"http://{_lan_ip()}:{port}", musickit=adapter.musickit_ready(), dev_token=bool(settings.apple_dev_token), resolving=apple.resolving())


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
