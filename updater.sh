#!/bin/bash
# Checks GitHub for a new server.py every time it runs (called by cron).
# If updated, replaces the local file and restarts the screen session.
# Attach to the dashboard at any time with: screen -r thermal

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/server.py"
REMOTE_URL="https://raw.githubusercontent.com/winsinor/temp/main/server.py"
SESSION="thermal"
LOG="$SCRIPT_DIR/updater.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Fetch remote content (bail out silently on network error -- don't kill a running session)
REMOTE_CONTENT=$(curl -fsSL --max-time 10 "$REMOTE_URL" 2>/dev/null)
if [ -z "$REMOTE_CONTENT" ]; then
    log "ERROR: Could not fetch remote script -- keeping current version"
    exit 1
fi

REMOTE_HASH=$(echo "$REMOTE_CONTENT" | sha256sum | cut -d' ' -f1)
LOCAL_HASH=$(sha256sum "$SCRIPT_PATH" 2>/dev/null | cut -d' ' -f1)

UPDATED=false
if [ "$REMOTE_HASH" != "$LOCAL_HASH" ]; then
    echo "$REMOTE_CONTENT" > "$SCRIPT_PATH"
    log "Updated server.py ($LOCAL_HASH -> $REMOTE_HASH)"
    UPDATED=true
fi

# Check if the screen session is alive
SESSION_RUNNING=false
if screen -list 2>/dev/null | grep -q "\.${SESSION}[[:space:]]"; then
    SESSION_RUNNING=true
fi

if [ "$SESSION_RUNNING" = true ] && [ "$UPDATED" = false ]; then
    exit 0
fi

if [ "$SESSION_RUNNING" = true ]; then
    log "Stopping existing session for restart"
    screen -S "$SESSION" -X quit
    sleep 2
fi

log "Starting screen session '$SESSION'"
screen -dmS "$SESSION" python3 "$SCRIPT_PATH"
log "Session started (attach with: screen -r $SESSION)"
