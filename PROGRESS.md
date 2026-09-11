# MastGaane — Progress Log

Newest first. A row is **done** only with the evidence that proves it.

## 2026-09-10 — Ports: nothing hardcoded (GUARDIAN_PLAN.md section 11, P2)

8787 was written down in five places — the CLI, the Apple phone page, the sync
report, the setup advice and the README. It now lives in one.

- `mastgaane/buzzcaf_ports.py` — byte-identical copy of
  `dexter/backend/buzzcaf_ports.py` (sha256 `51649b81…`, 18 243 bytes).
- `mastgaane/ports.py` — `preferred_port()` (`MASTGAANE_PORT`, else
  `DEFAULT_PORT = 8787`, the only literal), `choose_port()`, publish-when-healthy
  to `%LOCALAPPDATA%\Buzzcaf\ports.json`, `READY port=N`, `withdraw()`, and `running_base_url()` for the
  CLI's own advice.
- `mastgaane/cli.py serve` — `--port` is a preference; uvicorn is handed the app
  object (not an import string) so the bound port survives; the phone URL and the
  local URL are printed only once we answer, with the port we actually took.
- `mastgaane/web/app.py` — `/health` →
  `{"app":"mastgaane","status":"ok","port":n,"pid":n}`; the Apple page's
  `lan_url` uses the bound port instead of a literal.
- `mastgaane/playlists/sync.py` and `setup apple` name the running port, or the
  preferred one when nothing is running.

**Evidence.** `uv run pytest -q` → **24 passed** (16 before, +8 in
`tests/test_ports.py`). Live against the real ledger:

```
preferred 8787 free      -> bound 8787
  ledger  {"port":8787,"pid":51972,"health":"http://127.0.0.1:8787/health","extra":{}}
  health  {"app":"mastgaane","status":"ok","port":8787,"pid":51972}
preferred 8787 occupied  -> bound 8788   (the dummy listener kept 8787)
  health  {"app":"mastgaane","status":"ok","port":8788,"pid":46200}
after Ctrl-C             -> no "mastgaane" key in the ledger, both runs
```
