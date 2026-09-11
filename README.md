# MastGaane ♪

Watches the Instagram Reels your algorithm serves you late at night, picks out the melodious
songs / ghazals, identifies them, and builds a playlist on **YouTube Music**, **Spotify** and
**Apple Music** — with a local web UI to review and play them.

```
nightly task ─► harvest (your Chrome scrolls /reels, listens to Instagram's own responses)
             ─► resolve (IG attribution → Shazam → Whisper + LLM; LLM classifies genre — local Ollama or Claude)
             ─► sync    (YouTube Music / Spotify / Apple import list)
             ─► report  (reports/YYYY-MM-DD.md)
web UI (localhost, MASTGAANE_PORT or 8787 - it steps forward if that is busy and prints `READY port=N`): approve / reject / edit songs, embedded player, open in each service
```

## Setup (once)

```powershell
cd B:\youtubeProjects\Buzzcaf_Media\MastGaane
uv sync                      # Python 3.13 venv with all deps (ffmpeg must be on PATH — it is)
copy .env.example .env       # defaults work with local Ollama; edit limits / services / optional keys
uv run mastgaane setup login # opens Chrome with its own profile → log in to Instagram once
uv run mastgaane setup ytmusic   # paste request headers from music.youtube.com (see prompt)
```

Optional:

- **Spotify** (needs Spotify Premium): create an app at <https://developer.spotify.com/dashboard>
  with redirect URI `http://127.0.0.1:8788/callback`, put the client id/secret in `.env`,
  set `SPOTIFY_ENABLED=true`, run `uv run mastgaane setup spotify`.
- **AudD** fallback fingerprinting: `AUDD_API_KEY` in `.env` (Shazam via `shazamio` is used first, free).
- **LLM** (song guessing + genre classification) — local by default, Claude optional:
  - **Ollama** (installed on this PC, `qwen2.5:7b` already pulled): nothing to do. `LLM_PROVIDER=auto`
    starts `ollama serve` if needed and uses it. Better Hindi/Urdu knowledge with 16 GB VRAM:
    `ollama pull qwen2.5:14b` (or `qwen3:14b`, `gemma3:12b`) and set `LLM_MODEL`.
  - **LM Studio / llama.cpp / vLLM**: `LLM_PROVIDER=openai`, `LLM_BASE_URL=http://127.0.0.1:1234/v1`, `LLM_MODEL=<name shown in the server>`.
  - **Claude**: `ANTHROPIC_API_KEY` in `.env` (or `ant auth login`). With `LLM_FALLBACK=true` it is
    used whenever the local model is down or returns garbage; `LLM_PROVIDER=claude` makes it primary.
  - Check what is reachable: `uv run mastgaane llm --test`.
  - No LLM at all (`LLM_PROVIDER=none`): tier-3 guessing is skipped, classification uses `rules.yaml` keywords.

## Daily use

```powershell
uv run mastgaane harvest            # scroll ~150 reels (≈25 min), headed Chrome
uv run mastgaane resolve            # identify + classify songs
uv run mastgaane sync               # push approved songs to enabled services
uv run mastgaane serve              # prints the URL it took (MASTGAANE_PORT, else 8787) — review & play
uv run mastgaane run-all            # all of the above + report
uv run mastgaane songs --status pending
uv run mastgaane approve 12 15      # or --reject
```

Schedule it nightly (00:30) with Windows Task Scheduler:

```powershell
.\scripts\schedule_task.ps1 -At "00:30"
```

Keep the PC logged in (screen lock is fine) — the task uses your interactive session so Chrome
can run headed. Set `HEADLESS=true` only after a week of clean headed runs; Instagram flags
headless browsers more readily.

## Only Hindi film songs, ghazals and folk

`rules.yaml` has two allow-lists the classifier enforces after the LLM labels each song:

```yaml
allowed_languages: [hindi, urdu]        # language the song is SUNG in (captions are ignored)
allowed_genres:    [film, ghazal, folk] # add sufi / classical / devotional / indie to widen
```

English, Punjabi, Tamil… songs are marked *rejected* with the reason shown in the UI. If no LLM
is reachable, a script/romanised-Hindi heuristic decides language.

## Apple Music

Run `uv run mastgaane setup apple` — it prints the steps with your PC's LAN address. Summary:

- **No developer account (default):** `uv run mastgaane serve --lan`, then open
  `http://<pc-ip>:<the port serve printed>/apple` on the iPhone: every approved song has an *Open* button that lands
  in Apple Music (links resolved from the Indian storefront). For bulk import build the 3-action
  Shortcut once (Get Contents of URL `…/apple/import.txt` → Split Text → Repeat: Search Music →
  Add to Playlist) and run it whenever you like.
- **Apple Developer Program:** set `APPLE_DEV_TOKEN`, open `/apple/auth` once to authorize, and
  `sync` creates/fills the playlist in your library via MusicKit.

## How song identification works

| Tier | Source | Confidence |
|---|---|---|
| 1 | Instagram's own music attribution (licensed tracks) | 0.90 |
| 2 | Shazam fingerprint of a 25 s clip (AudD fallback if key set) | 0.75 |
| 3 | faster-whisper lyric transcript + caption → LLM guess (local Ollama or Claude) | ≤ 0.70 |

Both LLM steps ask for a strict JSON object (pydantic schema). With Ollama the schema is passed as
`format`, so the model is *constrained* to valid output; with Claude it is `messages.parse`
structured output. Prompts are identical across backends, so you can switch freely.

Songs with `keep=true` and confidence ≥ `AUTO_APPROVE_MIN_CONFIDENCE` (0.8) are approved
automatically; the rest wait for you in the web UI. Curation rules (always-keep artists, skip
patterns) live in `rules.yaml`.

## Keeping the Instagram account safe

- Real Chrome + persistent profile, attached over CDP — no automation fingerprint.
- We never call Instagram endpoints; we only read the responses the page fetches itself.
- Human-like pacing: 4–12 s per reel, 15–25 s on musical ones (this also trains your
  algorithm toward songs), random pauses, one session per night, ~150 reels.
- If Instagram ever shows "suspicious activity", run `mastgaane setup login`, clear it in the
  window, and lower `MAX_REELS` for a few days.

## Development

```powershell
uv run pytest -q                          # parser / pipeline / web tests (no network)
uv run mastgaane harvest --capture --max-reels 5   # saves raw GraphQL payloads to data/captures
```

If Instagram changes its payload shape, drop a captured file into `tests/fixtures/` and extend
`mastgaane/harvester/parse.py` — it walks the JSON defensively, so usually only a new field
name needs adding.
# MastGaane
