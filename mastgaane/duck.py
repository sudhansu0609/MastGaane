"""Turn one process's sound down and back up — the music dips while you speak a voice command.

ffplay cannot change its volume once it plays, so this sets the volume of its Windows audio session
(Core Audio, through pycaw) — the same slider as in the Volume Mixer. A session shows up only once
the process makes sound, and Windows remembers ffplay's last session volume (a dip from an earlier
song included), so `Keeper` does not set the level once and hope: one thread holds the current
process at the level asked for, applying it as soon as the session exists and again whenever it is
found elsewhere (checked every second). Windows only; elsewhere it does nothing.
"""
from __future__ import annotations

import sys
import threading
import time


def _volumes(pid: int):
    from pycaw.pycaw import AudioUtilities

    return [s.SimpleAudioVolume for s in AudioUtilities.GetAllSessions() if s.Process and s.Process.pid == pid]


def set_level(pid: int, level: float, wait_s: float = 0.0) -> bool:
    """Set the process's session volume once (0…1); keeps trying up to `wait_s` for its session."""
    if sys.platform != "win32" or not pid:
        return False
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        return False
    try:
        deadline = time.monotonic() + wait_s
        while True:
            vols = _volumes(pid)
            for v in vols:
                v.SetMasterVolume(max(0.0, min(1.0, level)), None)
            if vols or time.monotonic() >= deadline:
                return bool(vols)
            time.sleep(0.1)
    except Exception:
        return False
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


class Keeper:
    """Holds one process (the player's current ffplay) at one level."""

    POLL_S = 1.0

    def __init__(self):
        self._want: tuple[int, float] = (0, 1.0)
        self._changed = threading.Event()
        self._thread: threading.Thread | None = None

    def hold(self, pid: int, level: float) -> None:
        self._want = (pid, max(0.0, min(1.0, level)))
        if sys.platform != "win32":
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="mastgaane-volume")
            self._thread.start()
        self._changed.set()

    def _run(self) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            return
        while True:
            fast = self._changed.wait(self.POLL_S)
            self._changed.clear()
            pid, level = self._want
            if not pid:
                continue
            deadline = time.monotonic() + (3.0 if fast else 0.0)  # a new process: wait for its session
            while True:
                try:
                    vols = _volumes(pid)
                    for v in vols:
                        if abs(v.GetMasterVolume() - level) > 0.01:
                            v.SetMasterVolume(level, None)
                except Exception:
                    vols = []
                if vols or time.monotonic() >= deadline or self._changed.is_set():
                    break
                time.sleep(0.03)  # a muted song's first moments sound until its session is found
