# Installation & Auto-Update Guide

## Quick Start

### Option 1: Direct Run (Recommended)
```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/run.sh && \
chmod +x run.sh && \
./run.sh
```

This automatically:
- Checks for updates from GitHub
- Updates files if newer versions available
- Runs the dashboard

### Option 2: Download & Run
```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/dashboard.py && \
python3 dashboard.py
```

### Option 3: Use Setup Script
```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/setup.sh && \
bash setup.sh
```

## Auto-Update Configuration

### Automatic Updates on Every Run
The `run.sh` wrapper checks for updates every time you start the dashboard:
```bash
./run.sh
```

### Manual Update Check
```bash
python3 updater.py              # Check and update if needed
python3 updater.py --check      # Only check for updates
```

### Scheduled Updates (Cron)
Update the dashboard automatically every hour:

```bash
# Edit crontab
crontab -e

# Add this line to run updater every hour at minute 0
0 * * * * /path/to/updater.py >/dev/null 2>&1
```

### Scheduled Updates (systemd)
For Raspberry Pi and modern Linux systems:

Create `/etc/systemd/system/dashboard-update.timer`:
```ini
[Unit]
Description=Dashboard Auto-Update
After=network-online.target

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

Create `/etc/systemd/system/dashboard-update.service`:
```ini
[Unit]
Description=Dashboard Auto-Update Service
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /path/to/updater.py
User=pi
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard-update.timer
sudo systemctl start dashboard-update.timer
sudo systemctl status dashboard-update.timer
```

## How Auto-Update Works

The updater uses **file hash comparison** to detect changes:

1. Compares SHA256 hash of local files with remote files
2. Downloads only files that have changed
3. Works reliably without external version tracking
4. Silent operation - won't interrupt monitoring

## Update Behavior

- **No Network**: Updates fail silently, dashboard continues running
- **Network Error**: Updates are skipped, tries again next check
- **New Version**: Files are downloaded and replaced atomically
- **Same Version**: No action taken (zero overhead)

## Manual Update Recovery

If an update fails, you can manually download the latest:

```bash
# Re-run updater
python3 updater.py

# Or manually download
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/dashboard.py
```

## Troubleshooting

### Updates not working
```bash
# Check connectivity
curl -I https://raw.githubusercontent.com/winsinor/temp/main/dashboard.py

# Run updater with output
python3 updater.py
```

### Cron updates not running
```bash
# Check cron logs
sudo journalctl -u cron

# Verify cron entry
crontab -l

# Test cron execution
/usr/bin/python3 /path/to/updater.py
```

### systemd timer not working
```bash
# Check timer status
sudo systemctl status dashboard-update.timer
sudo systemctl list-timers dashboard-update.timer

# Check service logs
sudo journalctl -u dashboard-update.service -n 50
```

## Disable Auto-Updates

If you prefer manual updates, use `dashboard.py` directly instead of `run.sh`:
```bash
python3 dashboard.py
```

Or remove the updater call from `run.sh`.
