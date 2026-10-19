"""MastGaane in the Windows tray: what is playing, pause / mute / next / like / stop, open the app, or quit it.

`mastgaane serve` puts the icon there, also when Dexter starts the server with no window. It drives
the same PC player as Dexter and /api/player/* (music playing in a browser tab on /fm is that tab's
own). Left click = play / pause; right click = the menu; hovering shows what is on.
"""
from __future__ import annotations

import threading
import webbrowser
from typing import Callable

from rich.console import Console

console = Console()
REFRESH_S = 2.0
TIP_MAX = 120  # Windows cuts a tray tooltip at 127 characters


def icon_image(size: int = 64):
    """An eighth note on an amber disc (the web UI's accent colour)."""
    from PIL import Image, ImageDraw

    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((1, 1, size - 2, size - 2), fill=(242, 181, 68), outline=(15, 15, 19), width=max(1, round(2 * s)))
    ink = (15, 15, 19)
    d.ellipse((17 * s, 37 * s, 33 * s, 50 * s), fill=ink)  # note head
    d.rectangle((29 * s, 13 * s, 33 * s, 44 * s), fill=ink)  # stem
    d.polygon([(33 * s, 13 * s), (46 * s, 20 * s), (46 * s, 27 * s), (33 * s, 21 * s)], fill=ink)  # flag
    return img


def _wake_words() -> list[str]:
    from .voice_control import wake_words

    return wake_words()


def status_line(now: dict) -> str:
    """One line for the tooltip and the top of the menu."""
    if now["state"] == "stopped":
        return now.get("message") or "MastGaane — not playing"
    if not now["title"]:
        return "MastGaane — starting…"
    what = now["title"] + (f" — {now['artist']}" if now.get("artist") else "")
    if now["kind"] == "episode" and now.get("programme") and not now["title"].lower().startswith(now["programme"].lower()):
        what = f"{now['programme']}: {now['title']}"
    intro = " (intro)" if now["phase"] == "link" else ""
    mark = "⏸ " if now["state"] == "paused" else ("🔇 " if now.get("muted") else "▶ ")
    return mark + what + intro


class Tray:
    def __init__(self, player: Callable, base_url: Callable[[], str], on_quit: Callable[[], None], voice=None):
        self._player = player  # () -> Player (made on first use, like the web routes do)
        self._base = base_url
        self._on_quit = on_quit
        self.voice = voice  # voice_control.VoiceControl, set by `serve` when voice commands are on
        self._icon = None
        self._done = threading.Event()
        self._shown = ""

    def _now(self) -> dict:
        try:
            return self._player().now()
        except Exception as e:  # the tray must never take the server down
            return {"state": "stopped", "phase": "idle", "title": "", "message": f"MastGaane — {e}"}

    # ------------------------------------------------------------------ menu actions
    def _toggle(self, icon=None, item=None):
        p, now = self._player(), self._now()
        if now["state"] == "playing":
            p.pause()
        elif now["state"] == "paused":
            p.resume()
        else:
            p.play()
        self.refresh(force=True)

    def _do(self, name: str):
        def run(icon=None, item=None):
            getattr(self._player(), name)()
            self.refresh(force=True)
        return run

    def _toggle_mute(self, icon=None, item=None):
        self._player().mute(None)
        self.refresh(force=True)

    def _muted(self, item) -> bool:
        return bool(self._now().get("muted"))

    def _open(self, icon=None, item=None):
        webbrowser.open(self._base().rstrip("/") + "/fm")

    def _speak(self, icon=None, item=None):
        if self.voice:
            self.voice.arm()

    def _toggle_voice(self, icon=None, item=None):
        if self.voice:
            self.voice.stop() if self.voice.running else self.voice.start()
            self.refresh(force=True)

    def notify(self, message: str) -> None:
        """A Windows notification from the tray icon (what a voice command did)."""
        icon = self._icon
        if icon is not None:
            try:
                icon.notify(message[:200], "MastGaane")
            except Exception:
                pass

    def _quit(self, icon=None, item=None):
        try:
            self._player().stop()
        except Exception:
            pass
        self.stop()
        self._on_quit()

    def _toggle_text(self, item) -> str:
        return {"playing": "Pause", "paused": "Resume"}.get(self._now()["state"], "Play MastGaane FM")

    def _running(self, item) -> bool:
        return self._now()["state"] != "stopped"

    def _on_song(self, item) -> bool:
        now = self._now()
        return now["state"] != "stopped" and now["phase"] == "main"

    # ------------------------------------------------------------------ life
    def start(self) -> bool:
        """Show the icon (its own thread). False when there is no tray to show it in."""
        try:
            import pystray
        except Exception as e:
            console.log(f"[yellow]tray: not available ({e})[/]")
            return False
        M = pystray.MenuItem
        menu = pystray.Menu(
            M(lambda item: status_line(self._now())[:TIP_MAX], None, enabled=False),
            pystray.Menu.SEPARATOR,
            M(self._toggle_text, self._toggle, default=True),
            M("Mute", self._toggle_mute, checked=self._muted, enabled=self._running),
            M("Next", self._do("skip"), enabled=self._running),
            M("Like this", self._do("like"), enabled=self._on_song),
            M("Never play this again", self._do("dislike"), enabled=self._on_song),
            M("Stop music", self._do("stop"), enabled=self._running),
            pystray.Menu.SEPARATOR,
            M("Speak a command", self._speak, visible=lambda item: bool(self.voice and self.voice.running)),
            # Dexter hears "Bharti, …" and hands it over either way; this is MastGaane's own, weaker mic
            M(lambda item: "Own mic too (" + ", ".join(w.capitalize() for w in _wake_words()) + ")",
              self._toggle_voice, checked=lambda item: bool(self.voice and self.voice.running),
              visible=lambda item: self.voice is not None),
            pystray.Menu.SEPARATOR,
            M("Open MastGaane", self._open),
            M("Quit MastGaane", self._quit),
        )
        self._icon = pystray.Icon("mastgaane", icon_image(), "MastGaane", menu)
        threading.Thread(target=self._run, daemon=True, name="mastgaane-tray").start()
        threading.Thread(target=self._watch, daemon=True, name="mastgaane-tray-watch").start()
        return True

    def _run(self):
        try:
            self._icon.run()
        except Exception as e:
            console.log(f"[yellow]tray: {e}[/]")

    def _watch(self):
        while not self._done.wait(REFRESH_S):
            self.refresh()

    def refresh(self, force: bool = False):
        """New tooltip and menu when what plays has changed (the menu text is fixed when it is built)."""
        icon = self._icon
        if icon is None:
            return
        line = status_line(self._now())[:TIP_MAX]
        if force or line != self._shown:
            self._shown = line
            try:
                icon.title = line
                icon.update_menu()
            except Exception:
                pass

    def stop(self):
        self._done.set()
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()  # no ghost icon left behind in the tray
            except Exception:
                pass
