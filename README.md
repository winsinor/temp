# System Metrics Dashboard

A lightweight ASCII dashboard that displays CPU, GPU, and RAM usage over time with sparkline graphs. Designed to work on minimal terminal widths (starting at 40 characters) and adapts to your screen size.

## Features

- **CPU Usage**: Real-time CPU usage percentage with history
- **GPU Usage**: NVIDIA GPU utilization (if available) with combined average across all GPUs
- **GPU Memory**: GPU VRAM usage percentage (if available)
- **RAM Usage**: System RAM usage percentage with history
- **Sparkline Visualization**: Unicode block sparklines showing usage trends over time
- **Adaptive Layout**: Automatically adapts to terminal width (minimum 40 characters)
- **Low Latency**: Updates every 1 second
- **10-Minute History**: Keeps rolling 10-minute history window of metrics

## Requirements

- Python 3.6+
- Linux with `/proc/stat` and `/proc/meminfo`
- (Optional) NVIDIA GPU with `nvidia-smi` for GPU monitoring

## Quick Start

### Run with Auto-Update (Recommended)

```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/run.sh && \
chmod +x run.sh && ./run.sh
```

Automatically checks for updates from GitHub on every run.

### Run Directly

```bash
python3 dashboard.py
```

### One-Shot Install & Run (on Raspberry Pi or Linux)

```bash
curl -sO https://raw.githubusercontent.com/winsinor/temp/main/dashboard.py && \
python3 dashboard.py
```

## Display Adaptation

The dashboard automatically adapts to your terminal width:

- **40 characters** (Terminus on phone): Compact sparklines, perfect for small screens
- **80 characters** (Standard terminal): Full sparklines with good detail
- **120+ characters**: Extended sparklines for longer history visualization

```
╭─ System Metrics
CPU Usage      20.8%  │             ▁
              ▁=0%   █=100%

GPU Usage      45.2%  │            ▄▅
              ▁=0%   █=100%

GPU RAM        62.1%  │           ▅▆
              ▁=0%   █=100%

RAM Usage       3.4%  │             
              ▁=0%   █=100%

╰─ Collected: 120 samples
Ctrl+C to exit
```

## Auto-Update

The dashboard automatically checks for updates from GitHub and upgrades if a newer version is available.

**With auto-update enabled (recommended):**
```bash
./run.sh
```

**Check for updates manually:**
```bash
python3 updater.py --check
```

**Force update:**
```bash
python3 updater.py
```

For scheduled automatic updates (cron/systemd), see [INSTALL.md](INSTALL.md).

## Keyboard Shortcuts

- **Ctrl+C**: Exit the dashboard

## Architecture Notes

- Uses deque buffers for efficient rolling history
- Non-blocking metric collection (no hanging on GPU queries)
- Graceful degradation if GPU/metrics unavailable
- CPU usage calculated from `/proc/stat` deltas
- GPU metrics from `nvidia-smi` (fallback if unavailable)

## Future Enhancements

- [ ] HyperPixel 4 display support (480x800 @ 60Hz)
- [ ] Network I/O monitoring
- [ ] Disk I/O monitoring
- [ ] Persistent history logging
- [ ] Custom color themes

## Troubleshooting

### GPU metrics not showing
- Ensure NVIDIA drivers are installed
- Run `nvidia-smi --list-gpus` to verify GPU detection
- Dashboard will gracefully skip GPU metrics if unavailable

### Narrow display looks cut off
- The dashboard will adapt to your terminal width
- Minimum supported width is 40 characters
- Try resizing your terminal or adjusting zoom level

### High CPU usage from dashboard
- This is normal during development
- CPU usage spike is from continuous metric polling
- Consider increasing INTERVAL in dashboard.py if needed

## License

MIT
