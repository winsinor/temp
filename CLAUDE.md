# System Metrics Dashboard - Codebase Guide

## Project Overview

Two system metrics dashboards for real-time monitoring:

1. **CLI Dashboard** - Lightweight ASCII display with Braille sparkline graphs
   - Designed for minimal terminal widths (40+ characters)
   - Works on phones via SSH (Terminus) up to large desktop displays

2. **Web UI** - Modern browser-based interface with interactive charts
   - Time range selector (30s to 24h)
   - Real-time Chart.js graphs
   - Dark theme with responsive layout
   - Runs in background with auto-update

**Shared Features:**
- Real-time metrics with 1-second collection
- Individual GPU tracking (GPU 0, GPU 1, etc.)
- Temperature display (CPU and GPU)
- 24-hour rolling history
- Auto-update from GitHub
- No external dependencies (uses stdlib + optional nvidia-smi)

## Architecture

### Core Components

**dashboard.py** - Main application
- `MetricsCollector` class: Gathers CPU, GPU, RAM, temperature metrics
- `braille_sparkline()`: Renders Braille sparkline graphs
- Data buffers: Deques for efficient rolling history (600 samples = 10 minutes)
- Display loop: Updates every 1 second with ANSI escape codes

**updater.py** - Auto-update mechanism
- SHA256 hash comparison for file change detection
- Non-blocking updates (fails silently if network unavailable)
- Downloads only changed files
- Atomic file replacement via temp file

**run.sh** - Wrapper script
- Checks for updates before launching dashboard
- Ensures dashboard.py and updater.py are present
- Makes files executable

**setup.sh** - Installation script
- One-time setup for fresh systems
- Downloads all necessary files
- Verifies Python 3 and optional NVIDIA drivers

**web.py** - Web UI application
- HTTP server using stdlib `http.server`
- Real-time metrics API (`/api/metrics`)
- HTML5 frontend with Chart.js graphs
- Reuses `MetricsCollector` from dashboard.py
- Background process with logging to `web.log`
- Runs on port 5000 by default

**web.sh** - Web UI wrapper script
- Checks for updates before launching web UI
- Spawns web.py in background
- Logs startup info (PID, URL, log file)

## File Structure

```
.
├── dashboard.py              # CLI dashboard application
├── web.py                     # Web UI application
├── updater.py                # Auto-update script
├── run.sh                     # CLI wrapper with auto-update
├── web.sh                     # Web UI wrapper with auto-update
├── setup.sh                   # Installation script
├── CLAUDE.md                  # This file
├── README.md                  # User-facing documentation
├── INSTALL.md                 # Installation & scheduling guide
├── .gitignore                 # Python artifacts
└── dashboard-hyperpixel.py    # Placeholder for HyperPixel 4 support
```

## How It Works

### Metrics Collection

**CPU Usage:**
- Reads `/proc/stat` twice with 1-second interval
- Calculates utilization percentage from idle/total deltas
- Returns 0-100%

**CPU Temperature:**
- Reads `/sys/class/thermal/thermal_zone0/temp` (in millidegrees)
- Gracefully returns None if unavailable
- Displays as text, not graphed

**GPU Metrics (NVIDIA):**
- Uses `nvidia-smi` to query individual GPUs
- Collects utilization % and temperature for each GPU
- Shows GPU 0, GPU 1, etc. separately
- Non-blocking queries with timeout

**RAM Usage:**
- Parses `/proc/meminfo` for MemTotal and MemAvailable
- Calculates percentage: (Total - Available) / Total * 100
- Returns 0-100%

### Web UI System

**Architecture:**
- `MetricsStore` class: Thread-safe collection of metrics
- Background thread: Runs `MetricsCollector.collect_loop()` every 1 second
- HTTP handler: Serves static HTML and JSON API
- API endpoint: `/api/metrics?seconds=<range>` returns data slice

**History Management:**
- Extended to 24-hour history (86,400 samples at 1-second intervals)
- Deques with `maxlen` for automatic FIFO overflow
- Memory efficient: ~2MB per metric type

**Frontend Features:**
- Time range selector: 30s, 1m, 5m, 30m, 1h, 4h, 24h
- Chart.js line graphs with smooth animations
- Real-time updates every 2 seconds
- Dark theme matching Claude design
- Responsive grid layout for multiple GPUs
- Average calculations per time range

**Background Process:**
- `spawn_background()`: Uses `subprocess.Popen` with `start_new_session`
- Detaches from terminal completely
- Redirects stdout/stderr to `web.log`
- Shows PID and URL on startup

### Display System

**Braille Sparklines:**
- 16-character set: ⠀⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏
- Maps normalized values (0-1) to 0-15 index
- Each character represents a vertical bar height
- Pads with empty Braille (⠀) if insufficient data

**Layout:**
- Header: "╭─ System Metrics Dashboard"
- CPU line: label, percentage, sparkline
- CPU temp: displayed below as text
- GPU lines: one per GPU (same format as CPU)
- RAM line: same format
- Footer: sample count and exit instruction

**Terminal Adaptation:**
- Minimum width: 40 characters
- Sparkline width calculated as: max(20, terminal_width - 26)
- Automatically scales with window resize

### Auto-Update Flow

1. `run.sh` executes on startup
2. Calls `updater.py` (silently, non-blocking)
3. Updater compares local file hashes with remote GitHub versions
4. Downloads only changed files if network available
5. Replaces files atomically (write to temp, then rename)
6. Falls back gracefully if network unavailable
7. Dashboard launches with latest code

## Key Implementation Details

### Data Buffers

```python
cpu_data = deque(maxlen=HISTORY_SIZE)  # maxlen=86400 (24 hours)
```

Deques are used for O(1) append/pop with automatic old-data removal. No manual cleanup needed.

**History Sizes:**
- CLI Dashboard: 600 samples (10 minutes at 1-second intervals)
- Web UI: 86,400 samples (24 hours at 1-second intervals)
- Memory per metric: ~2-3 MB (24-hour history)

### Non-Blocking GPU Detection

GPU queries have timeout and exception handling:
```python
subprocess.check_output(..., timeout=10, stderr=subprocess.DEVNULL)
```

Prevents dashboard hang if GPU driver is slow or unavailable.

### Atomic File Updates

Updater writes to temp file first, then moves:
```python
temp_path = filepath.with_suffix(filepath.suffix + ".tmp")
# Write to temp_path
temp_path.replace(filepath)  # Atomic move
```

Prevents corruption if process interrupted during download.

### ANSI Display Control

```python
sys.stdout.write("\033[?25l")  # Hide cursor
sys.stdout.write("\033[2J\033[H")  # Clear screen, home cursor
sys.stdout.write("\033[H\033[J")  # Home, clear to end
sys.stdout.write("\033[?25h")  # Show cursor
```

Smooth, flicker-free updates without external library.

## Development Notes

### Testing

**CLI Dashboard - Test on narrow terminal:**
```bash
export COLUMNS=40
python3 dashboard.py
```

**CLI Dashboard - Test with CPU load:**
```bash
python3 -c "import time; [i**2 for i in range(1000000) for _ in range(100)]" &
python3 dashboard.py
```

**Web UI - Test in foreground:**
```bash
python3 web.py --foreground
# Open browser to http://localhost:5000
```

**Web UI - Test background mode:**
```bash
./web.sh
tail -f web.log
pkill -f "python3 web.py"
```

**Test GPU detection:**
```bash
nvidia-smi --list-gpus
```

### Adding New Metrics

1. Add collection method to `MetricsCollector`
2. Create data deque in `main()`
3. Call collector method in main loop
4. Create sparkline via `braille_sparkline()`
5. Add display lines

Example:
```python
def _get_disk_usage(self):
    # Implementation
    return usage_percent

# In main():
disk_data = deque(maxlen=HISTORY_SIZE)
disk_data.append(metrics["disk"])
disk_spark = braille_sparkline(disk_data, 0, 100, sparkline_width)
print(format_metric_line("DISK", metrics["disk"], disk_spark))
```

### Debugging

Check temperature availability:
```bash
cat /sys/class/thermal/thermal_zone0/temp
```

Check GPU availability:
```bash
nvidia-smi --query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits
```

Check file hashes during update:
```bash
python3 updater.py  # Shows download/update messages on stderr
```

## Completed Features

- [x] Web UI with modern charts (Chart.js)
- [x] Time range selector (30s to 24h)
- [x] 24-hour history support
- [x] Background process for web UI
- [x] Dark theme UI
- [x] GPU support in both dashboards
- [x] Temperature monitoring
- [x] Device names (CPU model, GPU model) in UI
- [x] Current temperature display next to device names
- [x] Smart time axis labels (adaptive intervals based on range)
- [x] Disk I/O charting
- [x] Accurate time-range-specific averages

## Known Issues

- **Y-axis unit labels not rendering** (in progress)
  - Code is present for `%` units on CPU/RAM/GPU and `MB/s` on Disk I/O
  - Chart.js callback may not be evaluating properly
  - Workaround: Check browser console (F12) for errors
  - Solution: May need to verify Chart.js version compatibility or use different approach

## Future Enhancements

- [ ] HyperPixel 4 display support (480x800 @ 60Hz)
- [ ] Network I/O monitoring (bytes in/out)
- [ ] Process monitoring (top N processes)
- [ ] Persistent history logging to SQLite
- [ ] Custom color themes for web UI
- [ ] Alert thresholds (highlight high usage)
- [ ] Multi-user support (per-user data)
- [ ] Configuration file (settings, update frequency, etc.)
- [ ] Export metrics to CSV/JSON
- [ ] Grafana integration

## Common Issues

### Y-axis unit labels not showing on charts
**Status:** In progress - code is written but not rendering
- Files affected: `web.py` (HTML template JavaScript section)
- Expected: `%` on CPU/RAM/GPU charts, `MB/s` on Disk I/O
- Current: Y-axis ticks show numbers only without units
- Possible causes:
  - Chart.js callback not evaluating (check browser console F12)
  - Arrow function vs function syntax issue (recently changed to `function(v) {}`)
  - Config spread/merge not preserving options properly
- Solution: Debug chart rendering, may need different Chart.js approach

### Dashboard shows old version
Clear cache and re-download:
```bash
rm -rf ~/.local/share/dashboard
task-manager
```

### GPU metrics not showing
Verify NVIDIA drivers:
```bash
nvidia-smi --list-gpus
```

If no output, GPU detection will skip gracefully.

### Thermal sensor unavailable
Some systems (containers, VMs) lack thermal zone files. Dashboard continues without temperature.

### High CPU usage from dashboard
Normal with 1-second updates. Can increase `INTERVAL` in code if needed.

## Recent Changes (To Fix)

Last commits focused on adding:
1. **Device names** - CPU model from `/proc/cpuinfo`, GPU names from `nvidia-smi`
2. **Current temperatures** - Displayed next to device names in status line
3. **Smart time axis labels** - Intervals adapt to selected time range (30s to 24h)
4. **Y-axis unit labels** - Added callbacks for `%` and `MB/s` (NOT RENDERING)

All changes are on `main` branch. Work directly on `main` for auto-update.

**Files to check:**
- `web.py` - Lines 440-520 for chart configuration and y-axis callbacks
- `web.py` - Lines 395-425 for `getSmartLabels()` function

## Performance Characteristics

- **CPU**: ~1-5% on typical systems (updating once per second)
- **Memory**: ~10-20 MB baseline
- **Network**: ~0 KB at rest, minimal during auto-update
- **Startup time**: <1 second (cold), <0.5 seconds (cached)

## Deployment

**Setup (downloads all files):**
```bash
curl -sL https://raw.githubusercontent.com/winsinor/temp/main/setup.sh | bash
```

**CLI Dashboard:**
```bash
./run.sh
```

**Web UI (runs in background):**
```bash
./web.sh
# View logs: tail -f web.log
# Run in foreground: ./web.sh --foreground
```

**Web UI from browser:**
- Open http://localhost:5000
- Select time range: 30s to 24h
- View CPU, RAM, GPU metrics with averages

**Scheduled updates (cron):**
```bash
0 * * * * python3 /path/to/updater.py >/dev/null 2>&1
```

**SSH monitoring (Terminus):**
```bash
ssh user@host "cd ~/.local/share/dashboard && ./run.sh"
```

## Git Workflow

- Main branch: Stable, released code
- Feature branches: New features/improvements
- All changes tested before merge
- Atomic commits with clear messages
- Auto-update detects changes via hash comparison

## License

MIT
