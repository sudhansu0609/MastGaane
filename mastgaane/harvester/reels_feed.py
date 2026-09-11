"""Scroll instagram.com/reels like a human and record every clip the feed serves.

We never call Instagram endpoints ourselves; we only listen to the responses the page
fetches on its own (page.on("response")) and parse them defensively.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime

from playwright.async_api import Page, Response
from rich.console import Console

from ..config import settings
from ..db import Run, session, utcnow
from .chrome import chrome_page
from .parse import ReelItem, extract_items
from .store import mark_watched, upsert_reel

console = Console()
REELS_URL = "https://www.instagram.com/reels/"
SHORTCODE_RE = re.compile(r"/reels?/([A-Za-z0-9_-]{5,})")

# cheap pre-filter used only to decide how long to dwell (steers the algorithm toward songs)
SKIP_WORDS = ("meme", "comedy", "funny", "prank", "trend", "sound", "audio", "voice", "dialogue", "tiktok")
MUSIC_WORDS = ("ghazal", "sufi", "song", "gaana", "gana", "geet", "melody", "cover", "unplugged", "live", "qawwali", "nazm", "shayari", "sad", "romantic", "love")


def looks_musical(item: ReelItem | None) -> bool:
    if item is None:
        return False
    if item.has_music:
        return True
    title = (item.song_title or "").lower()
    if title.startswith("original"):  # Instagram's generic "Original audio" label carries no signal
        title = ""
    blob = f"{title} {item.artist or ''} {item.caption}".lower()
    return any(w in blob for w in MUSIC_WORDS) and not any(w in blob for w in SKIP_WORDS)


def current_shortcode(url: str) -> str | None:
    m = SHORTCODE_RE.search(url)
    return m.group(1) if m else None


async def _dismiss_dialogs(page: Page) -> None:
    for text in ("Not Now", "Not now", "Cancel"):
        try:
            btn = page.get_by_role("button", name=text)
            if await btn.count():
                await btn.first.click(timeout=1000)
                await asyncio.sleep(0.5)
        except Exception:
            pass


async def _ensure_logged_in(page: Page, headless: bool) -> None:
    await page.goto(REELS_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)
    if "accounts/login" in page.url or await page.locator("input[name='username']").count():
        if headless:
            raise RuntimeError("Not logged in. Run once with HEADLESS=false and log in to Instagram in the Chrome window.")
        console.print("[yellow]Instagram is not logged in. Please log in in the Chrome window (waiting up to 5 min)...[/]")
        for _ in range(300):
            await asyncio.sleep(1)
            if "accounts/login" not in page.url and not await page.locator("input[name='username']").count():
                break
        else:
            raise RuntimeError("Timed out waiting for login.")
        await page.goto(REELS_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)
    await _dismiss_dialogs(page)


async def export_cookies(context) -> None:
    """Write instagram cookies in Netscape format so yt-dlp can fetch reels later."""
    try:
        cookies = await context.cookies(["https://www.instagram.com"])
    except Exception:
        return
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires") or 0)
        if exp < 0:
            exp = 0
        lines.append("\t".join([domain, flag, c.get("path", "/"), secure, str(exp), c.get("name", ""), c.get("value", "")]))
    (settings.data_dir / "cookies.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def harvest(
    max_reels: int | None = None,
    max_minutes: int | None = None,
    headless: bool | None = None,
    capture: bool = False,
) -> Run:
    max_reels = max_reels or settings.max_reels
    max_minutes = max_minutes or settings.max_minutes
    headless = settings.headless if headless is None else headless

    found: dict[str, ReelItem] = {}
    watched: list[str] = []
    capture_dir = settings.data_dir / "captures"
    if capture:
        capture_dir.mkdir(parents=True, exist_ok=True)

    with session() as s:
        run = Run(kind="harvest")
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    new_count = 0
    music_count = 0

    async def on_response(resp: Response) -> None:
        nonlocal new_count, music_count
        url = resp.url
        if "instagram.com" not in url or not ("graphql" in url or "/api/v1/" in url):
            return
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            data = await resp.json()
        except Exception:
            return
        items = extract_items(data)
        if not items:
            return
        if capture:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (capture_dir / f"{stamp}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with session() as s:
            for it in items:
                if it.shortcode in found and found[it.shortcode].song_title:
                    continue
                found[it.shortcode] = it
                _, created = upsert_reel(s, it, run_id, watched=False)
                if created:
                    new_count += 1
                    if it.has_music:
                        music_count += 1
        console.log(f"feed +{len(items)} items (total {len(found)}, new {new_count})")

    async with chrome_page(headless) as (_browser, _ctx, page):
        page.on("response", on_response)
        await _ensure_logged_in(page, headless)
        await export_cookies(_ctx)

        deadline = time.monotonic() + max_minutes * 60
        stalls = 0
        last_code: str | None = None
        await page.mouse.move(550, 450)

        while len(watched) < max_reels and time.monotonic() < deadline:
            code = current_shortcode(page.url)
            if code and code != last_code:
                last_code = code
                stalls = 0
                item = found.get(code)
                with session() as s:
                    if item is not None:
                        upsert_reel(s, item, run_id, watched=True)
                    else:
                        mark_watched(s, code)
                watched.append(code)
                musical = looks_musical(item)
                dwell = random.uniform(15, 25) if musical else random.uniform(4, 12)
                label = f"{item.song_title} — {item.artist}" if item and item.song_title else "(no attribution yet)"
                console.log(f"[{len(watched)}/{max_reels}] {code} {'♪' if musical else ' '} {label}  dwell {dwell:.0f}s")
                await asyncio.sleep(dwell)
                if random.random() < 0.08:  # occasional human pause
                    await asyncio.sleep(random.uniform(10, 30))
            else:
                stalls += 1
                await asyncio.sleep(random.uniform(1.5, 3))
                if stalls == 4:
                    await page.keyboard.press("ArrowDown")
                if stalls >= 8:
                    console.log("[yellow]feed stalled, reloading[/]")
                    await page.goto(REELS_URL, wait_until="domcontentloaded")
                    await asyncio.sleep(4)
                    await _dismiss_dialogs(page)
                    stalls = 0
                    last_code = None
                    continue

            await page.mouse.move(500 + random.randint(-60, 60), 420 + random.randint(-60, 60))
            await page.mouse.wheel(0, random.randint(600, 900))
            await asyncio.sleep(random.uniform(0.8, 1.6))

    with session() as s:
        run = s.get(Run, run_id)
        run.finished_at = utcnow()
        run.reels_seen = len(watched)
        run.reels_new = new_count
        run.with_music = music_count
        run.notes = f"queued={len(found)}"
        s.commit()
        s.refresh(run)
    console.print(f"[green]Harvest done:[/] watched {len(watched)} reels, {len(found)} queued, {new_count} new, {music_count} new with tagged music")
    return run
