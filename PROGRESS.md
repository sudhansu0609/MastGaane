# MastGaane — Progress Log

Newest first. A row is **done** only with the evidence that proves it.

## 2026-10-15 — FM Apple mode removed

The listener does not want the Apple Developer Program token route, so the untested `/fm` MusicKit
mode is gone; FM stays on the ad-free audio. Apple links, `apple_id` and the Shortcut route remain.

**Evidence.** `pytest -q` → **44 passed**; `/fm` has no Apple code (`grep -ci apple fm.html` → 0).

## 2026-10-15 — Apple Music: real links, exact Shortcut lines, FM Apple mode

Listener has an Apple Music subscription and wants both the playlist and FM playback there.
The Apple route had never run (0 links). Odesli, the backup resolver, now answers
`401 PUBLIC_API_ACCESS_DEPRECATED` — removed. iTunes Search took same-title songs by other singers
(Dillagi — Khalid Khan → Rahat Fateh Ali Khan's).

- `playlists/apple.py`: a hit needs the title and a shared credited name; second try with the title
  alone; calls spaced 3.2 s (Apple's ~20/min limit). `Song.apple_id` (catalog id) and
  `Song.apple_name` (Apple's spelling, used as the `import.txt` line). `/apple?resolve=1` runs in a
  background thread.
- `/fm`: " Apple Music" mode when `APPLE_DEV_TOKEN` is set — MusicKit JS v3 plays songs that have
  an `apple_id`; programmes / unmatched songs stay on the ad-free audio. Needs the $99 Apple
  Developer Program token: **not tested live**.

**Evidence.** `pytest -q` → **44 passed** (+4 `tests/test_apple.py`). Live `mastgaane sync -t apple`:
**108/121** approved songs linked, 13 no match, no 403s; `reports/apple_import.txt` 121 lines.
DB backup: `data/mastgaane.before-apple-2026-10-15.db`.

## 2026-10-15 — Reclassify results; LLM-only regional labels go to review

`reclassify` with gemma-4-12b flipped 46 songs to approved (Pardesi Pardesi, Dil Mera Tod Diya,
Tere Paas Main, Chupke Se, Darmiyaan, Marbo Re Sugwa Dhanukh…). It also called French, Telugu,
Malayalam and Punjabi songs Bhojpuri/Magahi/Awadhi "folk", and two heuristic (no-LLM) verdicts
slipped through. Now a keep that rests only on an LLM regional label, or on the no-LLM heuristic,
carries `[check] ` and stays *pending* (Discover / FM new finds) — `needs_review()`. 14 such songs
were moved from approved to pending in the live DB.

Discovery: round 1 used only 2 of 12 seeds (the 60-song budget went to the first radios, one seeded
from an LLM-only approval). Now each seed gets an equal quota, and seeds are hand-approved songs, then
songs a rules.yaml singer list vouches for, then LLM-only ones. Round 2: 12 radios — Chandan Tiwari /
Bhikhari Thakur bidesiya, Malini Awasthi kajri, Kalpana, 1990s ghazals (Pankaj Udhas 1997, Chandan
Dass 1991, Ustad Ahmed Hussain 1995, Ghulam Ali 1996), 1950s Rafi/Kishore — 47 queued, 13 filtered.

**Evidence.** `pytest -q` → **40 passed**. Live DB: approved 121, pending 15, rejected 121
(before today: approved 89, pending 4, rejected 164).

## 2026-10-15 — The last 25–30 years (programmes and songs), older kept

Listener wants programme recordings and songs from ~1996 onward, without dropping older classics.

- `archive.year_of()` reads broadcast dates from titles (`16 6 15`, `091216`, `21-08-98`, `1972`…);
  archive.org `date` is used otherwise (checked: it is the broadcast date, e.g. Jaimala Naushad →
  2005-07-31, uploaded 2025). `old_queries()` adds old-recording searches; up to 150 YouTube results.
- `/archive` decade filters + year badges; FM `_episode_item` picks a random era first, unheard first.
- `Song.year` (YouTube Music album year), `discover.fill_years()`; `seed_queries` in rules.yaml.

**Evidence.** `pytest -q` → **39 passed**. Live: Jaimala 4 / 18 / 6 / 80 / 24 episodes across
before-1996 / 1996–2005 / 2006–2015 / 2016+ / unknown; Geetmala reaches 1952; years found for
117/126 songs — approved: 35 before 1996, 20 1996–2005, 24 2006–2015, 37 2016+, 12 unknown.

## 2026-10-15 — MastGaane FM (jockey), auto-fetch, Naya kya hai

Listener asked for a video + radio jockey that keeps learning and brings new things. Choices
(asked 2026-10-15): natural Hindi voice (edge-tts), audio/video switchable, new finds mixed in,
weekly digest, auto-fetch new episodes.

- `mastgaane/jockey.py`: taste from the `play` table (like +3, full +1, skip −1, dislike −4 per
  artist/genre/programme), weighted pick, no repeats in the last 40, every 4th a discovery,
  Vividh-Bharati-like `SCHEDULE` slots for archive episodes; Hindi link lines from the LLM
  (prefetched while the current item plays) or templates; edge-tts voice cached in `data/jockey_voice/`.
- `/fm` page: one `<audio>` for voice + song (iPhone lock screen keeps playing), YouTube player in
  video mode, ⏭/👍/👎 → `/jockey/event`; skip uses an instant template link if the next item is not ready.
- `archive.fetch_new()` / `mastgaane fetch-episodes`; `mastgaane/digest.py` + `/naya` + `mastgaane digest`;
  both run from `run-all` (digest only when the last one is > 7 days old).

**Evidence.** `pytest -q` → **36 passed** (+7 in `tests/test_jockey.py`). Live on 8788: `/jockey/next`
returned "Chupke Se — A. R. Rahman…" with an LLM-written Devanagari link and a 68 KB voice mp3;
song audio was already prefetched (206 in 0.002 s). Headless Chrome on `/fm`: Start → voice link
played → song reached t=4 s → Skip → next link playing within 5 s; no page errors. The two test
skips were deleted from `play`. `match`: 114/115 approved songs now have a YouTube id.

## 2026-10-15 — Taste fixes, Radio, Discover, Archive

The listener said the app served nothing they liked. Causes found in `data/mastgaane.db`:
Bhojpuri was not an allowed language; qwen2.5:7b rejected good songs because "the caption is
not relevant"; `browser.json` (YouTube Music auth) is broken, so 0 of 89 approved songs had a
YouTube match and the Playlist player had nothing to play.

- Classifier: Bhojpuri/Awadhi/Maithili/Magahi allowed; `ghazal_artists`, `folk_artists`,
  `folk_keywords` rules; prompt forbids caption-based rejection. `.env` `LLM_MODEL=gemma-4-12b:latest`
  (9/10 on a hand-checked set vs 5/10 for qwen2.5:7b, ~20 s per song).
- `set_manual_status` marks hand decisions (`you: …`); `mastgaane reclassify` skips them.
- `mastgaane/radio.py` + `/radio` (hls.js for AIR m3u8 streams; directory refresh never blocks a page).
- `mastgaane/discover.py`: anonymous ytmusicapi search (`match`) and radio-based discovery
  (`discover`, also in `run-all`); `/discover` page with 👍/👎.
- `mastgaane/archive.py` + `/archive`: 8 programmes, ~350 episodes (archive.org + YouTube via
  ytmusicapi and yt-dlp search), title must name the programme, < 5 min dropped; YouTube audio
  fetched once and served with Range support.
- `mastgaane/buzzcaf_ports.py` was missing from the working tree; restored as a byte copy of
  `dexter/backend/buzzcaf_ports.py` (sha256 `51649b81…`).

**Evidence.** `uv run pytest -q` → **29 passed**. Live on port 8788: all pages 200; `/archive/ia/jaimala-k-j-yesudas.json`
→ 1 MP3; `/archive/yt/axyuY6yzeuI.audio` with `Range: bytes=0-1023` → 206 audio/mp4.
DB backup before reclassify: `data/mastgaane.before-reclassify-2026-10-15.db`.

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
