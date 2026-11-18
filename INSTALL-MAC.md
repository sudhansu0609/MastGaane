# MastGaane on a Mac

This guide installs MastGaane on a Mac with all your songs and the programs it needs. One script
does most of the work: `installer/install_mac.sh`.

## What gets moved, and how

MastGaane stores **links**, not music files. Each song is a YouTube video id. A song is downloaded
the first time it plays and kept in `data/archive_audio` after that.

| What | Where it is | How it gets to the Mac |
|---|---|---|
| The code, `rules.yaml` | git | `git clone` |
| **The song catalogue**: all 39,262 songs (title, artist, YouTube id, genre, language, mood, year, approved / library / rejected) | `library/songs.jsonl.gz`, in git (3 MB) | the installer imports it |
| Your database: likes, skips, the taste the FM learned, Instagram reels | `data/mastgaane.db` (not in git: private) | the data zip (step 1) |
| Songs and radio programmes already downloaded (~1 GB) | `data/archive_audio` (not in git) | the data zip, or downloaded again on first play |
| The host's recorded voice clips (Gemini allows only 10 a day) | `data/jockey_voice` | the data zip |
| Your API keys | `.env` (not in git: secret) | the data zip, or type them in again |
| The YouTube Music login | `browser.json` (not in git: secret) | the data zip, or `mastgaane setup ytmusic` again |
| The Instagram login | `chrome-profile/` | **can't be moved** (Chrome encrypts it for each computer). Log in again on the Mac |

The GitHub repo is **public**. Only the code and the song catalogue go there. Your keys, logins,
listening history and audio stay off it.

## You need

- macOS 11 (Big Sur) or newer, Apple silicon or Intel
- About 3 GB free. Add 1.2 GB if you bring the data zip, and 6 GB for the optional local LLM.
- An internet connection

## 1. On the Windows PC: pack your data (recommended)

Quit MastGaane first (tray icon → **Quit MastGaane**). Then, in PowerShell:

```powershell
cd B:\youtubeProjects\Buzzcaf_Media\MastGaane
powershell -ExecutionPolicy Bypass -File installer\pack_for_mac.ps1
```

This writes `Downloads\MastGaane-data.zip`, about 1.1 GB. Copy it to the Mac's **Downloads** folder.
The easiest way, if both computers are on your Tailscale network, is Taildrop. Run this on the PC
(`tailscale status` lists your Mac's name):

```powershell
& "C:\Program Files\Tailscale\tailscale.exe" file cp "$env:USERPROFILE\Downloads\MastGaane-data.zip" <your-mac-name>:
```

The zip lands in the Mac's Downloads. A USB drive or your own private cloud folder (iCloud Drive,
Google Drive, OneDrive) works too.

> The zip contains your API keys and the YouTube Music login. Never share it through a public
> link, and delete it from the cloud folder once the Mac is set up.

You can skip this step. The Mac then still gets every song from the catalogue in git, but it
starts with no likes or listening history, and you add your keys yourself (step 4).

## 2. On the Mac: Homebrew

Open **Terminal** (⌘ Space → "Terminal"). If you don't have [Homebrew](https://brew.sh) yet, install it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

At the end, Homebrew prints two `eval ...` lines under "Next steps". Run both. Homebrew also
installs Apple's command-line tools, which include `git`.

## 3. Get MastGaane and run the installer

```bash
git clone https://github.com/sudhansu0609/MastGaane.git ~/MastGaane
cd ~/MastGaane
bash installer/install_mac.sh ~/Downloads/MastGaane-data.zip
```

If you skipped step 1, leave the zip off: `bash installer/install_mac.sh`.

The script prints each step as it goes:

1. **Tools** from Homebrew: `ffmpeg` (includes `ffplay`, which plays the music, and handles mic
   recording), `uv` (Python 3.13 and every package) and `rust` (used once, to build the Shazam module).
2. **Google Chrome**, if you don't have it. Chrome scrolls your Instagram Reels.
3. **Your data**: it unpacks the zip into `data/`, `.env` and `browser.json`. If a database was
   already on the Mac, it saves a copy first.
4. **Settings**: if there's no `.env`, it creates one. It then removes the Windows-only values: the
   PC's Chrome path and its microphone names (Blue Yeti / Brio).
5. **Python environment** (`uv sync`). The first run builds the Shazam module with Rust, which
   takes a few minutes.
6. **Songs**: it adds every catalogue song that isn't in the database yet.
7. **Local LLM (optional)**: it asks before installing Ollama and `gemma4:e4b-it-qat` (~6 GB). The
   LLM writes the host's links and sorts new songs. MastGaane plays fine without it. To decide
   without the prompt, pass `--llm` or `--no-llm`.
8. **MastGaane.app** in `~/Applications`, with the MG icon.

You can run the script again at any time. It only adds what's missing and rebuilds the app.

## 4. One-time logins and keys

If you brought the zip, your `.env` and YouTube Music login came with it. You only need the
Instagram login.

```bash
cd ~/MastGaane
uv run mastgaane setup login     # Chrome opens: log in to Instagram once (only if you use the Reels harvest)
```

Without the zip, also do this:

```bash
open -e .env                     # TextEdit: fill in GEMINI_API_KEY (the host's voice); optional: ANTHROPIC_API_KEY, AUDD_API_KEY
uv run mastgaane setup ytmusic   # paste request headers from music.youtube.com (follow the prompt)
```

Keep each comment in `.env` on its own line. A `# comment` after a value becomes part of the value.

**Microphone (🎤 Listen, voice commands).** The first time MastGaane uses the mic, macOS asks for
permission. Click **Allow**. If you clicked Don't Allow, turn it on in **System Settings → Privacy &
Security → Microphone** (for *MastGaane*, or for *Terminal* if you started it from Terminal).

## 5. Start it

- **⌘ Space → MastGaane**, or **Finder → ~/Applications → MastGaane**. A music-note icon appears in
  the menu bar with play / pause, mute, next, like, stop and quit. MastGaane FM opens in your browser.
- **Start at login:** System Settings → General → Login Items → **+** → `~/Applications/MastGaane.app`.
- **From Terminal:** `cd ~/MastGaane && uv run mastgaane serve`. Every other command in
  [README.md](README.md) also works on the Mac with `uv run mastgaane …`.
- The server's output goes to `~/MastGaane/logs/serve.log`.

## 6. Keep the Mac up to date

When the PC finds new songs, publish the catalogue from the PC:

```powershell
uv run mastgaane export-songs     # rewrites library/songs.jsonl.gz
git add library/songs.jsonl.gz; git commit -m "catalogue: new songs"; git push
```

Then on the Mac:

```bash
cd ~/MastGaane && git pull && bash installer/install_mac.sh --no-llm
```

`import-songs` only adds songs the Mac doesn't have. It never changes a song you already approved
or rejected on the Mac.

## What works differently on a Mac

- **No Dexter.** Dexter, the voice assistant that passes "Bharti, …" commands on, runs only on
  Windows. On the Mac, use the menu-bar icon or the web UI. To use voice commands, turn on
  MastGaane's own mic: set `VOICE_CONTROL=true` in `.env`, or use **Own mic too** in the menu.
- **Volume, mute and the dip during voice commands.** macOS has no per-app volume, so MastGaane
  restarts the song where it was at the new level. You hear a short blip. Once the music is at
  100%, "louder" raises the Mac's own volume.
- **🎤 Listen → "this PC's sound"** (recording what the computer itself plays) needs an add-on such
  as BlackHole on a Mac. The mic and phone-recording / file sources work normally.
- **The nightly Windows task** (Instagram scroll at 00:30) isn't set up on the Mac. Run
  `uv run mastgaane run-all` yourself when you want a harvest.
- **The 12B model** (`gemma-4-12b`) was imported by hand on the PC, so the installer uses
  `gemma4:e4b-it-qat` for `LLM_MODEL` and `LIBRARY_LLM_MODEL` instead.

## Troubleshooting

| Problem | Fix |
|---|---|
| `uv sync` fails while building `shazamio-core` | `cargo --version` should print a version. If it doesn't: `brew install rust`, open a new Terminal, run the installer again |
| No sound | `which ffplay` must print a path (`brew install ffmpeg`). Then look in `logs/serve.log` |
| The app does nothing when opened | Run `~/Applications/MastGaane.app/Contents/MacOS/MastGaane` in Terminal to see the error, or read `logs/serve.log` |
| You moved the `MastGaane` folder | Run `bash installer/install_mac.sh` again. The app points to the folder it was installed from |
| "no microphone" in Listen | Allow the mic (step 4). If `LISTEN_MIC` in `.env` names a mic that isn't plugged in, clear it |
| Port 8787 busy | MastGaane moves to the next free port automatically. The URL is in `logs/serve.log` |
