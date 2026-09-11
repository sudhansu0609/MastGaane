"""Launch the user's real Chrome with remote debugging and attach Playwright over CDP.

Using a real Chrome + persistent profile (not Playwright's bundled Chromium) keeps the
browser fingerprint identical to normal browsing, which is the main thing that keeps
Instagram from flagging the session.
"""
from __future__ import annotations

import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..config import settings


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _cdp_browser(port: int) -> str | None:
    """Return the Browser string from /json/version, or None if nothing CDP-like answers."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return r.json().get("Browser") if r.status_code == 200 else None
    except Exception:
        return None


def _free_port(start: int) -> int:
    for p in range(start, start + 50):
        if not _port_open(p):
            return p
    raise RuntimeError("no free port for Chrome remote debugging")


def launch_chrome(headless: bool | None = None) -> tuple[subprocess.Popen | None, int]:
    """Start Chrome with --remote-debugging-port unless *our* Chrome is already listening.

    Returns (process or None if reused, port). Other tools (e.g. Adobe UXP) also expose a CDP
    endpoint on 9222, so we verify the responder is Chrome and otherwise pick another port.
    """
    headless = settings.headless if headless is None else headless
    port = settings.chrome_debug_port
    if _port_open(port):
        browser = _cdp_browser(port) or ""
        if browser.startswith(("Chrome", "HeadlessChrome")):
            return None, port
        port = _free_port(port + 1)
    profile = Path(settings.chrome_profile_dir).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        settings.chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1100,900",
        "--mute-audio",
    ]
    if headless:
        args.append("--headless=new")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if (_cdp_browser(port) or "").startswith(("Chrome", "HeadlessChrome")):
            return proc, port
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"Chrome did not open debug port {port}. Check CHROME_PATH={settings.chrome_path}")


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


@asynccontextmanager
async def chrome_page(headless: bool | None = None):
    """Yield (browser, context, page) attached to the real Chrome; close Chrome we started."""
    proc, port = launch_chrome(headless)
    try:
        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context: BrowserContext = browser.contexts[0] if browser.contexts else await browser.new_context()
            page: Page = context.pages[0] if context.pages else await context.new_page()
            try:
                yield browser, context, page
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
    finally:
        _stop(proc)
