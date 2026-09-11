"""MastGaane obeys the shared port law (GUARDIAN_PLAN.md section 11).

8787 is a preference, not a fact; a busy port is stepped over rather than taken;
/health names the app so a scan cannot confuse us with a neighbour; and what the
Apple page tells the phone is the port we are really on.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

from mastgaane import buzzcaf_ports, ports

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO.parent / "dexter" / "backend" / "buzzcaf_ports.py"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ports.json"
    monkeypatch.setenv("BUZZCAF_PORTS_FILE", str(path))
    return path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A web client on a throwaway database - /health needs no songs."""
    from mastgaane import config, db

    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    db._engine = None
    from fastapi.testclient import TestClient

    from mastgaane.web.app import app as web_app

    with TestClient(web_app) as test_client:
        yield test_client
    db._engine = None


@pytest.fixture()
def occupied():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def test_health_says_who_and_where_we_are(client):
    body = client.get("/health").json()
    assert body["app"] == ports.APP_ID == "mastgaane"
    assert body["status"] == "ok"
    assert body["pid"] == os.getpid()
    assert isinstance(body["port"], int)


def test_the_apple_page_names_the_port_we_bound(client):
    from mastgaane.web import app as web

    before = web.bound_port()
    try:
        web.set_bound_port(before + 3)
        assert web.bound_port() == before + 3
        assert client.get("/health").json()["port"] == before + 3
    finally:
        web.set_bound_port(before)


@pytest.mark.skipif(not CANONICAL.exists(), reason="Dexter checkout not beside this one")
def test_the_port_helper_is_a_byte_identical_copy():
    copy = REPO / "mastgaane" / "buzzcaf_ports.py"
    assert hashlib.sha256(copy.read_bytes()).hexdigest() == hashlib.sha256(CANONICAL.read_bytes()).hexdigest()


def test_preferred_port_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MASTGAANE_PORT", "9787")
    assert ports.preferred_port() == 9787
    monkeypatch.setenv("MASTGAANE_PORT", "not-a-port")
    assert ports.preferred_port() == ports.DEFAULT_PORT
    monkeypatch.delenv("MASTGAANE_PORT")
    assert ports.preferred_port() == ports.DEFAULT_PORT


def test_a_busy_port_is_stepped_over_never_taken(occupied):
    chosen = ports.choose_port(occupied)
    assert occupied < chosen <= occupied + buzzcaf_ports.SPAN
    spare = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            spare.bind(("127.0.0.1", occupied))
            spare.listen(1)
    finally:
        spare.close()


def test_publish_then_withdraw_leaves_the_ledger_clean(ledger):
    buzzcaf_ports.publish(ports.APP_ID, 9001, f"http://127.0.0.1:9001{ports.HEALTH_PATH}")
    entry = json.loads(ledger.read_text(encoding="utf-8"))["mastgaane"]
    assert entry["port"] == 9001 and entry["pid"] == os.getpid()
    ports.withdraw()
    assert "mastgaane" not in json.loads(ledger.read_text(encoding="utf-8"))


def test_discovery_ignores_a_listener_that_is_not_us(occupied, ledger):
    """Rule 4: a listener that will not name itself MastGaane does not count."""
    buzzcaf_ports.publish(ports.APP_ID, occupied, f"http://127.0.0.1:{occupied}{ports.HEALTH_PATH}")
    try:
        assert buzzcaf_ports.discover(ports.APP_ID, occupied, ttl=0) is None
    finally:
        ports.withdraw()


def test_the_default_port_lives_in_exactly_one_place():
    offenders = []
    for path in (REPO / "mastgaane").rglob("*.py"):
        if path.name in {"buzzcaf_ports.py", "ports.py"} or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if str(ports.DEFAULT_PORT) in line and not line.lstrip().startswith("#") and "help=" not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders
