"""Resolve pending reels → songs, classify, auto-approve."""
from __future__ import annotations

from rich.console import Console
from sqlmodel import select

from ..config import settings
from ..db import Reel, Run, Song, get_song_by_key, session, utcnow
from .attribution import from_attribution
from .audio import get_audio_clip
from .classify import classify
from .common import SongCandidate, norm_key
from .llm import llm_available
from .fingerprint import fingerprint
from .llm_guess import guess_song, transcribe

console = Console()


def _candidate_for(reel: Reel, deep: bool) -> tuple[SongCandidate | None, str]:
    cand = from_attribution(reel)
    if cand:
        return cand, ""
    if not deep:
        return None, "no attribution (deep resolve disabled)"
    clip = get_audio_clip(reel)
    if clip is None:
        return None, "audio unavailable"
    cand = fingerprint(clip)
    if cand:
        return cand, ""
    if not llm_available():
        return None, "fingerprint miss; no LLM available"
    lyrics = transcribe(clip)
    cand = guess_song(reel, lyrics)
    if cand:
        cand.note = f"{cand.note} | lyrics: {lyrics[:200]}"
        return cand, lyrics
    return None, "unidentified"


def _attach_song(s, reel: Reel, cand: SongCandidate, lyrics: str) -> Song:
    key = norm_key(cand.title, cand.artist)
    song = get_song_by_key(s, key)
    if song is None:
        song = Song(title=cand.title, artist=cand.artist, norm_key=key, source_tier=cand.tier, confidence=cand.confidence, lyrics_snippet=lyrics[:1500])
        s.add(song)
        s.commit()
        s.refresh(song)
        v = classify(song.title, song.artist, reel.caption, lyrics)
        song.keep, song.genre, song.language, song.classify_reason = v.keep, v.genre, v.language, v.reason
        if v.keep and song.confidence >= settings.auto_approve_min_confidence:
            song.status = "approved"
        elif not v.keep:
            song.status = "rejected"
    else:
        song.times_seen += 1
        song.last_seen = utcnow()
        if cand.confidence > song.confidence:
            song.confidence, song.source_tier = cand.confidence, cand.tier
            if song.keep and song.status == "pending" and song.confidence >= settings.auto_approve_min_confidence:
                song.status = "approved"
    reel.song_id = song.id
    s.commit()
    return song


def resolve_pending(limit: int = 500, deep: bool = True, only_watched: bool = False) -> Run:
    with session() as s:
        run = Run(kind="resolve")
        s.add(run)
        s.commit()
        s.refresh(run)
        q = select(Reel).where(Reel.resolve_status == "pending")
        if only_watched:
            q = q.where(Reel.watched == True)  # noqa: E712
        reels = s.exec(q.order_by(Reel.seen_at.desc()).limit(limit)).all()
        console.print(f"Resolving {len(reels)} reels (deep={deep})")
        resolved = kept = 0
        for reel in reels:
            try:
                cand, note = _candidate_for(reel, deep)
                if cand is None:
                    reel.resolve_status = "needs_review" if "unidentified" in note or "miss" in note else "skipped"
                    reel.resolve_note = note
                    s.commit()
                    console.log(f"[dim]{reel.shortcode}[/] → {reel.resolve_status}: {note}")
                    continue
                lyrics = note if cand.tier == 3 else ""
                song = _attach_song(s, reel, cand, lyrics)
                reel.resolve_status = "resolved"
                reel.resolve_note = cand.note
                s.commit()
                resolved += 1
                kept += bool(song.keep)
                mark = "[green]✓[/]" if song.keep else "[red]✗[/]"
                console.log(f"{mark} {song.title} — {song.artist}  (tier {cand.tier}, {song.confidence:.2f}, {song.language}/{song.genre}) {song.classify_reason}")
            except Exception as e:  # keep going; one bad reel must not kill the night
                reel.resolve_status = "failed"
                reel.resolve_note = f"{type(e).__name__}: {e}"[:500]
                s.commit()
                console.log(f"[red]{reel.shortcode} failed: {e}[/]")
        run.finished_at = utcnow()
        run.reels_seen = len(reels)
        run.reels_new = resolved
        run.with_music = kept
        s.commit()
        s.refresh(run)
        console.print(f"[green]Resolve done:[/] {resolved}/{len(reels)} identified, {kept} kept for playlist")
        return run
