import psutil
import time
import platform
import sqlite3
import subprocess
import json
from collections import deque
from datetime import datetime

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.text import Text
from rich.align import Align

# Try to load Windows GPU tools gracefully
try:
    import GPUtil
    # getGPUs() shells out to nvidia-smi and raises a variety of things when it
    # is missing or the driver is unhappy, not just ImportError.
    HAS_GPU = len(GPUtil.getGPUs()) > 0
except Exception:
    HAS_GPU = False

# Try to load requests for LibreHardwareMonitor's web server (real AMD/Intel sensor data)
try:
    import requests
    HAS_LHM_WEB = True
except ImportError:
    HAS_LHM_WEB = False

def is_wsl() -> bool:
    """Detects if the environment is running inside Windows Subsystem for Linux."""
    return 'microsoft' in platform.release().lower()

class HardwareMonitor:
    """Handles the stateful tracking of hardware metrics (especially network deltas)."""
    def __init__(self):
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()
        self.cpu_history = deque([0] * 60, maxlen=60)
        self.ram_history = deque([0] * 60, maxlen=60)

    def update_history(self, cpu, ram):
        self.cpu_history.append(cpu)
        self.ram_history.append(ram)

    def format_bytes(self, size):
        """Converts raw bytes into human-readable formats (KB, MB, GB)."""
        power = 2**10
        n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        # Stop at the largest label we have. Running past it used to fall through
        # to .get(n, 'B') and report petabytes as bytes.
        while size >= power and n < max(power_labels):
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"

    def get_network_speeds(self):
        """Calculates exact upload/download speeds based on time deltas."""
        now = time.time()
        current_net_io = psutil.net_io_counters()
        # Two calls inside the same clock tick would divide by zero
        dt = max(now - self.last_time, 1e-6)

        up_speed = (current_net_io.bytes_sent - self.last_net_io.bytes_sent) / dt
        down_speed = (current_net_io.bytes_recv - self.last_net_io.bytes_recv) / dt

        self.last_net_io = current_net_io
        self.last_time = now

        return self.format_bytes(up_speed), self.format_bytes(down_speed)

# --- UI Component Generators ---

def generate_header() -> Panel:
    """Creates the top header bar with OS info and current time."""
    sys_info = f"{platform.system()} {platform.release()} | {platform.node()}"
    clock = datetime.now().strftime("%H:%M:%S | %Y-%m-%d")

    table = Table.grid(expand=True)
    table.add_column(justify="left", ratio=1)
    table.add_column(justify="right", ratio=1)
    table.add_row(
        Text(sys_info, style="bold cyan"),
        Text(clock, style="bold magenta")
    )
    return Panel(table, style="bold white", border_style="blue")

def generate_cpu_panel(cpu_percentages) -> Panel:
    """Renders progress bars for every logical CPU core."""

    progress = Progress(
        TextColumn("[bold blue]Core {task.fields[core]:>2}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        expand=True
    )

    for i, percent in enumerate(cpu_percentages):
        # Color shift: Green -> Yellow -> Red based on load.
        # The style has to go on the task, not the shared BarColumn: a single
        # complete_style on the column paints every core the same colour, so the
        # value computed here used to be discarded and all bars stayed green.
        color = "green"
        if percent > 60: color = "yellow"
        if percent > 85: color = "red"

        progress.add_task("cpu", total=100, completed=percent, core=i,
                          style="grey23", complete_style=color, finished_style=color)

    # Use a grid layout to format multiple cores nicely
    table = Table.grid(expand=True)
    table.add_column()
    table.add_row(progress)

    avg_cpu = sum(cpu_percentages) / len(cpu_percentages) if cpu_percentages else 0
    title = f" CPU Usage (Avg: {avg_cpu:.1f}%) "
    return Panel(table, title=title, border_style="cyan")

def generate_memory_panel(monitor: HardwareMonitor) -> Panel:
    """Renders RAM and Swap usage."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    progress = Progress(
        TextColumn("[bold]{task.fields[label]}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
        TextColumn("{task.fields[usage]}"),
        expand=True
    )

    mem_usage_str = f"{monitor.format_bytes(mem.used)} / {monitor.format_bytes(mem.total)}"
    progress.add_task("ram", total=100, completed=mem.percent, label="RAM ", usage=mem_usage_str)

    swap_usage_str = f"{monitor.format_bytes(swap.used)} / {monitor.format_bytes(swap.total)}"
    progress.add_task("swap", total=100, completed=swap.percent, label="SWAP", usage=swap_usage_str)

    return Panel(progress, title=" Memory Information ", border_style="green")

def generate_network_panel(monitor: HardwareMonitor) -> Panel:
    """Renders live upload/download network speeds."""
    up_speed, down_speed = monitor.get_network_speeds()
    total_up = monitor.format_bytes(monitor.last_net_io.bytes_sent)
    total_down = monitor.format_bytes(monitor.last_net_io.bytes_recv)

    table = Table(expand=True, show_edge=False, show_header=False)
    table.add_column("Type", style="bold")
    table.add_column("Speed", style="bold yellow")
    table.add_column("Total", style="dim")

    table.add_row("🔽 Download", f"{down_speed}/s", f"Total: {total_down}")
    table.add_row("🔼 Upload", f"{up_speed}/s", f"Total: {total_up}")

    return Panel(table, title=" Network I/O ", border_style="magenta")

# --- LibreHardwareMonitor sensor bridge (via built-in web server) ---
#
# Standard WMI (Win32_VideoController) only exposes a static hardware inventory:
# no live load, no temp, and AdapterRAM is a 32-bit field that hard-caps at 4GB
# regardless of actual VRAM. WMI namespace registration for LHM's own provider
# also turned out to be broken on this machine (target namespace didn't exist),
# so instead we use LHM's built-in web server, which is simpler and just as live.
#
# Setup required:
#   1. pip install requests
#   2. In LibreHardwareMonitor: Options menu -> check "Remote Web Server"
#      (default port 8085). Leave LHM running as Administrator in the background.
#   3. Verify it works by opening http://localhost:8085/data.json in a browser.
#
# The JSON is a tree (Text/Children). We find the node whose HardwareId starts
# with "/gpu-amd" (confirmed via a live data.json pull), then read specific
# child sensors by their exact Text label under each category group.

LHM_URL = "http://localhost:8085/data.json"

# The dashboard repaints twice a second, but GPU sensors do not need polling that
# fast and an HTTP round trip per frame is wasteful when LHM is up and a 1 second
# stall per frame when it is not. Sensors are refreshed at most this often and the
# last reading is reused in between.
GPU_POLL_INTERVAL = 2.0

# When the LHM fetch fails it used to append a line to gpu_debug.log on every
# frame, so a session with LHM closed grew the file by two lines a second
# indefinitely. Failures are now logged once per distinct reason, then throttled.
GPU_LOG_INTERVAL = 60.0

_gpu_cache = {"panel": None, "fetched_at": 0.0}
_gpu_log_state = {"last_message": None, "last_logged_at": 0.0}


def _log_gpu_debug(message):
    """Appends to gpu_debug.log, but only when the reason changed or a minute passed."""
    now = time.time()
    if (message == _gpu_log_state["last_message"]
            and now - _gpu_log_state["last_logged_at"] < GPU_LOG_INTERVAL):
        return
    _gpu_log_state["last_message"] = message
    _gpu_log_state["last_logged_at"] = now
    try:
        with open("gpu_debug.log", "a") as f:
            f.write(f"{datetime.now()} {message}\n")
    except OSError:
        pass

def _find_node_by_hardware_id_prefix(node, prefix):
    """Recursively searches the LHM sensor tree for a node whose HardwareId starts with prefix."""
    if node.get("HardwareId", "").startswith(prefix):
        return node
    for child in node.get("Children", []):
        found = _find_node_by_hardware_id_prefix(child, prefix)
        if found is not None:
            return found
    return None

def _find_child_value(node, group_text, target_text):
    """Within a hardware node, finds Children[group_text].Children[target_text].Value."""
    for group in node.get("Children", []):
        if group.get("Text") == group_text:
            for leaf in group.get("Children", []):
                if leaf.get("Text") == target_text:
                    return leaf.get("Value", "")
    return None

def _parse_number(raw_value):
    """Strips units like '°C', '%', 'W', 'MB' from an LHM value string and returns a float."""
    if not raw_value:
        return None
    try:
        return float(raw_value.split(" ")[0])
    except (ValueError, IndexError):
        return None

def get_lhm_gpu_sensors():
    """Pulls live GPU sensor data from LibreHardwareMonitor's web server."""
    resp = requests.get(LHM_URL, timeout=1)
    resp.raise_for_status()
    root = resp.json()

    gpu_node = _find_node_by_hardware_id_prefix(root, "/gpu-amd")
    if gpu_node is None:
        return None

    data = {
        "name": gpu_node.get("Text", "AMD GPU"),
        "temp": _parse_number(_find_child_value(gpu_node, "Temperatures", "GPU Core")),
        "hotspot": _parse_number(_find_child_value(gpu_node, "Temperatures", "GPU Hot Spot")),
        "load": _parse_number(_find_child_value(gpu_node, "Load", "GPU Core")),
        "power": _parse_number(_find_child_value(gpu_node, "Powers", "GPU Package")),
        "vram_used": _parse_number(_find_child_value(gpu_node, "Data", "GPU Memory Used")),
        "vram_total": _parse_number(_find_child_value(gpu_node, "Data", "GPU Memory Total")),
    }

    return data if data["temp"] is not None else None

def generate_gpu_panel_lhm(sensor_data) -> Panel:
    """Renders GPU panel from real LibreHardwareMonitor sensor data."""
    table = Table(expand=True, show_edge=False)
    table.add_column("GPU", style="bold cyan")
    table.add_column("Load", style="bold yellow")
    table.add_column("VRAM", style="bold green")
    table.add_column("Temp", style="bold red")
    table.add_column("Hot Spot", style="bold red")
    table.add_column("Power", style="bold magenta")

    name = (sensor_data["name"] or "AMD GPU")[:20]
    load_str = f"{sensor_data['load']:.1f}%" if sensor_data["load"] is not None else "N/A"
    temp_str = f"{sensor_data['temp']:.0f}°C" if sensor_data["temp"] is not None else "N/A"
    hotspot_str = f"{sensor_data['hotspot']:.0f}°C" if sensor_data.get("hotspot") is not None else "N/A"
    power_str = f"{sensor_data['power']:.0f}W" if sensor_data["power"] is not None else "N/A"

    if sensor_data["vram_used"] is not None and sensor_data["vram_total"] is not None:
        vram_str = f"{sensor_data['vram_used']:.0f}MB / {sensor_data['vram_total']:.0f}MB"
    else:
        vram_str = "N/A"

    table.add_row(name, load_str, vram_str, temp_str, hotspot_str, power_str)

    # Color the border based on hot spot temp (more sensitive early-warning signal than core temp)
    border = "green"
    reference_temp = sensor_data.get("hotspot") or sensor_data.get("temp")
    if reference_temp is not None:
        if reference_temp > 85:
            border = "yellow"
        if reference_temp > 100:
            border = "red"

    return Panel(table, title=" GPU Information (LibreHardwareMonitor) ", border_style=border)

def generate_gpu_panel() -> Panel:
    """
    Returns the GPU panel, refreshing the underlying sensors at most every
    GPU_POLL_INTERVAL seconds and reusing the last panel in between.
    """
    now = time.time()
    if (_gpu_cache["panel"] is not None
            and now - _gpu_cache["fetched_at"] < GPU_POLL_INTERVAL):
        return _gpu_cache["panel"]

    panel = _build_gpu_panel()
    _gpu_cache["panel"] = panel
    _gpu_cache["fetched_at"] = now
    return panel


def _build_gpu_panel() -> Panel:
    """
    Fetches GPU info, preferring real sensor data in this order:
    1. LibreHardwareMonitor via its web server (real temp/load/vram/power)
    2. GPUtil (Nvidia only)
    3. Manual Win32_VideoController WMI fallback (static info only, VRAM capped at 4GB)
    """
    if is_wsl():
        msg = "WSL Hypervisor Detected.\n\nRaw PCIe GPU sensors (Thermals/VRAM)\nare blocked by the Windows hypervisor.\n\nRun directly in Windows CMD/PowerShell\nto access hardware sensors."
        return Panel(Align.center(Text(msg, style="dim yellow", justify="center")), title=" GPU (Hypervisor Blocked) ", border_style="yellow")

    # 1. Try LibreHardwareMonitor's web server first — this is the real fix for AMD sensors
    if HAS_LHM_WEB:
        try:
            lhm_data = get_lhm_gpu_sensors()
            if lhm_data is not None:
                return generate_gpu_panel_lhm(lhm_data)
        except Exception as e:
            # Log the real reason instead of silently falling through, so this is debuggable
            _log_gpu_debug(f"LHM web fetch failed: {type(e).__name__}: {e}")
    else:
        _log_gpu_debug("'requests' module not available in this build")

    # 2. Nvidia via GPUtil
    if HAS_GPU:
        try:
            gpu = GPUtil.getGPUs()[0]

            load_percent = gpu.load * 100
            vram_used = gpu.memoryUsed
            vram_total = gpu.memoryTotal
            temp = gpu.temperature

            table = Table(expand=True, show_edge=False)
            table.add_column("GPU", style="bold cyan")
            table.add_column("Load", style="bold yellow")
            table.add_column("VRAM", style="bold green")
            table.add_column("Temp", style="bold red")

            table.add_row(
                gpu.name[:15],
                f"{load_percent:.1f}%",
                f"{vram_used:.0f}MB / {vram_total:.0f}MB",
                f"{temp}°C"
            )
            return Panel(table, title=" GPU Information ", border_style="yellow")
        except Exception:
            pass  # Fallback to manual WMI

    # 3. Manual Windows Fallback for AMD / Intel (static info only — VRAM capped at 4GB by WMI)
    try:
        cmd = ['powershell', '-NoProfile', '-Command', 'Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json']

        flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)

        if not result.stdout.strip():
            return Panel(Align.center(Text("No GPU detected.", style="dim")), title=" GPU ", border_style="red")

        data = json.loads(result.stdout)
        if isinstance(data, list):
            data = data[0]  # Grab primary GPU if multiple exist

        gpu_name = data.get('Name', 'Unknown AMD/Intel GPU')
        vram_bytes = data.get('AdapterRAM', 0)

        vram_gb = vram_bytes / (1024**3) if vram_bytes else 0
        vram_display = f"{vram_gb:.1f} GB" if vram_bytes else "N/A"

        if vram_bytes == 4294967296 or vram_bytes == 4294967295:
            vram_display = "4.0+ GB (WMI Capped — install LibreHardwareMonitor for real VRAM)"

        table = Table(expand=True, show_edge=False)
        table.add_column("GPU", style="bold cyan")
        table.add_column("Load", style="dim yellow")
        table.add_column("VRAM", style="bold green")
        table.add_column("Temp", style="dim red")

        table.add_row(
            gpu_name[:22],
            "OS Locked",
            vram_display,
            "Req. LHM"
        )
        return Panel(table, title=" GPU Info (WMI Fallback — install LibreHardwareMonitor) ", border_style="cyan")

    except Exception as e:
        return Panel(Align.center(Text(f"Manual GPU Query Error: {e}", style="dim red")), title=" GPU ", border_style="red")

# --- New Features: DB, Sparklines, & Processes ---

def init_db():
    """Initializes the SQLite database for historical metrics."""
    conn = sqlite3.connect("system_metrics.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cpu_percent REAL,
            ram_percent REAL
        )
    """)
    conn.commit()
    return conn

def generate_sparkline(data_points):
    """Converts a list of percentages into a fixed-width, Windows-safe ASCII sparkline."""
    bars = " .|:-=+*#%@"
    line = ""
    for p in data_points:
        if p == 0:
            line += " "
        else:
            index = min(int((p or 0) / 10), 9) + 1
            line += bars[index]

    return line.ljust(60, " ")

def generate_trend_panel(monitor: HardwareMonitor) -> Panel:
    """Renders 60-second sparkline trends for CPU and RAM."""
    cpu_spark = generate_sparkline(monitor.cpu_history)
    ram_spark = generate_sparkline(monitor.ram_history)

    table = Table.grid(padding=1, expand=True)
    table.add_column("Resource", style="cyan", width=8)
    table.add_column("Trend (Last 60s)", style="bold yellow")

    table.add_row("CPU", cpu_spark)
    table.add_row("RAM", ram_spark)

    return Panel(table, title=" 📈 60-Second Trends ", border_style="blue")

def generate_process_panel() -> Panel:
    """Fetches and displays top 5 processes by memory usage."""
    table = Table(expand=True, show_edge=False)
    table.add_column("PID", style="dim")
    table.add_column("Name", style="bold white")
    table.add_column("Mem %", justify="right", style="magenta")
    table.add_column("CPU %", justify="right", style="green")

    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
        try:
            pinfo = proc.info
            if pinfo['memory_percent'] is not None:
                processes.append(pinfo)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    processes = sorted(processes, key=lambda p: p['memory_percent'] or 0, reverse=True)[:5]

    for p in processes:
        table.add_row(
            str(p['pid']),
            p['name'][:15],
            f"{p['memory_percent']:.1f}%",
            f"{p['cpu_percent']:.1f}%"
        )
    return Panel(table, title=" ⚙️ Top Processes (RAM) ", border_style="red")

# --- Main Dashboard Setup ---

def make_layout() -> Layout:
    """Defines the grid structure of the dashboard UI."""
    layout = Layout(name="root")

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=2),
        Layout(name="lower", ratio=1)
    )

    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right")
    )

    layout["left"].split_column(
        Layout(name="cpu", ratio=2),
        Layout(name="memory", size=6)
    )

    layout["right"].split_column(
        Layout(name="gpu", ratio=1),
        Layout(name="network", size=6)
    )

    layout["lower"].split_row(
        Layout(name="trends", ratio=1),
        Layout(name="processes", ratio=1)
    )

    return layout

def main():
    db_conn = init_db()
    monitor = HardwareMonitor()
    layout = make_layout()

    psutil.cpu_percent(interval=0.1, percpu=True)

    tick = 0
    with Live(layout, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                cpu_percentages = psutil.cpu_percent(interval=None, percpu=True)
                current_cpu = sum(cpu_percentages) / len(cpu_percentages) if cpu_percentages else 0
                current_ram = psutil.virtual_memory().percent

                monitor.update_history(current_cpu, current_ram)

                if tick % 10 == 0:
                    cursor = db_conn.cursor()
                    cursor.execute("INSERT INTO metrics (cpu_percent, ram_percent) VALUES (?, ?)", (current_cpu, current_ram))
                    db_conn.commit()

                layout["header"].update(generate_header())
                layout["cpu"].update(generate_cpu_panel(cpu_percentages))
                layout["memory"].update(generate_memory_panel(monitor))
                layout["network"].update(generate_network_panel(monitor))
                layout["gpu"].update(generate_gpu_panel())
                layout["trends"].update(generate_trend_panel(monitor))
                layout["processes"].update(generate_process_panel())

                tick += 1
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            # Previously only the Ctrl-C path closed the connection, so any other
            # exception left the last writes unflushed.
            db_conn.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("\n" + "="*50)
        print(" FATAL ERROR ENCOUNTERED")
        print("="*50)
        traceback.print_exc()
        print("="*50)
        input("\nPress Enter to close this window...")