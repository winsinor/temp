# System Metrics Dashboard - Codebase Guide

## Project Overview

A lightweight ASCII system metrics dashboard that displays real-time CPU, GPU, and RAM usage with Braille sparkline graphs. Designed for minimal terminal widths (40+ characters) and works on phones via SSH (Terminus) up to large desktop displays.

**Key Features:**
- Real-time metrics with 1-second updates
- Braille sparklines (16 levels) for better resolution
- Individual GPU tracking (GPU 0, GPU 1, etc.)
- Temperature display (CPU and GPU)
- 10-minute rolling history
- Auto-update from GitHub
- Adaptive to terminal width
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

## File Structure

```
.
├── dashboard.py              # Main dashboard application
├── updater.py                # Auto-update script
├── run.sh                     # Wrapper with auto-update
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
cpu_data = deque(maxlen=HISTORY_SIZE)  # maxlen=600
```

Deques are used for O(1) append/pop with automatic old-data removal. No manual cleanup needed.

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

**Test on narrow terminal:**
```bash
export COLUMNS=40
python3 dashboard.py
```

**Test with CPU load:**
```bash
python3 -c "import time; [i**2 for i in range(1000000) for _ in range(100)]" &
python3 dashboard.py
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

## Future Enhancements

- [ ] HyperPixel 4 display support (480x800 @ 60Hz)
- [ ] Network I/O monitoring (bytes in/out)
- [ ] Disk I/O monitoring (read/write throughput)
- [ ] Process monitoring (top N processes)
- [ ] Persistent history logging to file
- [ ] Custom color themes
- [ ] Alert thresholds (highlight high usage)
- [ ] Multi-user support (per-user data)
- [ ] Configuration file (settings, update frequency, etc.)

## Common Issues

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

## Performance Characteristics

- **CPU**: ~1-5% on typical systems (updating once per second)
- **Memory**: ~10-20 MB baseline
- **Network**: ~0 KB at rest, minimal during auto-update
- **Startup time**: <1 second (cold), <0.5 seconds (cached)

## Deployment

**Single machine:**
```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/run.sh
chmod +x run.sh
./run.sh
```

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
