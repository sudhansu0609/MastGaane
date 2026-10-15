"""Live radio: Vividh Bharati / AIR, Bhojpuri and old-Hindi stations.

A curated list (checked 2026-10-15) is always available. More stations are pulled from the
community directory at radio-browser.info by tag and cached for a week — that API is slow and
often times out, so the cache and the curated list are what the page actually leans on.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass

import httpx

from .config import settings

CACHE = settings.data_dir / "radio_stations.json"
CACHE_TTL = 7 * 24 * 3600
MIRRORS = ("de2", "de1", "fi1", "nl1", "at1")
# directory search -> group shown on the page
DISCOVER = {
    "tag=bhojpuri": "Bhojpuri & folk",
    "tag=ghazal": "Ghazal",
    "tag=oldies&countrycode=IN": "Purane gaane",
    "tag=retro&countrycode=IN": "Purane gaane",
}
# directory entries are user-submitted; drop the party/"hot" and non-Hindi-belt ones
SKIP = re.compile(r"\b(hot|dj|remix|party|tamil|telugu|malayalam|kannada|vanavil|punjabi)\b", re.I)


@dataclass
class Station:
    name: str
    url: str
    group: str
    note: str = ""

    @property
    def hls(self) -> bool:
        return ".m3u8" in self.url


CURATED = [
    Station("Vividh Bharati", "https://air.pc.cdn.bitgravity.com/air/live/pbaudio001/playlist.m3u8", "Vividh Bharati & AIR", "national service — live, with ads"),
    Station("AIR FM Gold", "https://airhlspush.pc.cdn.bitgravity.com/httppush/hlspbaudio005/hlspbaudio005_Auto.m3u8", "Vividh Bharati & AIR", "old film songs, Delhi"),
    Station("AIR Raagam", "https://airhlspush.pc.cdn.bitgravity.com/httppush/hlspbaudioragam/hlspbaudioragam_Auto.m3u8", "Vividh Bharati & AIR", "Indian classical, 24 hours"),
    Station("Radio Kesari", "https://stream.zeno.fm/7yhq985hnxhvv", "Bhojpuri & folk"),
    Station("Radio Badal", "https://stream.zeno.fm/nhrtcxg09u8uv", "Bhojpuri & folk"),
    Station("Radio Rang (Deoria)", "https://stream.zeno.fm/44r9x09cqchvv", "Bhojpuri & folk"),
    Station("Goldy Mukesh", "https://stream.zeno.fm/t39watus1p8uv", "Purane gaane", "Mukesh"),
    Station("Hindi Retro", "https://stream.zeno.fm/v2zfmxef798uv", "Purane gaane"),
    Station("Hindi Gold Radio", "https://azuracast.vibesounds.in:8010/radio.mp3", "Purane gaane"),
]


def _query(path: str) -> list[dict]:
    for host in MIRRORS:
        try:
            r = httpx.get(f"https://{host}.api.radio-browser.info/json/{path}", timeout=20, headers={"User-Agent": "MastGaane/0.1"})
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return []


def discover() -> list[Station]:
    """Stations from the directory for each tag, deduped by stream URL. Empty if the directory is down."""
    found: list[Station] = []
    for q, group in DISCOVER.items():
        for s in _query(f"stations/search?{q}&hidebroken=true&order=votes&reverse=true&limit=15"):
            url = s.get("url_resolved") or s.get("url")
            if url and url.startswith("http") and not SKIP.search(f"{s['name']} {s.get('tags', '')}"):
                found.append(Station(s["name"].strip()[:60], url, group, s.get("tags", "")[:60]))
    return found


def _cached() -> list[Station]:
    try:
        return [Station(**d) for d in json.loads(CACHE.read_text(encoding="utf-8"))]
    except Exception:
        return []


def refresh() -> int:
    """Re-fetch directory stations into the cache. Keeps the old cache if the directory is down."""
    found = discover()
    if found:
        CACHE.write_text(json.dumps([asdict(s) for s in found], ensure_ascii=False, indent=1), encoding="utf-8")
    return len(found)


_refreshing = threading.Lock()


def _refresh_once() -> None:
    if _refreshing.acquire(blocking=False):
        try:
            refresh()
        finally:
            _refreshing.release()


def stations() -> list[Station]:
    """Curated stations first, then cached directory finds. Never blocks on the network:
    a stale or missing cache is refreshed in a background thread for the next page load."""
    if not CACHE.exists() or time.time() - CACHE.stat().st_mtime > CACHE_TTL:
        threading.Thread(target=_refresh_once, daemon=True).start()
    seen, out = set(), []
    for s in CURATED + _cached():
        if s.url not in seen:
            seen.add(s.url)
            out.append(s)
    return out
