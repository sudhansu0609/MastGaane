"""MastGaane on the PC speakers — the FM jockey without a browser, driven over HTTP (Dexter, Alexa).

One background thread runs the show: pick → the host's Hindi link → song or programme → next.
ffplay (FFmpeg) does the playing with no window; pause suspends the ffplay process. Every listen,
skip, like and dislike lands in the same `play` table as /fm, so the taste it learns is shared.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from rich.console import Console

from . import jockey

console = Console()
MAX_FAILURES = 5  # in a row — then stop instead of spinning through a broken queue


def ffplay_path() -> str | None:
    return shutil.which("ffplay")


class Player:
    def __init__(self, base_url: Callable[[], str]):
        self._base = base_url  # the running server, which serves voice + audio routes
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._cmd: str | None = None  # skip | dislike | switch | stop — read by the show thread
        self._gen = 0  # bumped whenever a prefetched item would no longer fit
        self._upcoming: jockey.Item | None = None
        self._n = 0
        self._played = 0.0
        self._seg_start = 0.0
        self.cur: jockey.Item | None = None
        self.phase = "idle"  # idle | link | main
        self.paused = False
        self.focus = ""
        self.talk = True
        self.message = ""

    # ------------------------------------------------------------------ commands
    def play(self, focus: str = "", talk: bool = True) -> dict:
        """Start, switch to a new wish, or resume. Returns now()."""
        focus = (focus or "").strip()
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if running and not focus:
                if self.paused:
                    self._resume()
                return self.now()
            self.focus, self.talk, self.message = focus, talk, ""
            self._gen += 1
            self._upcoming = None
            if running:
                self._command("switch")
                return self.now()
            self._cmd = None
            self._thread = threading.Thread(target=self._show, daemon=True, name="mastgaane-player")
            self._thread.start()
        return self.now()

    def pause(self) -> dict:
        with self._lock:
            if self._proc and not self.paused:
                import psutil

                try:
                    psutil.Process(self._proc.pid).suspend()
                    self._played += time.monotonic() - self._seg_start
                    self.paused = True
                except Exception as e:
                    self.message = f"could not pause: {e}"
            return self.now()

    def resume(self) -> dict:
        with self._lock:
            self._resume()
            return self.now()

    def skip(self) -> dict:
        return self._command("skip")

    def like(self) -> dict:
        with self._lock:
            if self.cur and self.phase != "idle":
                self._record("like")
                self.message = "liked — more like this"
            return self.now()

    def dislike(self) -> dict:
        with self._lock:
            if self.cur and self.phase != "idle":
                self._record("dislike")
            return self._command("dislike")

    def stop(self) -> dict:
        return self._command("stop")

    def now(self) -> dict:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            it = self.cur if running else None
            return {
                "state": "stopped" if not running else ("paused" if self.paused else "playing"),
                "phase": self.phase if running else "idle",
                "kind": it.kind if it else "",
                "title": it.title if it else "",
                "artist": it.artist if it else "",
                "programme": it.programme_name if it else "",
                "discovery": bool(it and it.discovery),
                "focus": self.focus,
                "listened_s": round(self._listened(), 1),
                "upcoming": self._upcoming.title if self._upcoming and running else "",
                "message": self.message,
            }

    # ------------------------------------------------------------------ internals
    def _command(self, cmd: str) -> dict:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return self.now()
            self._cmd = cmd
            self._kill()
            return self.now()

    def _resume(self) -> None:
        if self._proc and self.paused:
            import psutil

            try:
                psutil.Process(self._proc.pid).resume()
            except Exception:
                pass
            self._seg_start = time.monotonic()
            self.paused = False

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._resume()  # a suspended process cannot exit cleanly
            self._proc.terminate()

    def _listened(self) -> float:
        if self.phase != "main":
            return 0.0
        live = 0.0 if self.paused or not self._proc else time.monotonic() - self._seg_start
        return self._played + live

    def _record(self, outcome: str) -> None:
        it = self.cur
        try:
            jockey.record(it.kind, it.ref, outcome, title=it.title, artist=it.artist, genre=it.genre,
                          programme=it.programme, listened_s=self._listened())
        except Exception as e:
            console.log(f"[yellow]player: could not record {outcome}: {e}[/]")

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else self._base().rstrip("/") + path

    def _play(self, path: str) -> tuple[bool, float]:
        """Play one file to the end (True) or until a command cuts it (False); returns seconds heard."""
        exe = ffplay_path()
        if not exe:
            raise RuntimeError("ffplay (FFmpeg) is not on PATH")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with self._lock:
            if self._cmd:
                return False, 0.0
            self._proc = subprocess.Popen([exe, "-nodisp", "-autoexit", "-loglevel", "error", self._url(path)],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=flags)
            self._played, self._seg_start, self.paused = 0.0, time.monotonic(), False
        rc = self._proc.wait()
        with self._lock:
            if not self.paused:
                self._played += time.monotonic() - self._seg_start
            heard, self.paused, self._proc = self._played, False, None
            return rc == 0 and self._cmd is None, heard

    def _pick(self, previous: str, fast: bool) -> jockey.Item | None:
        exclude = [x.ref for x in (self.cur, self._upcoming) if x]
        item = jockey.next_item(exclude, n_picked=self._n, previous=previous, talk=self.talk,
                                use_llm=not fast and self._n > 0, focus=self.focus)
        if item:
            self._n += 1
        return item

    def _prefetch(self, previous: str) -> None:
        gen = self._gen

        def work():
            try:
                item = self._pick(previous, fast=False)
            except Exception as e:
                console.log(f"[yellow]player: prefetch failed: {e}[/]")
                return
            with self._lock:
                if gen == self._gen and self._upcoming is None:
                    self._upcoming = item

        threading.Thread(target=work, daemon=True).start()

    def _show(self) -> None:
        failures = 0
        previous = ""
        try:
            while True:
                with self._lock:
                    if self._cmd == "stop":
                        break
                    self._cmd = None
                    item, self._upcoming = self._upcoming, None
                    self._gen += 1  # anything still being prefetched was meant for an older queue
                if item is None:
                    item = self._pick(previous, fast=True)
                if item is None:
                    self.message = f"Nothing to play for “{self.focus}”." if self.focus else "Nothing to play yet."
                    break
                with self._lock:
                    self.cur, self.phase = item, "link"
                self._prefetch(f"{item.title} — {item.artist}".strip(" —"))
                if item.link_audio and self.talk:
                    self._play(item.link_audio)
                with self._lock:
                    cmd = self._cmd
                if cmd == "stop":
                    break
                if cmd is None:
                    with self._lock:
                        self.phase = "main"
                    ended, heard = self._play(item.audio)
                    with self._lock:
                        cmd = self._cmd
                    if ended:
                        self._record("full")
                        failures = 0
                    elif cmd is None:  # ffplay gave up on its own: unplayable, move on quietly
                        failures += 1
                        console.log(f"[yellow]player: could not play {item.title}[/]")
                        if failures >= MAX_FAILURES:
                            self.message = "Several items in a row would not play — stopped."
                            break
                    elif cmd == "skip":
                        self._record("skip")
                    if cmd == "stop":
                        break
                elif cmd == "skip" and self.phase == "link":
                    self._record("skip")
                previous = f"{item.title} — {item.artist}".strip(" —")
        except Exception as e:
            self.message = f"player stopped: {e}"
            console.log(f"[red]player: {e}[/]")
        finally:
            with self._lock:
                self._kill()
                self.phase, self.paused = "idle", False
