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
- a Hindi announcer between items: the local LLM writes the line while the previous song plays.
  The host feels the song: hushed and slow before a sad ghazal, tender for a romantic one, bright
  and quick for an upbeat one or a new find. Like a real RJ she talks before every 2nd song
  (`JOCKEY_TALK_EVERY`; programmes, new finds and caught songs always). Untick *host voice* to turn
  her off. Who speaks:
  - **Gemini TTS** (set `GEMINI_API_KEY`, free key at https://aistudio.google.com/apikey): one of
    Google's native Hindi voices (`GEMINI_TTS_VOICE=hi-in-podcaster-9`, a 25-year-old radio host from
    Delhi), told the feeling in words. The free tier gives 10 links a day per model; the 3.8 Flash and
    Flash-Lite take turns (`GEMINI_TTS_MODEL`), so ~20 links ≈ 40 songs a day. When both are used up
    she goes quiet and the music flows on (`JOCKEY_FALLBACK=edge` lets the Microsoft voice stand in).
    Hear her with `uv run mastgaane host-voice --play`.
  - without a Gemini key, a Microsoft neural voice (`JOCKEY_VOICE`: `hi-IN-SwaraNeural`,
    `hi-IN-MadhurNeural` (male) or `mr-IN-AarohiNeural`; en-IN Neerja and ur-IN Gul cannot read
    Devanagari), each sentence in its own pitch and pace
- **🎧 Audio** plays everything as ad-free audio served by this app, and works with the iPhone
  screen locked. **📺 Video** plays music videos in the YouTube player, so YouTube ads may appear;
  programmes always stay audio.

It learns from you: ⏭ (skip), letting a song finish, 👍 and 👎 are stored (`play` table) and shift
which singers, genres and programmes come up. 👍 also approves a new find, and 👎 rejects a song for good.

Every night `run-all` also finds new songs, downloads a couple of new episodes per programme, and
once a week builds **Naya kya hai** (`/naya`, or `uv run mastgaane digest`): your week in numbers,
new finds, new episodes, one-tap rule suggestions (a singer you keep liking joins `folk_artists`),
and a few ideas from the LLM.

## On the PC speakers (Dexter / Alexa)

`mastgaane serve` also runs the jockey on the PC itself — no browser, played by `ffplay` (FFmpeg).
Dexter drives it over HTTP:

| Route | Does |
|---|---|
| `POST /api/player/play` `{"focus": "ghazals", "talk": true}` | start, or switch to a wish: a genre (ghazal, folk, film…), a language (bhojpuri, urdu), an era (`90s`, `purane gaane`), a programme (`Hawa Mahal`) or a singer (`Jagjit Singh`); empty = the usual mix |
| `POST /api/player/{pause,resume,next,like,dislike,stop}` | the remote control (like/dislike teach the same taste as `/fm`) |
| `POST /api/player/{mute,unmute}` | no sound, the show going on: songs and links play on silently (`now` says `muted`); play / resume unmute |
| `GET /api/player/now` | state + a `summary` sentence to speak |
| `POST /api/player/song` `{"song_id": 12, "now": true}` | play one song from the collection now (`now: false` = next, only while playing) |
| `POST /api/shutdown` | stop the music and close MastGaane (only from this PC) — Dexter's "close MastGaane" |

**Tray icon.** While `mastgaane serve` runs (also when Dexter starts it with no window) an amber ♪
sits in the Windows tray: hover for what is playing, left click to play / pause, right click for
Mute, Next, Like, Never play this again, Stop music, Open MastGaane and Quit MastGaane (🔇 in the
tooltip while muted). Windows 11 puts new
icons under the **^** overflow at first — drag it onto the taskbar to keep it in sight.
`TRAY=false` (or `serve --no-tray`) hides it.

**Voice commands — Dexter is the ear.** Say its name — **"Bharti"** (the surest), "Radio" or "FM"
(`VOICE_WAKE_WORDS`) — and then the command. Dexter hears it (Whisper large-v3-turbo on the GPU, his
owner-voice check, a name only at the start of a sentence) and hands it over (`POST /api/voice/heard`);
MastGaane does the rest:

- **"Bharti"** … *ding*, the music dips … **"agla gaana"** — Dexter sends the next sentence too, no name
  needed (`{"listening": true}` in the reply to the name alone);
- or in one breath: **"Bharti, next song"** — done straight away;
- MastGaane closed: **"Bharti, ghazal sunao"** (a wish to play) makes Dexter start it first.

Its own mic is off by default (`VOICE_CONTROL=false`): live, 17 Oct, on the new Yeti mic its small CPU
model took the songs and Hawa Mahal's dialogue for "Bharti" every few seconds (26 false chimes in 47
minutes), while Dexter, on the same music, handed over only real calls. `VOICE_CONTROL=true` (or the
tray's *Own mic too*) listens on its own mic as well — for when Dexter is not running; everything below
about echoes is about that mic.

| Say (after the name) | Does |
|---|---|
| "next song" · "agla gaana" · "change karo" | skip |
| "stop" · "gaana band karo" / "pause" · "ruko" / "chalao" | stop / pause / play again |
| "I like this" · "badhiya" / "I don't like this" · "hatao" | like / never again |
| "what's playing?" · "kaun sa gaana hai" | a notification with the song |
| "mute" · "awaaz band karo" / "unmute" · "awaaz kholo" | sound off, the songs going on / sound back |
| "ghazal sunao" · "gajal" · "Hawa Mahal lagao" · "Jagjit Singh ke gaane" · "something sad" · "purane gaane" | play that |

**Mishearings** are corrected the way Dexter's lexicon does it (`mastgaane/lexicon.py`): Whisper hears
Hindi in English mode, and live it wrote "Bharti" as "Marthi." / "Bhaarati" and "Hanuman Chalisa" as
"Leharman Chalisa". The name is also recognised by how it sounds (b/bh/m/v + "ar" + t/th/d + i/ee/y:
Marthi, Marty, Bhaarati, Smartie, Barty…, but not "party", "Bharat", "bhakti" or the lyric "marte"), and
a wish that matches nothing exactly is matched to your titles, singers and programmes by consonant
shape ("leharman chalisa" → Hanuman Chalisa, "jagjeet singh" → Jagjit Singh, "hawa mehal" → Hawa Mahal).

It never acts on the songs or the host: a lyric that happens to hold a name must also sound like a
command; Whisper's echo of its own prompt (on plain music it writes "Bharti.", "Hello, Bharti." back) is
caught by a second listen without the prompt: over the music a name with no command after it counts
only if the name is there again in some form (not "part": "a part of you" once counted); while the host talks (and a few
seconds after) only stop / pause / next are obeyed; and one command heard twice (by Dexter too) is done
once. "Dexter, …" also dips the music, so Dexter hears you clean; **Dexter ignores anything that starts
with Bharti, Radio or FM** and hands it over (`POST /api/voice/heard`). The tray has *Speak a command*
(no name needed, own mic only) and the own-mic switch; `VOICE_CHIME=false` silences the chime. `GET /api/voice` lists what it heard and what it did;
`VOICE_DEBUG=true` also logs every transcription (with its no-speech score) and every decision to
`data/voice_debug.log`, and keeps the audio of the last 120 with a name or a command in them in
`data/voice_debug/`.

How well it hears, in a simulated room (real songs at the level the Brio hears your speakers, commands
spoken by edge-tts voices over them): with your voice 8 dB above the music 16 of 17 commands were done,
0.2–0.6 s after the last word; 3 dB above, "Bharti" still worked for nearly everything while "Radio" and
"FM" (short, English-sounding, easily lost in a song) mostly did not; 80 s of music alone did nothing.

**The collection.** Besides your own songs, the jockey plays from a big collection — about 5,000 songs,
nearly all from before 2010, at most 200 from 2010 on (yours included): `uv run mastgaane library` fills
it from YouTube Music — the singers, composers and playlists in `rules.yaml` `library` (Rafi, Lata,
Kishore, R.D. Burman… Kumar Sanu, Alka Yagnik… Jagjit Singh, Ghulam Ali… Anup Jalota, Sharda Sinha), each
up to its number, in turns. Remixes, lofi, jhankar and jukebox uploads and second uploads of a song are
skipped; the local LLM (never a paid API) labels each one — the language it is sung in (Lata's Marathi
and Hemant Kumar's Bengali songs drop out), genre, mood, the film's year. Stop it any time and run it
again to carry on; `--stats` shows what it holds. With no wish, three picks in ten still come from your
own songs.

**Through the day.** With no wish, about two thirds of the songs suit the hour: bhakti and peaceful
songs in the morning, happy ones after lunch, romantic ones in the evening, ghazals and sad songs at
night — around the Vividh Bharati programmes in their slots. It also learns what *you* play at each
hour: the moods and genres you finish there come more often then, the ones you skip less. Nothing of
the last 40 songs comes back — in any upload of it ("Hum Tere Shahar Me Aaye Hai" and "…Mein Aaye
Hain" are one song). The first time you start it on a
morning it opens with **Hanuman Chalisa** (Hariharan; `MORNING_OPENER`, once a day). Where a part of
the day runs short, the nightly run finds more from `rules.yaml` `time_seeds` (bhajans for the
morning…); `uv run mastgaane discover --time morning` does it now.

## 🎤 Listen — Shazam inside MastGaane

Hear a song you like? Open the **🎤 Listen** tab (or ask Dexter). MastGaane names it with Shazam,
saves it to your collection as *approved* (whatever the language rules say — you chose it),
finds it on YouTube so the players can play it, and **MastGaane FM plays it next** with a
special announcement. Like every approved song it joins the YouTube Music / Apple Music lists
at the next `sync` (Shazam already supplies its Apple Music id).

- **🎤 Listen** — the PC's microphones listen for `LISTEN_SECONDS` (10). Every real mic records
  at once and the loudest take is used; virtual devices and Bluetooth hands-free mics are skipped
  (opening one drops your headphones into call quality). `LISTEN_MIC=Brio` pins one.
- **🔊 What's playing on this PC** — names a reel or video playing in the browser (it records
  the PC's own sound output, not the room).
- **📱 Record on phone** — on the iPhone (`mastgaane serve --lan`, open `/listen`) it opens the
  camera: point it anywhere for ~10 s, only the sound is used. **📁 Pick a recording** takes a
  voice memo or video.
- **“Hey Siri, Mast Pehchano”** — a Shortcut with the iPhone's own Shazam action posting to
  `/api/listen/save`; the step-by-step is at the bottom of the Listen tab.
- *After saving*: play it next on the PC speakers (only if MastGaane is already playing), now,
  or just save it. The /fm page in a browser picks it up as its next song by itself.

```powershell
uv run mastgaane listen             # PC mic
uv run mastgaane listen --pc        # what this PC is playing
uv run mastgaane listen -f take.m4a # a recording
uv run mastgaane listen --mics      # which microphones it uses
```

| Route | Does |
|---|---|
| `POST /api/listen` `{"source": "mic" or "pc", "seconds": 10, "play": "next"}` | record on the PC, name it, keep it — returns a `summary` to speak |
| `POST /api/listen/file` (multipart `file`, `play`) | the same for a phone recording |
| `POST /api/listen/save` `{"title", "artist", "apple_url", "play"}` | keep a song the iPhone's Shazam already named |

Recordings go to a temp folder that is deleted straight after; Shazam gets a fingerprint, not
the audio (AudD, if `AUDD_API_KEY` is set and Shazam misses, gets the clip).

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
  `sync` creates/fills the playlist in your library via MusicKit.

## How song identification works

| Tier | Source | Confidence |
|---|---|---|
| 1 | Instagram's own music attribution (licensed tracks) | 0.90 |
| 2 | Shazam fingerprint of a 25 s clip (AudD fallback if key set; needs `audioop-lts` on Python 3.13) | 0.75 |
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
