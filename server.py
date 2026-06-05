import time
import multiprocessing
import sys
import threading
import math
import plotext as plt
import pyfiglet
from sshkeyboard import listen_keyboard, stop_listening

# --- CPU Stress Worker ---
def stress_worker(load_shared, core_id):
    """Dynamically throttles itself to match the target load percentage."""
    while True:
        with load_shared.get_lock():
            target_load = load_shared.value / 100.0
        if target_load <= 0:
            time.sleep(0.1)
            continue

        start = time.time()
        while time.time() - start < (0.01 * target_load):
            _ = 1.1 ** 1.1
        time.sleep(0.01 * (1.0 - target_load))

def get_temp():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        return float(f.read()) / 1000.0

class DemoDashboard:
    def __init__(self):
        self.start_time = time.time()
        self.times = []
        self.temps = []
        self.markers = []
        self.step_timestamps = {}
        self.fan_speed = 0
        self.fan_enabled = True
        self.demo_active = False
        self.demo_step = 1
        self.demo_start_time = None
        self.running = True
        self.current_std_dev = 0.0
        self.current_delta = 0.0
        self.is_steady = False
        self.STEADY_THRESHOLD = 0.5
        self._seen_unsteady_in_step = False

    def reset_demo(self):
        self.demo_step = 1
        self.demo_start_time = time.time()
        self.markers = []
        self.step_timestamps = {}
        self._seen_unsteady_in_step = False

    def _enter_steady_wait_step(self):
        self._seen_unsteady_in_step = False

    def calculate_metrics(self, window_samples=30):
        if len(self.temps) < window_samples:
            self.current_std_dev = 0.0
            self.current_delta = 0.0
            self.is_steady = False
            return
        window = self.temps[-window_samples:]
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        self.current_std_dev = math.sqrt(variance)
        self.current_delta = max(window) - min(window)
        self.is_steady = self.current_std_dev <= self.STEADY_THRESHOLD
        if not self.is_steady:
            self._seen_unsteady_in_step = True

    def _steady_confirmed(self):
        return self.is_steady and self._seen_unsteady_in_step

    def update_demo_logic(self, current_time, current_temp, load_shared, fan_device):
        actual_fan_speed = self.fan_speed if self.fan_enabled else 0
        if fan_device:
            fan_device.value = actual_fan_speed / 100.0
        self.calculate_metrics()
        if not self.demo_active:
            return
        demo_elapsed = time.time() - self.demo_start_time

        if self.demo_step == 1:
            load_shared.value = 0
            self.fan_speed = 0
            if demo_elapsed >= 30.0:
                self.step_timestamps["Step 1 (Baseline Ready)"] = f"{current_time:.1f}s"
                self.markers.append(current_time)
                self.demo_step = 2
                self._enter_steady_wait_step()

        elif self.demo_step == 2:
            load_shared.value = 100
            if self._steady_confirmed():
                self.step_timestamps["Step 2 (Load Max Steady)"] = f"{current_time:.1f}s"
                self.markers.append(current_time)
                self.demo_step = 3

        elif self.demo_step == 3:
            self.fan_speed = 100
            self.fan_enabled = True
            self.step_timestamps["Step 3 (Fan Deployed)"] = f"{current_time:.1f}s"
            self.markers.append(current_time)
            self.demo_step = 4
            self._enter_steady_wait_step()

        elif self.demo_step == 4:
            if self._steady_confirmed():
                self.step_timestamps["Step 4 (Cooldown Steady)"] = f"{current_time:.1f}s"
                self.markers.append(current_time)
                self.demo_step = 5
                self._enter_steady_wait_step()

        elif self.demo_step == 5:
            load_shared.value = 0
            if self._steady_confirmed():
                self.step_timestamps["Step 5 (Final Idle Steady)"] = f"{current_time:.1f}s"
                self.markers.append(current_time)
                self.demo_step = 6

    def draw_dashboard(self, current_time, current_temp, load_val):
        plt.clear_figure()
        plt.plot(self.times, self.temps, marker="braille", color="red")
        plt.hline(80, color="yellow")
        plt.title("Raspberry Pi Thermal Dynamic Demo & Dashboard")
        plt.ylim(40, 95)
        plt.plotsize(90, 16)
        plt.theme("dark")

        raw_ascii = pyfiglet.figlet_format(f"{current_temp:.1f} C", font="standard")
        clean_ascii = "".join([f"{line}\033[K\n" for line in raw_ascii.splitlines()])

        fan_status = f"{self.fan_speed}%" if self.fan_enabled else "MUTED (0%)"
        demo_status = f"\033[92mRUNNING (Step {self.demo_step})\033[0m" if self.demo_active else "\033[91mINACTIVE (Manual Mode)\033[0m"
        steady_status = "\033[92mSTEADY\033[0m" if self.is_steady else "\033[93mFLUCTUATING\033[0m"

        sys.stdout.write("\033[H")
        plt.show()
        print("\n" + clean_ascii)
        print(f"--- SYSTEM STATUS PANEL \033[K")
        print(f"Total Elapsed Time: {current_time:.1f}s | Demo Mode: {demo_status}\033[K")
        print(f"Target CPU Load: {load_val}% | Fan Status: {fan_status}\033[K")
        print(f"--- \033[92mLIVE THERMAL METRICS\033[0m \033[K")
        print(f"\033[92mStatus: {steady_status} | Rolling Delta: {self.current_delta:.2f}°C | Std Dev: {self.current_std_dev:.3f} (Target <= {self.STEADY_THRESHOLD})\033[0m\033[K")
        print(f"--- KEYBINDINGS \033[K")
        print(f"[d]: Activate/Cancel Demo Routine | [f]: Toggle Fan Override\033[K")
        print(f"[1-5]: Fan Speed (20-100%)       | [6-0]: CPU Load (20-100%, 0=100%)\033[K")
        print(f"[q] or [esc]: Exit Program \033[K")

        if self.demo_active or len(self.step_timestamps) > 0:
            print(f"--- AUTOMATED DEMO TIMING LOGS \033[K")
            for step, timestamp in self.step_timestamps.items():
                print(f" > {step}: {timestamp}\033[K")
        else:
            print(f"\033[K\n\033[K\n\033[K\n\033[K")
        sys.stdout.flush()

def update_loop(dashboard, load_shared, fan_device):
    while dashboard.running:
        elapsed = time.time() - dashboard.start_time
        temp = get_temp()
        dashboard.times.append(elapsed)
        dashboard.temps.append(temp)
        if len(dashboard.times) > 150:
            dashboard.times.pop(0)
            dashboard.temps.pop(0)
        dashboard.update_demo_logic(elapsed, temp, load_shared, fan_device)
        dashboard.draw_dashboard(elapsed, temp, load_shared.value)
        time.sleep(0.2)

def make_key_handler(dashboard, load_shared):
    def handle_keypress(key):
        if key == "d":
            dashboard.demo_active = not dashboard.demo_active
            if dashboard.demo_active:
                dashboard.reset_demo()
        elif key == "f":
            dashboard.fan_enabled = not dashboard.fan_enabled
        elif key == "1": dashboard.fan_speed = 20
        elif key == "2": dashboard.fan_speed = 40
        elif key == "3": dashboard.fan_speed = 60
        elif key == "4": dashboard.fan_speed = 80
        elif key == "5": dashboard.fan_speed = 100
        elif key == "6": load_shared.value = 20
        elif key == "7": load_shared.value = 40
        elif key == "8": load_shared.value = 60
        elif key == "9": load_shared.value = 80
        elif key == "0": load_shared.value = 100
        elif key == "esc" or key == "q":
            dashboard.running = False
            stop_listening()
    return handle_keypress

if __name__ == "__main__":
    fan_device = None
    try:
        from gpiozero import PWMOutputDevice
        fan_device = PWMOutputDevice(18, frequency=100, start_value=0.0)
    except Exception:
        pass

    load_shared = multiprocessing.Value('i', 0)
    cores = multiprocessing.cpu_count()
    processes = [multiprocessing.Process(target=stress_worker, args=(load_shared, i)) for i in range(cores)]
    for p in processes:
        p.start()

    db = DemoDashboard()
    print("\033[2J\033[H", end="")

    render_thread = threading.Thread(target=update_loop, args=(db, load_shared, fan_device), daemon=True)
    render_thread.start()

    handle_key = make_key_handler(db, load_shared)

    try:
        listen_keyboard(on_press=handle_key, sleep=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        db.running = False
        render_thread.join(timeout=2.0)
        print("\033[K\nShutting down stress workers and turning off fan control...")
        for p in processes:
            p.terminate()
            p.join()
        if fan_device:
            fan_device.value = 0.0
            fan_device.close()
        print("Done. System returned to standard safe idle.\033[K")
