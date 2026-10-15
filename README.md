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

## ▶ MastGaane FM — your jockey

Open the **▶ FM** tab and press *Start*. It plays one endless channel:

- your approved songs, weighted by what you like; every 4th item is a new find from Discover
- old programmes in their time slots, loosely after the Vividh Bharati day: Sangeet Sarita and
  Triveni in the morning, Bhoole Bisre Geet after lunch, Jaimala at 7 pm, Hawa Mahal at 8
  (Geetmala on Wednesdays), Chhaya Geet late at night
- a Hindi announcer between items: the local LLM writes the line while the previous song plays,
  and a Microsoft neural voice reads it (`JOCKEY_VOICE=hi-IN-SwaraNeural` or `hi-IN-MadhurNeural`;
  needs internet; untick *host voice* to turn it off)
- **🎧 Audio** plays everything as ad-free audio served by this app, and works with the iPhone
  screen locked. **📺 Video** plays music videos in the YouTube player, so YouTube ads may appear;
  programmes always stay audio.

It learns from you: ⏭ (skip), letting a song finish, 👍 and 👎 are stored (`play` table) and shift
which singers, genres and programmes come up. 👍 also approves a new find, and 👎 rejects a song for good.

Every night `run-all` also finds new songs, downloads a couple of new episodes per programme, and
once a week builds **Naya kya hai** (`/naya`, or `uv run mastgaane digest`): your week in numbers,
new finds, new episodes, one-tap rule suggestions (a singer you keep liking joins `folk_artists`),
and a few ideas from the LLM.

## Radio, Archive and Discover (web UI tabs)

- **Radio** — live Vividh Bharati, AIR FM Gold, AIR Raagam, Bhojpuri and purane-gaane stations.
  More are pulled from radio-browser.info weekly: `uv run mastgaane radio --refresh`.
- **Archive** — old programmes without ads: Hawa Mahal, Chhaya Geet, Jaimala, Bhoole Bisre Geet,
  Sangeet Sarita, Geetmala, Inse Miliye, Triveni. archive.org recordings play directly; YouTube
  off-air uploads are fetched once with yt-dlp into `data/archive_audio/` and played from there.
  Every episode shows its broadcast year when the title or archive.org has it, with filters by period
  (before 1996 / 1996–2005 / 2006–2015 / 2016–now); extra searches look for old recordings.
  FM rotates through the periods, so older episodes come up as often as recent uploads.
- **Discover** — new songs like the ones you keep: `uv run mastgaane discover` starts YouTube Music
  radios from your approved songs and a few singers in `rules.yaml`, classifies what it finds and
  queues the keepers. 👍 / 👎 on the page; hand-approved songs are preferred as the next seeds.
  `run-all` does this every night. No YouTube Music login is needed for this. `seed_queries` in
  `rules.yaml` steers it — by default toward 1990s–2000s ghazals and Bhojpuri folk, while your
  approved classics keep older songs coming. Songs show their release year (YouTube Music's album
  year; a re-release can make it later).
- `uv run mastgaane match` gives approved songs a YouTube match so the Playlist player can play them.
- `uv run mastgaane reclassify` re-checks rejected/pending songs after you edit `rules.yaml` or change
  `LLM_MODEL`; songs you approved or rejected yourself are never touched.

## Only Hindi film songs, ghazals and Bhojpuri / Hindi-belt folk

`rules.yaml` has two allow-lists the classifier enforces after the LLM labels each song:

```yaml
allowed_languages: [hindi, urdu, bhojpuri, awadhi, maithili, magahi]  # language the song is SUNG in
allowed_genres:    [film, ghazal, folk]  # add sufi / classical / devotional / indie to widen
ghazal_artists:    [Jagjit Singh, Chitra Singh, ...]         # always kept as ghazal
folk_artists:      [Sharda Sinha, Bharat Sharma Vyas, ...]   # always kept as folk
folk_keywords:     [chhath, kajri, chaita, sohar, birha, nirgun, ...]
```

The LLM only labels language and genre; it may not reject a song because the reel caption is
unrelated. `gemma-4-12b` labelled 9/10 of a hand-checked set correctly, `qwen2.5:7b` 5/10.
English, Punjabi, Tamil… songs are marked *rejected* with the reason shown in the UI. If no LLM
is reachable, a script/romanised-Hindi heuristic decides language.

## Apple Music

Run `uv run mastgaane setup apple` — it prints the steps with your PC's LAN address. Summary:

- **No developer account (default):** `uv run mastgaane serve --lan`, then open
  `http://<pc-ip>:<the port serve printed>/apple` on the iPhone: every approved song has an *Open* button that lands
  in Apple Music (links resolved from the Indian storefront). For bulk import build the 3-action
  Shortcut once (Get Contents of URL `…/apple/import.txt` → Split Text → Repeat: Search Music →
  Add to Playlist) and run it whenever you like.
  `uv run mastgaane sync -t apple` finds the links (iTunes Search API, ~3 s per song because Apple
  allows ~20 searches a minute). A link counts only when the title *and* an artist agree; for a
  matched song `import.txt` carries Apple's own spelling, so the Shortcut lands on that exact track.
- **Apple Developer Program:** set `APPLE_DEV_TOKEN`, open `/apple/auth` once to authorize, and
  `sync` creates/fills the playlist in your library via MusicKit. The token also adds an
  ** Apple Music** mode to `/fm`: songs with an Apple match play in full from your subscription
  (MusicKit JS); programmes and unmatched songs stay on the ad-free audio.

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
