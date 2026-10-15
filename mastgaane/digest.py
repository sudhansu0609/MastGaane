"""'Naya kya hai' — the weekly digest: what you listened to, what is new, and what to change.

Suggestions come in two kinds:
  rules  concrete, one-tap changes to rules.yaml (e.g. a singer you keep liking joins folk_artists)
  ideas  a few free-text ideas from the local LLM, based on the week's numbers
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlmodel import select

from . import archive
from .config import settings
from .db import Play, Song, session, utcnow
from .resolver.classify import rules

RULE_LISTS = ("folk_artists", "ghazal_artists", "always_keep_artists")


class Ideas(BaseModel):
    ideas: list[str]


def _known_artists() -> set[str]:
    r = rules()
    return {a.lower() for key in RULE_LISTS for a in r.get(key, [])}


def build(days: int = 7, use_llm: bool = True) -> dict:
    since = utcnow() - timedelta(days=days)
    with session() as s:
        plays = s.exec(select(Play).where(Play.at >= since)).all()
        finds = s.exec(select(Song).where(Song.source_tier == 4, Song.first_seen >= since)).all()
        songs_by_artist: dict[str, Counter] = defaultdict(Counter)
        for song in s.exec(select(Song).where(Song.status == "approved")).all():
            for a in song.artist.split(","):
                if a.strip():
                    songs_by_artist[a.strip()][song.genre] += 1
    outcome = Counter(p.outcome for p in plays)
    artist_score: Counter = Counter()
    prog_count: Counter = Counter()
    for p in plays:
        sc = {"like": 3, "full": 1, "skip": -1, "dislike": -4}.get(p.outcome, 0)
        for a in p.artist.split(","):
            if a.strip():
                artist_score[a.strip()] += sc
        if p.programme:
            prog_count[archive.PROGRAMMES[p.programme].name if p.programme in archive.PROGRAMMES else p.programme] += 1
    known = _known_artists()
    rule_suggestions = []
    for artist, sc in artist_score.most_common(10):
        if sc >= 4 and artist.lower() not in known:
            genre = songs_by_artist.get(artist, Counter()).most_common(1)
            g = genre[0][0] if genre else ""
            target = "folk_artists" if g == "folk" else "ghazal_artists" if g == "ghazal" else "always_keep_artists"
            rule_suggestions.append({"artist": artist, "list": target, "why": f"you liked or finished their songs (score {sc})"})
    try:
        new_eps = [e for e in json.loads(archive.NEW_LOG.read_text(encoding="utf-8")) if e["at"] >= since.isoformat()]
    except Exception:
        new_eps = []
    d = {
        "made": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": days,
        "plays": len(plays),
        "hours": round(sum(p.listened_s for p in plays) / 3600, 1),
        "likes": outcome["like"], "skips": outcome["skip"], "dislikes": outcome["dislike"], "full": outcome["full"],
        "top_artists": [a for a, sc in artist_score.most_common(8) if sc > 0],
        "programmes": prog_count.most_common(),
        "finds": {"total": len(finds), "kept": sum(f.status == "approved" for f in finds),
                  "waiting": [f"{f.title} — {f.artist}" for f in finds if f.status == "pending"][:12]},
        "new_episodes": new_eps[:20],
        "rule_suggestions": rule_suggestions,
        "ideas": [],
    }
    if use_llm and plays:
        from .resolver.llm import LLMUnavailable, complete_json

        stats = {k: d[k] for k in ("plays", "hours", "likes", "skips", "dislikes", "top_artists", "programmes")}
        stats["finds_kept_of"] = [d["finds"]["kept"], d["finds"]["total"]]
        try:
            d["ideas"] = complete_json(
                "You help tune a personal Hindi music + old Vividh Bharati radio app for one listener who loves ghazals "
                "and Bhojpuri folk. From the week's numbers suggest 3 short, concrete ideas (one sentence each, English) "
                "— e.g. a programme to play more or less, a singer or folk form to explore, a time slot to change.",
                json.dumps(stats, ensure_ascii=False), Ideas).ideas[:3]
        except LLMUnavailable:
            pass
    path = settings.reports_dir / f"naya-{datetime.now():%Y-%m-%d}.json"
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def latest() -> dict | None:
    files = sorted(settings.reports_dir.glob("naya-*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def due(days: int = 7) -> bool:
    d = latest()
    return d is None or datetime.strptime(d["made"], "%Y-%m-%d %H:%M") < datetime.now() - timedelta(days=days)


def add_to_rules(list_name: str, artist: str) -> bool:
    """Append an artist to a list in rules.yaml without disturbing its comments. False if already there."""
    if list_name not in RULE_LISTS or not artist.strip() or "\n" in artist:
        raise ValueError("bad rule")
    artist = artist.strip()
    if artist.lower() in _known_artists():
        return False
    text = settings.rules_file.read_text(encoding="utf-8")
    m = re.search(rf"^{list_name}:\s*\n", text, re.M)
    if m:
        text = text[: m.end()] + f"  - {json.dumps(artist, ensure_ascii=False)}\n" + text[m.end():]
    else:
        text = text.rstrip("\n") + f"\n\n{list_name}:\n  - {json.dumps(artist, ensure_ascii=False)}\n"
    settings.rules_file.write_text(text, encoding="utf-8")
    rules.cache_clear()
    return True
