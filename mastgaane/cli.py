from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import settings

app = typer.Typer(help="MastGaane — late-night Instagram Reels → songs → playlists", no_args_is_help=True)
setup_app = typer.Typer(help="One-time account setups")
app.add_typer(setup_app, name="setup")
console = Console()


@app.command()
def harvest(
    max_reels: Optional[int] = typer.Option(None, help="Stop after this many watched reels"),
    max_minutes: Optional[int] = typer.Option(None, help="Stop after this many minutes"),
    headless: Optional[bool] = typer.Option(None, "--headless/--headed", help="Override HEADLESS from .env"),
    capture: bool = typer.Option(False, help="Save raw GraphQL payloads to data/captures for parser debugging"),
):
    """Scroll the Reels feed in your Chrome and record every reel + its audio attribution."""
    from .harvester.reels_feed import harvest as _harvest

    asyncio.run(_harvest(max_reels=max_reels, max_minutes=max_minutes, headless=headless, capture=capture))


@app.command()
def resolve(
    limit: int = typer.Option(500),
    deep: bool = typer.Option(True, help="Use fingerprinting + Whisper/Claude for reels without attribution"),
    only_watched: bool = typer.Option(False, help="Only reels actually scrolled to (not just queued)"),
):
    """Identify songs for harvested reels and classify them."""
    from .resolver.pipeline import resolve_pending

    resolve_pending(limit=limit, deep=deep, only_watched=only_watched)


@app.command()
def sync(target: list[str] = typer.Option(None, "--target", "-t", help="ytmusic | spotify | apple (default: all enabled)")):
    """Push approved songs to the playlist targets."""
    from .playlists.sync import sync as _sync

    _sync(target or None)


@app.command()
def report():
    """Write reports/YYYY-MM-DD.md for the last ~20 hours."""
    from .report import write_report

    console.print(f"Report → {write_report()}")


@app.command("run-all")
def run_all(max_reels: Optional[int] = None, max_minutes: Optional[int] = None, deep: bool = True):
    """harvest → resolve → discover → new episodes → weekly digest → sync → report (the nightly task)."""
    from .harvester.reels_feed import harvest as _harvest
    from .playlists.sync import sync as _sync
    from .report import write_report
    from .resolver.pipeline import resolve_pending

    try:
        asyncio.run(_harvest(max_reels=max_reels, max_minutes=max_minutes))
    except Exception as e:
        console.print(f"[red]harvest failed: {e}[/] — continuing with what we have")
    resolve_pending(deep=deep)
    try:
        from .discover import discover as _discover, match_missing

        match_missing()
        _discover()
        from .discover import fill_years, top_up_times

        top_up_times()  # bhajans for the morning, ghazals for the night… where a part of the day runs short

        fill_years()
    except Exception as e:
        console.print(f"[red]discover failed: {e}[/]")
    try:
        from .archive import fetch_new
        from .digest import build, due

        fetch_new()
        if due():
            build()
    except Exception as e:
        console.print(f"[red]episodes/digest failed: {e}[/]")
    _sync()
    console.print(f"Report → {write_report()}")


@app.command()
def serve(
    port: Optional[int] = typer.Option(None, help="Preferred port (default: MASTGAANE_PORT, else 8787). Busy? we step forward."),
    host: str = "127.0.0.1",
    lan: bool = typer.Option(False, help="Listen on all interfaces so your phone can open it"),
    tray: Optional[bool] = typer.Option(None, help="Icon in the Windows tray to pause / stop the music or quit (default: TRAY)"),
    listen: Optional[bool] = typer.Option(None, help="Voice commands on its own mic too, not only from Dexter: \"Bharti, agla gaana\" (default: VOICE_CONTROL)"),
):
    """Start the local web UI (review songs, play them)."""
    import uvicorn

    from . import ports as port_ledger
    from .web import app as web
    from .web.app import app as web_app
    from .web.app import set_bound_port

    if lan:
        host = "0.0.0.0"

    # The port is a wish: whatever already holds it keeps it, and we take the
    # next free one (GUARDIAN_PLAN.md section 11 rule 2).
    chosen = port_ledger.choose_port(port, host="127.0.0.1")
    if port is not None and chosen != port:
        console.print(f"[yellow]Port {port} is held by another process; using {chosen}.[/]")
    set_bound_port(chosen)

    def announce(bound: int) -> None:
        console.print(f"[green]MastGaane:[/] http://127.0.0.1:{bound}/")
        if settings.autoplay:  # the radio is simply on (AUTOPLAY=true)
            console.print("[green]Autoplay:[/] starting the show.")
            try:
                web.player.play("")
            except Exception as e:
                console.print(f"[yellow]Autoplay failed: {e}[/]")
        if lan:
            from .web.app import _lan_ip

            console.print(f"[green]Phone URL:[/] http://{_lan_ip()}:{bound}/apple")

    port_ledger.announce_when_ready(chosen, host, on_ready=announce)
    # The app object, not an import string: a string would re-import the module
    # in uvicorn and lose the bound port we just set on it.
    server = uvicorn.Server(uvicorn.Config(web_app, host=host, port=chosen, reload=False))

    def shut_down() -> None:  # the tray's Quit and POST /api/shutdown (Dexter's "close MastGaane")
        server.should_exit = True

    web.set_shutdown(shut_down)
    icon = voice = None
    if settings.tray if tray is None else tray:
        from .tray import Tray

        icon = Tray(web.player, lambda: f"http://127.0.0.1:{web.bound_port()}", shut_down)
        icon.start()
    from .voice_control import VoiceControl

    # Dexter hands over what he hears ("Bharti, …", POST /api/voice/heard); this does the commands
    voice = VoiceControl(web.player, notify=icon.notify if icon else None)
    web.set_voice(voice)
    if settings.voice_control if listen is None else listen:
        voice.start()  # its own mic as well
    if icon is not None:
        icon.voice = voice
    try:
        server.run()
    finally:
        if voice is not None:
            voice.stop()
        if icon is not None:
            icon.stop()
        port_ledger.withdraw()


@app.command()
def songs(status: Optional[str] = None, limit: int = 50):
    """List songs in the DB."""
    from sqlmodel import select

    from .db import Song, session

    with session() as s:
        q = select(Song).order_by(Song.last_seen.desc())
        if status:
            q = q.where(Song.status == status)
        rows = s.exec(q.limit(limit)).all()
    t = Table("id", "title", "artist", "genre", "keep", "status", "conf", "seen")
    for r in rows:
        t.add_row(str(r.id), r.title, r.artist, r.genre, str(r.keep), r.status, f"{r.confidence:.2f}", str(r.times_seen))
    console.print(t)


@app.command()
def approve(song_id: list[int], reject: bool = typer.Option(False, help="Reject instead of approve")):
    """Approve (or --reject) songs by id."""
    from .db import Song, session, set_manual_status

    with session() as s:
        for sid in song_id:
            song = s.get(Song, sid)
            if song:
                set_manual_status(song, "rejected" if reject else "approved")
        s.commit()
    console.print("done")


@app.command()
def reclassify(
    status: list[str] = typer.Option(["rejected", "pending"], "--status", "-s", help="which songs to re-check"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show what would change, write nothing"),
):
    """Re-run the classifier (after editing rules.yaml or switching LLM). Your own approve/reject calls are kept."""
    from sqlmodel import select

    from .db import MANUAL, Reel, Song, session
    from .resolver.classify import classify, needs_review

    flipped = 0
    with session() as s:
        rows = s.exec(select(Song).where(Song.status.in_(status))).all()
        for song in rows:
            if song.classify_reason.startswith(MANUAL):
                continue
            reel = s.exec(select(Reel).where(Reel.song_id == song.id)).first()
            v = classify(song.title, song.artist, reel.caption if reel else "", song.lyrics_snippet)
            new = "approved" if v.keep and song.confidence >= settings.auto_approve_min_confidence and not needs_review(v) else ("pending" if v.keep else "rejected")
            if new != song.status:
                flipped += 1
                console.print(f"{song.status:>8} → {new:<8} {song.title} — {song.artist}  ({v.language}/{v.genre}) {v.reason[:80]}")
            if not dry_run:
                song.keep, song.genre, song.language, song.classify_reason, song.status = v.keep, v.genre, v.language, v.reason, new
                s.commit()
    console.print(f"{len(rows)} checked, {flipped} changed{' (dry run)' if dry_run else ''}")


@app.command()
def match():
    """Find YouTube versions (and release years) of approved/pending songs so the web player can play them (no login needed)."""
    from .discover import fill_years, match_missing

    match_missing()
    fill_years()


@app.command()
def listen(
    pc: bool = typer.Option(False, "--pc", help="name what this PC is playing instead of using the microphone"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="name the song in a recording (audio or video)"),
    seconds: int = typer.Option(0, help="how long to listen (default LISTEN_SECONDS)"),
    play: str = typer.Option("next", help="next | now | none — on the PC speakers of a running MastGaane"),
    mics: bool = typer.Option(False, "--mics", help="list the microphones it listens with, and stop"),
):
    """Shazam inside MastGaane: name the song playing nearby, keep it in your collection, play it."""
    import httpx

    from . import listen as L
    from .ports import running_base_url

    if mics:
        names = L.mics()
        console.print("\n".join(f"🎤 {n}" for n in names) or "[red]no microphone found[/] (LISTEN_MIC set to something absent?)")
        return
    if play not in ("next", "now", "none"):
        raise typer.BadParameter("play must be next, now or none")
    if file and not file.exists():
        raise typer.BadParameter(f"{file} does not exist")
    source = "file" if file else ("pc" if pc else "mic")
    secs = seconds or settings.listen_seconds
    if not file:
        console.print(f"[dim]listening for {secs} s…[/]")
    try:
        out = L.catch(source, secs, upload=file, classify=False)
    except L.ListenError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(("[green]" if out["found"] else "[yellow]") + out["summary"] + "[/]")
    if not out["found"]:
        raise typer.Exit(1)
    if play != "none" and out["playable"]:
        base = running_base_url()
        if base:
            try:
                httpx.post(f"{base}/api/player/song", json={"song_id": out["song_id"], "now": play == "now"}, timeout=30)
            except httpx.HTTPError as e:
                console.print(f"[yellow]could not reach the player: {e}[/]")
    if out["created"]:
        L.classify_song(out["song_id"], source)


@app.command()
def discover(
    seeds: int = typer.Option(12, help="how many radios to start"),
    max_new: int = typer.Option(60, help="stop after classifying this many new songs"),
    time: Optional[str] = typer.Option(None, "--time", help="fill a part of the day from rules.yaml time_seeds: morning (bhajans), afternoon, evening, night"),
):
    """Find new songs like the ones you approved (YouTube Music radio) and queue them on the Discover page."""
    from .discover import discover as _discover, match_missing, top_up_times

    if time:
        top_up_times(max_new=max_new if max_new != 60 else 25, only=time.lower())
        return
    match_missing(("approved",))
    _discover(n_seeds=seeds, max_new=max_new)


@app.command()
def library(
    target: Optional[int] = typer.Option(None, help="songs in the collection, yours included (default: rules.yaml library.target)"),
    modern_max: Optional[int] = typer.Option(None, help="at most this many from 2010 on (default: library.modern_max)"),
    stats: bool = typer.Option(False, "--stats", help="only show what the collection holds"),
):
    """Fill the big collection the jockey plays from: rules.yaml library.artists and .playlists, labelled by the
    local LLM, nearly all from before 2010. Resumable: run it again to carry on."""
    from .library import build, summary

    if not stats:
        build(target=target, modern_max=modern_max)
    console.print(summary())


@app.command()
def moods(
    limit: int = typer.Option(0, "--limit", "-n", help="tag at most this many songs (0 = all untagged)"),
    heuristic: bool = typer.Option(False, "--heuristic", help="genre/title/year rules only, no LLM (fast)"),
):
    """Tag songs with a mood (sad, romantic, energetic, ...) so "play something sad" works. Only untagged songs."""
    import time as _t
    from collections import Counter

    from sqlmodel import select

    from . import moods as M
    from .db import Reel, Song, session

    started, by_mood, llm_ok = _t.monotonic(), Counter(), not heuristic
    with session() as s:
        rows = s.exec(select(Song).where((Song.mood == "") | (Song.mood.is_(None)))).all()
        rows = rows[:limit] if limit > 0 else rows
        for song in rows:
            mood = ""
            if llm_ok:
                from .resolver.classify import SYSTEM, Verdict
                from .resolver.llm import LLMUnavailable, complete_json

                reel = s.exec(select(Reel).where(Reel.song_id == song.id)).first()
                try:
                    v = complete_json(SYSTEM, f"Song: {song.title}\nArtist: {song.artist}\nReel caption: {(reel.caption if reel else '')[:600]}\n"
                                              f"Lyrics snippet: {song.lyrics_snippet[:400]}\nAlso label the song's mood.", Verdict)
                    mood = v.mood if v.mood != M.ANY else ""
                except LLMUnavailable as e:
                    console.print(f"[yellow]no LLM ({str(e)[:80]}) — heuristics for the rest[/]")
                    llm_ok = False
            song.mood = mood or M.guess_mood(song)
            by_mood[song.mood] += 1
        s.commit()
    table = Table("mood", "songs")
    for mood, n in by_mood.most_common():
        table.add_row(mood, str(n))
    console.print(table)
    console.print(f"{sum(by_mood.values())} songs tagged in {_t.monotonic() - started:.2f} s")


@app.command("fetch-episodes")
def fetch_episodes(per_programme: int = typer.Option(2, help="new episodes to download per programme")):
    """Download a few new Vividh Bharati programme episodes so MastGaane FM plays them instantly."""
    from .archive import fetch_new

    for e in fetch_new(per_programme):
        console.print(f"+ {e['programme']}: {e['title']}")


@app.command()
def digest(days: int = 7):
    """Build the 'Naya kya hai' weekly digest (also shown at /naya)."""
    from .digest import build

    d = build(days)
    console.print(f"{d['plays']} plays, {d['hours']} h, {d['likes']} likes, {d['skips']} skips; "
                  f"{d['finds']['total']} new finds; {len(d['new_episodes'])} new episodes; "
                  f"{len(d['rule_suggestions'])} rule suggestions")
    for i in d["ideas"]:
        console.print(f"  • {i}")


@app.command()
def radio(refresh: bool = typer.Option(False, "--refresh", help="re-fetch stations from radio-browser.info now")):
    """List the radio stations (curated + directory finds)."""
    from . import radio as R

    if refresh:
        console.print(f"directory: {R.refresh()} stations fetched")
    t = Table("group", "station", "stream")
    for st in R.stations():
        t.add_row(st.group, st.name, st.url)
    console.print(t)


@app.command("host-voice")
def host_voice(
    design: Optional[str] = typer.Option(None, help="Design a new Gemini voice from this description (\"default\" = the built-in one)"),
    play: bool = typer.Option(False, help="Play the samples on the PC speakers"),
):
    """Hear the FM host: one short link per feeling, in the voice MastGaane uses now (Gemini TTS or edge-tts)."""
    import shutil
    import subprocess

    from . import gemini_tts, jockey

    gemini_tts.MAX_WAIT_S = 65  # samples may wait out the per-minute limit instead of falling back
    if design is not None:
        out = gemini_tts.design_voice(gemini_tts.VOICE_DESCRIPTION if design == "default" else design)
        console.print(f"[green]Designed[/] {out['id']} — saved to {gemini_tts._voice_file()}")
        if out["sample"]:
            (jockey.VOICE_DIR / "designed_sample.wav").write_bytes(out["sample"])
    if gemini_tts.enabled():
        engine = f"Gemini TTS ({settings.gemini_tts_model}), voice {gemini_tts.voice()}"
    else:
        engine = f"edge-tts, {settings.jockey_voice}" + ("" if settings.gemini_api_key else " (set GEMINI_API_KEY for the Gemini voice)")
    console.print(f"Host voice: {engine}")
    song = {"singer": "Jagjit Singh", "title": "Hothon Se Chhoo Lo Tum"}
    samples = jockey.VOICE_DIR / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    for feeling in jockey.EMOTIONS:
        text = jockey.SONG_LINKS[feeling][0].format(**song)
        key = jockey.speak(text, feeling)
        if key is None:
            console.print(f"{feeling:9} [yellow]quiet — Gemini has no requests left today (JOCKEY_FALLBACK=edge lets edge-tts speak)[/]")
            continue
        path = samples / f"{feeling}.mp3"
        shutil.copyfile(jockey.VOICE_DIR / f"{key}.mp3", path)
        console.print(f"{feeling:9} {path}")
        if play and shutil.which("ffplay"):
            subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)])


@app.command()
def llm(test: bool = typer.Option(False, help="Send a test classification through the selected backend")):
    """Show which LLM backends are reachable (local Ollama / LM Studio / Claude)."""
    from .resolver import llm as L

    t = Table("backend", "reachable", "detail")
    t.add_row("ollama", str(L.ollama_alive()), f"{L._ollama_base()}  model={settings.llm_model}")
    t.add_row("openai-compatible", str(L.openai_alive()), L._openai_base())
    t.add_row("claude", str(L.claude_alive()), settings.claude_model)
    console.print(t)
    console.print(f"LLM_PROVIDER={settings.llm_provider} → order: {L.providers() or 'NONE (rules.yaml keywords only)'}")
    if test:
        from .resolver.classify import Verdict

        import time as _t

        t0 = _t.time()
        v = L.complete_json(
            "Classify songs for a late-night melodious playlist.",
            "Song: Tum Itna Jo Muskura Rahe Ho / Artist: Jagjit Singh / Reel caption: late night feels",
            Verdict,
        )
        console.print(f"[green]{v}[/]  ({_t.time() - t0:.1f}s)")


@setup_app.command("ytmusic")
def setup_ytmusic():
    """Create browser.json for ytmusicapi (paste request headers from music.youtube.com)."""
    import ytmusicapi

    console.print(
        "Open https://music.youtube.com logged in → DevTools → Network → filter 'browse' → click a POST request\n"
        "→ copy the *Request Headers* block (raw) and paste below, then press Enter twice (Ctrl+Z on Windows)."
    )
    ytmusicapi.setup(filepath=str(settings.ytmusic_auth_file))
    console.print(f"[green]Saved {settings.ytmusic_auth_file}[/]")


@setup_app.command("spotify")
def setup_spotify():
    """Run the Spotify OAuth flow once (opens browser)."""
    from .playlists.spotify import SpotifyAdapter

    a = SpotifyAdapter()
    if not a.available():
        raise typer.Exit("Set SPOTIFY_ENABLED=true, SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env first.")
    me = a._sp().current_user()
    console.print(f"[green]Spotify authorized as {me.get('display_name')} ({me.get('product')})[/]")


@setup_app.command("apple")
def setup_apple():
    """Show how to get the playlist into Apple Music on the iPhone (no developer account needed)."""
    from . import ports as port_ledger
    from .web.app import _lan_ip

    # If a MastGaane is running, name the port it really took; otherwise the one
    # `serve` will ask for next.
    running = port_ledger.running_base_url()
    port = int(running.rsplit(":", 1)[1]) if running else port_ledger.preferred_port()
    lan = f"http://{_lan_ip()}:{port}"
    if settings.apple_dev_token:
        console.print(f"APPLE_DEV_TOKEN is set -> run [bold]mastgaane serve[/], open {lan}/apple/auth once, then [bold]mastgaane sync[/] fills the playlist directly.")
        return
    steps = [
        "",
        "[bold]Apple Music via iPhone Shortcut (no developer account)[/]",
        "1. On the PC:  [bold]uv run mastgaane serve --lan[/]   (allow it through Windows Firewall when asked)",
        f"2. On the iPhone (same Wi-Fi) open  [bold]{lan}/apple[/]  - tap any song -> opens in Apple Music -> + Add to Playlist.",
        "3. One-time Shortcut for bulk import - Shortcuts app -> + -> add these actions:",
        f"     (1) Get Contents of URL        ->  {lan}/apple/import.txt",
        "     (2) Split Text                  ->  New Lines",
        "     (3) Repeat with Each (the split result)",
        "           Search Music             ->  Apple Music catalog, Songs, limit 1, term = Repeat Item",
        f"           Add to Playlist          ->  '{settings.playlist_name}'  (create the playlist in Music first)",
        "   Name it 'MastGaane' and run it whenever you want the Apple playlist refreshed.",
        "",
        "[dim]Have an Apple Developer account? Put a MusicKit developer token in APPLE_DEV_TOKEN and the playlist syncs automatically.[/]",
    ]
    console.print("\n".join(steps))


@setup_app.command("login")
def setup_login():
    """Open Chrome with the MastGaane profile so you can log in to Instagram once."""
    from .harvester.reels_feed import harvest as _harvest

    asyncio.run(_harvest(max_reels=3, max_minutes=3, headless=False))


if __name__ == "__main__":
    app()
