"""MastGaane's side of the shared port law (GUARDIAN_PLAN.md section 11).

8787 used to be a fact: the CLI bound it, the Apple page printed it, and the
phone was told to open it. Here it becomes a wish - `MASTGAANE_PORT` or that
default - and if something already holds it we step forward instead of evicting
whoever is there. Once /health answers as us we publish where we landed into the
shared ledger, print `READY port=N`, and take the entry back out on the way down.

`buzzcaf_ports.py` beside this file is a byte-identical copy of Dexter's
canonical helper; never edit it here.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
import urllib.request
from typing import Callable, Optional

from . import buzzcaf_ports

#: This app's name in the ledger and in every health body.
APP_ID = "mastgaane"
#: Today's number, and only a default: MASTGAANE_PORT wins.
DEFAULT_PORT = 8787
HEALTH_PATH = "/health"


def preferred_port() -> int:
    raw = (os.environ.get("MASTGAANE_PORT") or "").strip()
    if raw.isdigit() and 0 < int(raw) <= 65535:
        return int(raw)
    return DEFAULT_PORT


def choose_port(preferred: Optional[int] = None, host: str = "127.0.0.1") -> int:
    """The preferred port if free, else the next one. Never evicts."""
    return buzzcaf_ports.pick_port(preferred if preferred is not None else preferred_port(), host=host)


def announce_when_ready(
    port: int,
    host: str = "127.0.0.1",
    timeout: float = 40.0,
    on_ready: Optional[Callable[[int], None]] = None,
) -> None:
    """Publish once we really answer there, then say so on stdout."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{probe_host}:{port}{HEALTH_PATH}"

    def _wait() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    if json.loads(response.read().decode("utf-8") or "{}").get("app") == APP_ID:
                        buzzcaf_ports.publish(APP_ID, port, url)
                        print(f"READY port={port}", flush=True)
                        if on_ready is not None:
                            try:
                                on_ready(port)
                            except Exception:
                                pass
                        return
            except Exception:
                pass
            time.sleep(0.25)

    threading.Thread(target=_wait, name="mastgaane-ports-publish", daemon=True).start()
    atexit.register(withdraw)


def withdraw() -> None:
    try:
        buzzcaf_ports.withdraw(APP_ID)
    except Exception:  # a ledger problem must never stop a shutdown
        pass


def running_base_url() -> Optional[str]:
    """Where a running MastGaane is, for the CLI's own advice. None when offline."""
    return buzzcaf_ports.discover(APP_ID, preferred_port(), HEALTH_PATH)
