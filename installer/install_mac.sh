#!/bin/bash
# MastGaane on a Mac, in one go (INSTALL-MAC.md explains every step):
#   Homebrew tools (ffmpeg, uv, Rust), Google Chrome, optionally Ollama + the local LLM, the Python
#   environment, your data from the Windows PC (the zip from installer/pack_for_mac.ps1), the
#   39,000-song catalogue from git, and MastGaane.app in ~/Applications.
#
#   ./installer/install_mac.sh [~/Downloads/MastGaane-data.zip] [--llm | --no-llm]
#
# Safe to run again: it adds what is missing, rebuilds the app and adds catalogue songs it lacks.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
PACK=""
LLM="ask"
FROM_WINDOWS=0
MODEL="gemma4:e4b-it-qat"

for arg in "$@"; do
  case "$arg" in
    --llm) LLM="yes" ;;
    --no-llm) LLM="no" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) PACK="$arg" ;;
  esac
done

step() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mXX %s\033[0m\n' "$*" >&2; exit 1; }
need() {  # need <command> <brew formula>: install the formula unless the command is already there
  if command -v "$1" >/dev/null 2>&1; then
    note "$1: $(command -v "$1")"
  else
    brew install "$2"
  fi
}

[ "$(uname -s)" = "Darwin" ] || die "This script is for macOS. On Windows run installer/MastGaane Setup.exe."

# ----------------------------------------------------------------------------- 1. Homebrew
step "Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
  done
fi
command -v brew >/dev/null 2>&1 || die "Homebrew is missing. Install it first (https://brew.sh):
    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
then run this script again."
note "$(brew --version | head -1)"

# ----------------------------------------------------------------------------- 2. tools
step "Tools: ffmpeg (plays and records), uv (Python), Rust (builds the Shazam part once)"
need ffplay ffmpeg
need uv uv
need cargo rust
command -v ffplay >/dev/null 2>&1 || die "ffplay is still missing after 'brew install ffmpeg'"

step "Google Chrome (scrolls your Instagram Reels)"
if [ -d "/Applications/Google Chrome.app" ]; then
  note "already installed"
elif [ -d "$HOME/Applications/Google Chrome.app" ]; then
  note "in ~/Applications"
else
  brew install --cask google-chrome
fi

# ----------------------------------------------------------------------------- 3. your data
step "Your data from the Windows PC"
if [ -n "$PACK" ]; then
  [ -f "$PACK" ] || die "$PACK not found"
  mkdir -p data
  stamp="$(date +%Y%m%d-%H%M%S)"
  if [ -f data/mastgaane.db ]; then
    cp data/mastgaane.db "data/mastgaane.before-pack-$stamp.db"
    note "kept the Mac's old database as data/mastgaane.before-pack-$stamp.db"
  fi
  if [ -f .env ]; then cp .env ".env.before-pack-$stamp"; fi
  note "unpacking $PACK (data/, .env, browser.json) ..."
  ditto -x -k "$PACK" "$ROOT"
  FROM_WINDOWS=1
  note "done: $(du -sh data | cut -f1) in data/"
else
  note "no zip given: the songs come from the catalogue in git (step 6)."
  note "To bring everything (likes, taste, downloaded songs, keys), run pack_for_mac.ps1 on the PC"
  note "and run this again with the zip:  ./installer/install_mac.sh ~/Downloads/MastGaane-data.zip"
fi

# ----------------------------------------------------------------------------- 4. settings
step "Settings (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  note "new .env from .env.example: add your keys there (INSTALL-MAC.md, step 4)"
fi
sed -i '' -e $'s/\r$//' .env  # Windows line endings
# Windows-only values: the PC's Chrome path (blank = /Applications/Google Chrome.app)
sed -i '' -E 's#^CHROME_PATH=.*\.exe"?[[:space:]]*$#CHROME_PATH=#' .env
if [ -d "$HOME/Applications/Google Chrome.app" ] && [ ! -d "/Applications/Google Chrome.app" ]; then
  sed -i '' -E "s#^CHROME_PATH=\$#CHROME_PATH=\"$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\"#" .env
fi
if [ "$FROM_WINDOWS" = 1 ]; then
  # the PC's microphone names (Blue Yeti, Brio 100): blank = every real mic on this Mac
  sed -i '' -E 's#^(LISTEN_MIC|VOICE_MIC)=.*#\1=#' .env
fi
note "ok"

# ----------------------------------------------------------------------------- 5. Python
step "Python environment (uv sync; the first run builds the Shazam part with Rust: a few minutes)"
uv sync
note "ok: $(.venv/bin/python --version)"

# ----------------------------------------------------------------------------- 6. songs
step "Songs: adding the catalogue from git (library/songs.jsonl.gz)"
.venv/bin/mastgaane import-songs

# ----------------------------------------------------------------------------- 7. local LLM
step "Local LLM (optional): Ollama + $MODEL, about 6 GB"
if [ "$LLM" = ask ]; then
  if [ -t 0 ]; then
    read -r -p "    Install it? It writes the host's links and sorts new songs; without it MastGaane still plays. [y/N] " answer
    case "$answer" in [yY]*) LLM=yes ;; *) LLM=no ;; esac
  else
    LLM=no
  fi
fi
if [ "$LLM" = yes ]; then
  need ollama ollama
  if ! ollama list >/dev/null 2>&1; then
    brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
    for _ in 1 2 3 4 5 6 7 8 9 10; do ollama list >/dev/null 2>&1 && break; sleep 1; done
  fi
  ollama pull "$MODEL"
else
  note "skipped (run again with --llm to add it)"
fi
# the PC's 12B model was imported by hand and is not on this Mac: use the e4b one instead
if ! (command -v ollama >/dev/null 2>&1 && ollama list 2>/dev/null | grep -q '^gemma-4-12b'); then
  sed -i '' -E "s#^(LLM_MODEL|LIBRARY_LLM_MODEL)=gemma-4-12b:latest[[:space:]]*\$#\\1=$MODEL#" .env
fi

# ----------------------------------------------------------------------------- 8. the app
step "MastGaane.app in ~/Applications"
APP="$HOME/Applications/MastGaane.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$ROOT/logs"
cp assets/MastGaane.icns "$APP/Contents/Resources/MastGaane.icns"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MastGaane</string>
  <key>CFBundleDisplayName</key><string>MastGaane</string>
  <key>CFBundleIdentifier</key><string>local.mastgaane.app</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>MastGaane</string>
  <key>CFBundleIconFile</key><string>MastGaane</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <!-- a menu-bar app: no Dock icon -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>MastGaane listens to name a song playing near you (Listen) and for your voice commands.</string>
</dict>
</plist>
PLIST
cat > "$APP/Contents/MacOS/MastGaane" <<LAUNCHER
#!/bin/bash
# MastGaane.app: the web UI and the menu-bar icon (mastgaane serve); opens MastGaane FM in the browser.
# Written by installer/install_mac.sh; run that again after moving the MastGaane folder.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"  # ffmpeg and ollama (an app from Finder gets a bare PATH)
cd "$ROOT" || exit 1
LOG="$ROOT/logs/serve.log"
[ -f "\$LOG" ] && mv -f "\$LOG" "\$LOG.1"
( for _ in \$(seq 1 90); do
    url=\$(grep -Eo 'http://127\.0\.0\.1:[0-9]+/' "\$LOG" 2>/dev/null | head -1)
    if [ -n "\$url" ]; then open "\${url}fm"; break; fi
    sleep 1
  done ) &
exec "$ROOT/.venv/bin/mastgaane" serve >>"\$LOG" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/MastGaane"
# an ad-hoc signature, so macOS remembers the Microphone permission it gives the app
codesign --force --sign - "$APP" >/dev/null 2>&1 || note "(could not sign the app: Listen may ask for the mic again)"
touch "$APP"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true
note "$APP"

# ----------------------------------------------------------------------------- done
step "Done"
note "Start it: Spotlight (Cmd+Space) > MastGaane, or Finder > ~/Applications > MastGaane."
note "It sits in the menu bar (the music-note icon: play / pause / next / quit) and opens"
note "MastGaane FM in your browser. From a terminal:  cd \"$ROOT\" && uv run mastgaane serve"
note "Once-only logins (YouTube Music, Instagram) and the keys: INSTALL-MAC.md, step 4."
