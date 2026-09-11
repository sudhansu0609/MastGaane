"""Nightly markdown report."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from .config import settings
from .db import Reel, Run, Song, session


def write_report(date: datetime | None = None) -> str:
    date = date or datetime.now()
    since = datetime.now(timezone.utc) - timedelta(hours=20)
    with session() as s:
        runs = s.exec(select(Run).where(Run.started_at >= since).order_by(Run.started_at)).all()
        reels = s.exec(select(Reel).where(Reel.seen_at >= since)).all()
        songs = s.exec(select(Song).where(Song.last_seen >= since).order_by(Song.times_seen.desc())).all()
        lines = [f"# MastGaane — {date:%Y-%m-%d}", ""]
        lines.append("## Runs")
        for r in runs:
            lines.append(f"- {r.kind}: seen={r.reels_seen} new={r.reels_new} music={r.with_music} {r.notes}")
        watched = [r for r in reels if r.watched]
        tagged = [r for r in watched if r.ig_song_title and not r.is_original_audio]
        lines += ["", f"## Reels: {len(watched)} watched, {len(reels)} queued, {len(tagged)} with tagged music", ""]
        kept = [x for x in songs if x.keep]
        lines += [f"## Songs kept ({len(kept)})", ""]
        for x in kept:
            links = []
            if x.yt_video_id:
                links.append(f"[YT](https://music.youtube.com/watch?v={x.yt_video_id})")
            if x.spotify_uri:
                links.append(f"[Spotify](https://open.spotify.com/track/{x.spotify_uri.split(':')[-1]})")
            if x.apple_url:
                links.append(f"[Apple]({x.apple_url})")
            lines.append(f"- **{x.title}** — {x.artist} · {x.language}/{x.genre} · ×{x.times_seen} · {x.status} · t{x.source_tier} {x.confidence:.2f} {' '.join(links)}")
        rejected = [x for x in songs if x.keep is False]
        lines += ["", f"## Rejected ({len(rejected)})", ""]
        for x in rejected[:40]:
            lines.append(f"- {x.title} — {x.artist}: {x.classify_reason}")
        review = [r for r in reels if r.resolve_status == "needs_review"]
        lines += ["", f"## Needs review ({len(review)})", ""]
        for r in review[:40]:
            lines.append(f"- [{r.shortcode}]({r.url}) @{r.owner}: {r.resolve_note}")
    path = settings.reports_dir / f"{date:%Y-%m-%d}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
