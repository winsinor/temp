#!/bin/bash
# Cache-proof updater: resolves the latest commit SHA via the GitHub API, then
# downloads server.py pinned to that SHA (immutable URLs are never served stale).
# Always restarts the screen session so you KNOW you're on the latest code.
# Attach to the dashboard at any time with: screen -r thermal

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/server.py"
OWNER="winsinor"
REPO="temp"
BRANCH="main"
SESSION="thermal"
LOG="$SCRIPT_DIR/updater.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# 1. Resolve the latest commit SHA on the branch (API responses are not CDN-cached stale)
SHA=$(curl -fsSL --max-time 10 \
    "https://api.github.com/repos/$OWNER/$REPO/commits/$BRANCH" 2>/dev/null \
    | grep -m1 '"sha"' | cut -d'"' -f4)

if [ -z "$SHA" ]; then
    log "ERROR: could not resolve latest SHA -- keeping current version"
    echo "Could not reach GitHub. Keeping current version."
    exit 1
fi

# 2. Download server.py pinned to that exact SHA (immutable, never stale)
RAW_URL="https://raw.githubusercontent.com/$OWNER/$REPO/$SHA/server.py"
if ! curl -fsSL --max-time 15 "$RAW_URL" -o "$SCRIPT_PATH.tmp"; then
    log "ERROR: download failed for $SHA -- keeping current version"
    echo "Download failed. Keeping current version."
    exit 1
fi
mv "$SCRIPT_PATH.tmp" "$SCRIPT_PATH"
log "Pulled server.py @ $SHA"
echo "Pulled server.py @ ${SHA:0:7}"

# 3. Always restart the session so the new code is definitely running
if screen -list 2>/dev/null | grep -q "\.${SESSION}[[:space:]]"; then
    screen -S "$SESSION" -X quit
    sleep 2
    log "Stopped existing session"
fi

screen -dmS "$SESSION" python3 "$SCRIPT_PATH"
log "Started session '$SESSION'"
echo "Restarted dashboard (attach with: screen -r $SESSION)"
