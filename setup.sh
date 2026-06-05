#!/bin/bash
# One-time setup: download all files, install Python deps, register cron, start dashboard.
# Run once on the Pi:
#   curl -sO https://raw.githubusercontent.com/winsinor/temp/main/setup.sh && bash setup.sh

set -e

BASE_URL="https://raw.githubusercontent.com/winsinor/temp/main"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$SCRIPT_DIR/updater.sh"

echo "=== Pi Thermal Dashboard Setup ==="

# Download all required files
echo "Downloading files..."
curl -fsSL "$BASE_URL/server.py" -o "$SCRIPT_DIR/server.py"
curl -fsSL "$BASE_URL/updater.sh" -o "$UPDATER"
chmod +x "$UPDATER"
echo "Done."

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install --quiet --break-system-packages plotext pyfiglet sshkeyboard

# Install cron job (once per minute) if not already present
CRON_LINE="* * * * * $UPDATER >> $SCRIPT_DIR/updater.log 2>&1"
if crontab -l 2>/dev/null | grep -qF "$UPDATER"; then
    echo "Cron job already installed -- skipping"
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Cron job installed (runs every minute)"
fi

# Run updater immediately to start the first session
echo "Starting dashboard..."
bash "$UPDATER"

echo ""
echo "Done. Attach to the live dashboard with:"
echo "  screen -r thermal"
echo ""
echo "Detach without stopping it: Ctrl+A then D"
