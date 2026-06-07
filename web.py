#!/usr/bin/env python3
"""Web UI for system metrics dashboard with time range selection."""

import json
import time
import threading
import sys
import os
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import subprocess
import shutil
import re

INTERVAL = 1.0
HISTORY_SIZE = 86400
DISK_RE = re.compile(r'^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+|vd[a-z]+|xvd[a-z]+|hd[a-z])$')


class MetricsCollector:
    def __init__(self):
        self.prev_idle, self.prev_total = self._read_cpu_stat()
        self.has_nvidia = shutil.which("nvidia-smi") is not None
        self.gpu_count = 0
        if self.has_nvidia:
            self._detect_gpu_count()
        self._prev_disk = {}
        self._prev_disk_t = time.monotonic()
        self.cpu_model = self._get_cpu_model()
        self.gpu_models = self._get_gpu_models()

    def _read_cpu_stat(self):
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            vals = [int(x) for x in parts[1:]]
            return vals[3], sum(vals)
        except (FileNotFoundError, ValueError):
            return 0, 0

    def _get_cpu_usage(self):
        idle, total = self._read_cpu_stat()
        di, dt = idle - self.prev_idle, total - self.prev_total
        self.prev_idle, self.prev_total = idle, total
        return 100.0 * (1.0 - di / dt) if dt > 0 else 0.0

    def _get_cpu_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return float(f.read()) / 1000.0
        except (FileNotFoundError, ValueError):
            return None

    def _detect_gpu_count(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--list-gpus"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            self.gpu_count = len(out.split('\n')) if out else 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            self.gpu_count = 0

    def _get_cpu_model(self):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except (FileNotFoundError, ValueError):
            pass
        return "CPU"

    def _get_gpu_models(self):
        if not self.has_nvidia or self.gpu_count == 0:
            return []
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            return out.split('\n') if out else []
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def _get_gpu_stats(self):
        if not self.has_nvidia or self.gpu_count == 0:
            return [], [], []
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=5, text=True,
            ).strip()
            usage, temps, mem = [], [], []
            for line in out.split('\n'):
                p = [x.strip() for x in line.split(',')]
                if len(p) >= 4:
                    usage.append(float(p[0]))
                    temps.append(float(p[1]))
                    mu, mt = float(p[2]), float(p[3])
                    mem.append(100.0 * mu / mt if mt > 0 else 0.0)
            return usage, temps, mem
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, FileNotFoundError):
            return [], [], []

    def _get_ram(self):
        try:
            with open("/proc/meminfo") as f:
                mi = {}
                for line in f:
                    k, v = line.split(':')
                    mi[k.strip()] = int(v.split()[0])
            total_kb = mi.get("MemTotal", 1)
            avail_kb = mi.get("MemAvailable", 0)
            used_kb = total_kb - avail_kb
            return (100.0 * used_kb / total_kb,
                    total_kb / 1048576,
                    used_kb / 1048576)
        except (FileNotFoundError, ValueError, KeyError):
            return 0.0, 0.0, 0.0

    def _get_disk_io(self):
        now = time.monotonic()
        dt = now - self._prev_disk_t
        try:
            with open("/proc/diskstats") as f:
                curr = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and DISK_RE.match(parts[2]):
                        curr[parts[2]] = (int(parts[5]), int(parts[9]))

            r_mb = w_mb = 0.0
            if self._prev_disk and dt > 0:
                for name, (sr, sw) in curr.items():
                    if name in self._prev_disk:
                        pr, pw = self._prev_disk[name]
                        r_mb += (sr - pr) * 512 / (dt * 1048576)
                        w_mb += (sw - pw) * 512 / (dt * 1048576)

            self._prev_disk = curr
            self._prev_disk_t = now
            return max(0.0, r_mb), max(0.0, w_mb)
        except (FileNotFoundError, ValueError, OSError):
            self._prev_disk_t = now
            return 0.0, 0.0

    def collect(self):
        gpu_usage, gpu_temp, gpu_mem = self._get_gpu_stats()
        ram_pct, ram_gb, ram_used_gb = self._get_ram()
        disk_r, disk_w = self._get_disk_io()
        return {
            "cpu": self._get_cpu_usage(),
            "cpu_temp": self._get_cpu_temp(),
            "gpu_usage": gpu_usage,
            "gpu_temp": gpu_temp,
            "gpu_mem": gpu_mem,
            "ram": ram_pct,
            "ram_gb": ram_gb,
            "ram_used": ram_used_gb,
            "disk_r": disk_r,
            "disk_w": disk_w,
        }


class MetricsStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.collector = MetricsCollector()
        self.history = {
            "cpu": deque(maxlen=HISTORY_SIZE),
            "cpu_temp": deque(maxlen=HISTORY_SIZE),
            "ram": deque(maxlen=HISTORY_SIZE),
            "ram_gb": deque(maxlen=HISTORY_SIZE),
            "gpu": [deque(maxlen=HISTORY_SIZE) for _ in range(4)],
            "gpu_temp": [deque(maxlen=HISTORY_SIZE) for _ in range(4)],
            "gpu_mem": [deque(maxlen=HISTORY_SIZE) for _ in range(4)],
            "disk_r": deque(maxlen=HISTORY_SIZE),
            "disk_w": deque(maxlen=HISTORY_SIZE),
            "timestamps": deque(maxlen=HISTORY_SIZE),
        }

    def collect_loop(self):
        while True:
            try:
                metrics = self.collector.collect()
                with self.lock:
                    self.history["cpu"].append(metrics["cpu"])
                    self.history["cpu_temp"].append(metrics["cpu_temp"] or 0)
                    self.history["ram"].append(metrics["ram"])
                    self.history["ram_gb"].append(metrics["ram_gb"])
                    for i, u in enumerate(metrics["gpu_usage"]):
                        if i < 4:
                            self.history["gpu"][i].append(u)
                    for i, t in enumerate(metrics["gpu_temp"]):
                        if i < 4:
                            self.history["gpu_temp"][i].append(t)
                    for i, m in enumerate(metrics["gpu_mem"]):
                        if i < 4:
                            self.history["gpu_mem"][i].append(m)
                    self.history["disk_r"].append(metrics["disk_r"])
                    self.history["disk_w"].append(metrics["disk_w"])
                    self.history["timestamps"].append(time.time())
                time.sleep(INTERVAL)
            except Exception as e:
                print(f"Error in collection loop: {e}")
                time.sleep(INTERVAL)

    def get_data(self, seconds=300):
        """Get metrics data for the last N seconds."""
        with self.lock:
            num_samples = min(int(seconds), len(self.history["timestamps"]))
            if num_samples == 0:
                return None

            def get_slice(deq):
                if len(deq) >= num_samples:
                    return list(deq)[-num_samples:]
                return list(deq)

            timestamps = get_slice(self.history["timestamps"])
            relative_times = [(t - timestamps[0]) for t in timestamps] if timestamps else []

            gpu_count = self.collector.gpu_count
            gpu_data = [{
                "usage": get_slice(self.history["gpu"][i]),
                "temp": get_slice(self.history["gpu_temp"][i]),
                "mem": get_slice(self.history["gpu_mem"][i]),
            } for i in range(min(gpu_count, 4))]

            current_cpu_temp = self.history["cpu_temp"][-1] if self.history["cpu_temp"] else 0
            current_gpu_temps = [self.history["gpu_temp"][i][-1] if self.history["gpu_temp"][i] else 0
                                 for i in range(min(gpu_count, 4))]

            return {
                "times": relative_times,
                "cpu": get_slice(self.history["cpu"]),
                "cpu_temp": get_slice(self.history["cpu_temp"]),
                "cpu_model": self.collector.cpu_model,
                "cpu_current_temp": current_cpu_temp,
                "ram": get_slice(self.history["ram"]),
                "ram_gb": get_slice(self.history["ram_gb"]),
                "gpu": gpu_data,
                "gpu_models": self.collector.gpu_models[:gpu_count],
                "gpu_current_temps": current_gpu_temps,
                "disk_r": get_slice(self.history["disk_r"]),
                "disk_w": get_slice(self.history["disk_w"]),
                "gpu_count": gpu_count,
            }


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>System Metrics</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            font-size: 24px;
            margin-bottom: 20px;
            color: #fff;
        }
        .controls {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        button {
            padding: 8px 16px;
            background: #2a2a2a;
            border: 1px solid #404040;
            color: #e0e0e0;
            cursor: pointer;
            border-radius: 4px;
            font-size: 14px;
            transition: all 0.2s;
        }
        button:hover {
            background: #3a3a3a;
            border-color: #505050;
        }
        button.active {
            background: #e87722;
            border-color: #e87722;
            color: #fff;
        }
        .charts {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
        }
        .chart-container {
            background: #1a1a1a;
            border: 1px solid #2a2a2a;
            border-radius: 8px;
            padding: 15px;
            position: relative;
            min-height: 350px;
        }
        .chart-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 10px;
            color: #b0b0b0;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        canvas { max-height: 300px; }
        .status {
            text-align: center;
            color: #707070;
            margin-top: 10px;
            font-size: 12px;
            line-height: 1.6;
            word-break: break-word;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>◆ System Metrics</h1>
        <div class="controls">
            <button class="time-btn" data-seconds="30">30 sec</button>
            <button class="time-btn" data-seconds="60">1 min</button>
            <button class="time-btn active" data-seconds="300">5 min</button>
            <button class="time-btn" data-seconds="1800">30 min</button>
            <button class="time-btn" data-seconds="3600">1 hour</button>
            <button class="time-btn" data-seconds="14400">4 hours</button>
            <button class="time-btn" data-seconds="86400">24 hours</button>
        </div>
        <div class="charts">
            <div class="chart-container">
                <div class="chart-title">CPU Usage</div>
                <canvas id="cpuChart"></canvas>
                <div class="status" id="cpuStatus"></div>
            </div>
            <div class="chart-container">
                <div class="chart-title">Memory Usage</div>
                <canvas id="ramChart"></canvas>
                <div class="status" id="ramStatus"></div>
            </div>
            <div id="gpuCharts"></div>
            <div class="chart-container">
                <div class="chart-title">Disk I/O</div>
                <canvas id="diskChart"></canvas>
                <div class="status" id="diskStatus"></div>
            </div>
        </div>
    </div>

    <script>
        const charts = {};
        let currentRange = 300;

        const chartConfig = {
            type: 'line',
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: {
                        display: false,
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        grid: { color: '#2a2a2a' },
                        ticks: { color: '#707070' }
                    },
                    x: {
                        grid: { color: '#2a2a2a' },
                        ticks: { color: '#707070' }
                    }
                }
            }
        };

        function getSmartLabels(times, seconds) {
            if (times.length === 0) return [];
            const labels = new Array(times.length).fill('');

            let interval = 1;
            if (seconds <= 30) interval = 5;
            else if (seconds <= 60) interval = 10;
            else if (seconds <= 300) interval = 30;
            else if (seconds <= 1800) interval = 300;
            else if (seconds <= 3600) interval = 600;
            else if (seconds <= 14400) interval = 1800;
            else interval = 7200;

            const lastTime = times[times.length - 1];
            times.forEach((t, i) => {
                if (Math.abs(t - Math.round(t / interval) * interval) < 0.5) {
                    const min = Math.floor(t / 60);
                    const sec = Math.floor(t % 60);
                    labels[i] = min > 0 ? `${min}m${sec}s` : `${sec}s`;
                }
            });
            if (labels[labels.length - 1] === '') {
                const t = lastTime;
                const min = Math.floor(t / 60);
                const sec = Math.floor(t % 60);
                labels[labels.length - 1] = min > 0 ? `${min}m${sec}s` : `${sec}s`;
            }
            return labels;
        }

        async function loadData(seconds, isRangeChange = false) {
            currentRange = seconds;
            try {
                const response = await fetch(`/api/metrics?seconds=${seconds}`);
                const data = await response.json();
                if (!data || data.error) return;

                const labels = getSmartLabels(data.times, seconds);

                // CPU chart
                if (!charts.cpu || isRangeChange) {
                    if (charts.cpu) charts.cpu.destroy();
                    const cpuConfig = JSON.parse(JSON.stringify(chartConfig));
                    cpuConfig.options.scales.y.ticks.callback = v => v + '%';
                    charts.cpu = new Chart(document.getElementById('cpuChart'), {
                        ...cpuConfig,
                        data: {
                            labels,
                            datasets: [{
                                label: 'CPU',
                                data: data.cpu,
                                borderColor: '#e87722',
                                backgroundColor: 'rgba(232, 119, 34, 0.1)',
                                tension: 0.4,
                                fill: true
                            }]
                        }
                    });
                } else {
                    charts.cpu.data.labels = labels;
                    charts.cpu.data.datasets[0].data = data.cpu;
                    charts.cpu.update('none');
                }
                const cpuAvg = (data.cpu.reduce((a, b) => a + b, 0) / data.cpu.length).toFixed(1);
                const cpuTempStr = data.cpu_current_temp > 0 ? ` • ${data.cpu_current_temp.toFixed(0)}°C` : '';
                document.getElementById('cpuStatus').textContent = `${data.cpu_model}${cpuTempStr} • Avg: ${cpuAvg}%`;

                // RAM chart
                if (!charts.ram || isRangeChange) {
                    if (charts.ram) charts.ram.destroy();
                    const ramConfig = JSON.parse(JSON.stringify(chartConfig));
                    ramConfig.options.scales.y.ticks.callback = v => v + '%';
                    charts.ram = new Chart(document.getElementById('ramChart'), {
                        ...ramConfig,
                        data: {
                            labels,
                            datasets: [{
                                label: 'RAM',
                                data: data.ram,
                                borderColor: '#8c50f5',
                                backgroundColor: 'rgba(140, 80, 245, 0.1)',
                                tension: 0.4,
                                fill: true
                            }]
                        }
                    });
                } else {
                    charts.ram.data.labels = labels;
                    charts.ram.data.datasets[0].data = data.ram;
                    charts.ram.update('none');
                }
                const ramAvg = (data.ram.reduce((a, b) => a + b, 0) / data.ram.length).toFixed(1);
                document.getElementById('ramStatus').textContent = `Average: ${ramAvg}%`;

                // GPU charts
                if (isRangeChange) {
                    const gpuContainer = document.getElementById('gpuCharts');
                    gpuContainer.innerHTML = '';
                    Object.keys(charts).forEach(k => { if (k.startsWith('gpu')) { charts[k].destroy(); delete charts[k]; } });
                    if (charts.disk) { charts.disk.destroy(); delete charts.disk; }
                }

                if (data.gpu_count > 0) {
                    const gpuContainer = document.getElementById('gpuCharts');
                    for (let i = 0; i < data.gpu_count; i++) {
                        const gpu = data.gpu[i];
                        if (!document.getElementById(`gpuChart${i}`)) {
                            const container = document.createElement('div');
                            container.className = 'chart-container';
                            container.innerHTML = `
                                <div class="chart-title">GPU ${i} Usage</div>
                                <canvas id="gpuChart${i}"></canvas>
                                <div class="status" id="gpuStatus${i}"></div>
                            `;
                            gpuContainer.appendChild(container);
                        }
                        if (!charts[`gpu${i}`]) {
                            const gpuConfig = JSON.parse(JSON.stringify(chartConfig));
                            gpuConfig.options.scales.y.ticks.callback = v => v + '%';
                            charts[`gpu${i}`] = new Chart(document.getElementById(`gpuChart${i}`), {
                                ...gpuConfig,
                                data: {
                                    labels,
                                    datasets: [{
                                        label: `GPU ${i}`,
                                        data: gpu.usage,
                                        borderColor: '#ff9500',
                                        backgroundColor: 'rgba(255, 149, 0, 0.1)',
                                        tension: 0.4,
                                        fill: true
                                    }]
                                }
                            });
                        } else {
                            charts[`gpu${i}`].data.labels = labels;
                            charts[`gpu${i}`].data.datasets[0].data = gpu.usage;
                            charts[`gpu${i}`].update('none');
                        }
                        const gpuAvg = (gpu.usage.reduce((a, b) => a + b, 0) / gpu.usage.length).toFixed(1);
                        const gpuModel = data.gpu_models[i] || `GPU ${i}`;
                        const gpuTempStr = data.gpu_current_temps[i] > 0 ? ` • ${data.gpu_current_temps[i].toFixed(0)}°C` : '';
                        document.getElementById(`gpuStatus${i}`).textContent = `${gpuModel}${gpuTempStr} • Avg: ${gpuAvg}%`;
                    }
                }
                // Disk I/O chart
                const diskAvg = arr => arr.length ? (arr.reduce((a,b)=>a+b,0)/arr.length).toFixed(2) : '0.00';
                if (!charts.disk || isRangeChange) {
                    if (charts.disk) charts.disk.destroy();
                    charts.disk = new Chart(document.getElementById('diskChart'), {
                        type: 'line',
                        data: {
                            labels,
                            datasets: [
                                { label: 'Read MB/s', data: data.disk_r, borderColor: '#00d2ff', backgroundColor: 'rgba(0,210,255,0.1)', tension: 0.4, fill: true },
                                { label: 'Write MB/s', data: data.disk_w, borderColor: '#ff5f6d', backgroundColor: 'rgba(255,95,109,0.1)', tension: 0.4, fill: true }
                            ]
                        },
                        options: {
                            responsive: true, maintainAspectRatio: false, animation: false,
                            plugins: { legend: { display: true, labels: { color: '#b0b0b0' } } },
                            scales: {
                                y: { beginAtZero: true, grid: { color: '#2a2a2a' }, ticks: { color: '#707070', callback: v => v + ' MB/s' } },
                                x: { grid: { color: '#2a2a2a' }, ticks: { color: '#707070' } }
                            }
                        }
                    });
                } else {
                    charts.disk.data.labels = labels;
                    charts.disk.data.datasets[0].data = data.disk_r;
                    charts.disk.data.datasets[1].data = data.disk_w;
                    charts.disk.update('none');
                }
                document.getElementById('diskStatus').textContent = `R: ${diskAvg(data.disk_r)} MB/s  W: ${diskAvg(data.disk_w)} MB/s`;

            } catch (e) {
                console.error('Error loading data:', e);
            }
        }

        document.querySelectorAll('.time-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                loadData(parseInt(e.target.dataset.seconds), true);
            });
        });

        // Initial load and auto-refresh every 2 seconds
        loadData(currentRange, true);
        setInterval(() => loadData(currentRange), 2000);
    </script>
</body>
</html>
'''


# Global metrics store
metrics_store = MetricsStore()


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))

        elif path == '/api/metrics':
            seconds = int(query.get('seconds', ['300'])[0])
            data = metrics_store.get_data(seconds)
            if not data:
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "No data available"}).encode('utf-8'))
            else:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode('utf-8'))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def spawn_background(log_file):
    """Spawn the server in the background using subprocess."""
    script_path = os.path.abspath(__file__)
    with open(log_file, 'a') as log:
        proc = subprocess.Popen(
            [sys.executable, script_path, '--worker'],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
    print(f"Web UI running in background (PID: {proc.pid})")
    print(f"Web UI: http://localhost:5000")
    print(f"Logs: {log_file}")
    sys.exit(0)


def main():
    foreground = '--foreground' in sys.argv or '-f' in sys.argv
    is_worker = '--worker' in sys.argv

    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(script_dir, "web.log")

    if not foreground and not is_worker:
        spawn_background(log_file)

    start_collection()
    server = HTTPServer(('0.0.0.0', 5000), RequestHandler)
    if is_worker:
        with open(log_file, 'a') as f:
            f.write(f"Web UI started (PID: {os.getpid()})\n")
    else:
        print(f"Web UI running at http://localhost:5000", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not is_worker:
            print("\nServer stopped.", flush=True)


def start_collection():
    thread = threading.Thread(target=metrics_store.collect_loop, daemon=True)
    thread.start()


if __name__ == '__main__':
    main()
