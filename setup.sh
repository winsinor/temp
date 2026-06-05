#!/bin/bash
# One-time setup: install Python deps, register the cron job, start the dashboard.
# Run once on the Pi: bash setup.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$SCRIPT_DIR/updater.sh"

echo "=== Pi Thermal Dashboard Setup ==="

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install --quiet plotext pyfiglet sshkeyboard

# Make scripts executable
chmod +x "$UPDATER"

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
