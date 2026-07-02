import psutil
import time
import platform
import sqlite3
from collections import deque
from datetime import datetime

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.text import Text
from rich.align import Align

# Try to load AMD GPU tools gracefully
try:
    import pyamdgpuinfo

    try:
        HAS_GPU = pyamdgpuinfo.detect_gpus() > 0
    except Exception:
        # Catches FileNotFoundError if /dev/dri/ is missing
        HAS_GPU = False
except ImportError:
    HAS_GPU = False


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
        power = 2 ** 10
        n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size > power:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels.get(n, 'B')}"

    def get_network_speeds(self):
        """Calculates exact upload/download speeds based on time deltas."""
        now = time.time()
        current_net_io = psutil.net_io_counters()
        dt = now - self.last_time

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
        BarColumn(bar_width=None, complete_style="green", finished_style="red"),
        TaskProgressColumn(),
        expand=True
    )

    for i, percent in enumerate(cpu_percentages):
        # Color shift: Green -> Yellow -> Red based on load
        color = "green"
        if percent > 60: color = "yellow"
        if percent > 85: color = "red"

        progress.add_task("cpu", total=100, completed=percent, core=i)

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


def generate_gpu_panel() -> Panel:
    """Renders GPU metrics specifically for AMD Radeon architecture."""
    if is_wsl():
        msg = "WSL Hypervisor Detected.\n\nRaw PCIe GPU sensors (Thermals/VRAM)\nare blocked by the Windows hypervisor.\n\nRun directly in Windows CMD/PowerShell\nto access hardware sensors."
        return Panel(Align.center(Text(msg, style="dim yellow", justify="center")), title=" GPU (Hypervisor Blocked) ",
                     border_style="yellow")

    if not HAS_GPU:
        return Panel(Align.center(Text("No AMD GPU detected or drivers missing.", style="dim")), title=" GPU ",
                     border_style="red")

    try:
        gpu = pyamdgpuinfo.get_gpu(0)

        load_percent = gpu.query_load() * 100
        vram_used = gpu.query_vram_usage() / (1024 ** 2)
        vram_total = gpu.memory_info['vram_size'] / (1024 ** 2)
        temp = gpu.query_temperature()

        table = Table(expand=True, show_edge=False)
        table.add_column("GPU", style="bold cyan")
        table.add_column("Load", style="bold yellow")
        table.add_column("VRAM", style="bold green")
        table.add_column("Temp", style="bold red")

        table.add_row(
            gpu.name,
            f"{load_percent:.1f}%",
            f"{vram_used:.0f}MB / {vram_total:.0f}MB",
            f"{temp}°C"
        )
        return Panel(table, title=" GPU Information ", border_style="yellow")
    except Exception as e:
        return Panel(Align.center(Text(f"GPU Query Error: {e}", style="dim red")), title=" GPU ", border_style="red")


# --- New Features: DB, Sparklines, & Processes ---

def init_db():
    """Initializes the SQLite database for historical metrics."""
    conn = sqlite3.connect("system_metrics.db")
    cursor = conn.cursor()
    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS metrics
                   (
                       id
                       INTEGER
                       PRIMARY
                       KEY
                       AUTOINCREMENT,
                       timestamp
                       DATETIME
                       DEFAULT
                       CURRENT_TIMESTAMP,
                       cpu_percent
                       REAL,
                       ram_percent
                       REAL
                   )
                   """)
    conn.commit()
    return conn


def generate_sparkline(data_points):
    """Converts a list of percentages into a unicode sparkline."""
    bars = " ▂▃▄▅▆▇█"
    line = ""
    for p in data_points:
        index = min(int((p or 0) / 12.5), 7)
        line += bars[index]
    return line


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

    # Sort processes by memory usage
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

    # Split into Header, Main Body, and Lower Body
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=2),
        Layout(name="lower", ratio=1)
    )

    # Split Main Body into Left and Right Columns
    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right")
    )

    # Left Column: CPU & Memory
    layout["left"].split_column(
        Layout(name="cpu", ratio=2),
        Layout(name="memory", size=6)
    )

    # Right Column: GPU & Network
    layout["right"].split_column(
        Layout(name="gpu", ratio=1),
        Layout(name="network", size=6)
    )

    # Lower Body: Trends and Processes
    layout["lower"].split_row(
        Layout(name="trends", ratio=1),
        Layout(name="processes", ratio=1)
    )

    return layout


def main():
    db_conn = init_db()
    monitor = HardwareMonitor()
    layout = make_layout()

    # Pre-warm psutil CPU counter to prevent a 0.0% reading on first loop
    psutil.cpu_percent(interval=0.1, percpu=True)

    tick = 0
    # Initialize Live context manager (Runs the render loop automatically)
    with Live(layout, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                # 1. Gather Data & Update History
                cpu_percentages = psutil.cpu_percent(interval=None, percpu=True)
                current_cpu = sum(cpu_percentages) / len(cpu_percentages) if cpu_percentages else 0
                current_ram = psutil.virtual_memory().percent

                monitor.update_history(current_cpu, current_ram)

                # 2. Log to DB every 5 seconds (10 ticks at 2 refreshes/sec)
                if tick % 10 == 0:
                    cursor = db_conn.cursor()
                    cursor.execute("INSERT INTO metrics (cpu_percent, ram_percent) VALUES (?, ?)",
                                   (current_cpu, current_ram))
                    db_conn.commit()

                # 3. Render UI components
                layout["header"].update(generate_header())
                layout["cpu"].update(generate_cpu_panel(cpu_percentages))
                layout["memory"].update(generate_memory_panel(monitor))
                layout["network"].update(generate_network_panel(monitor))
                layout["gpu"].update(generate_gpu_panel())
                layout["trends"].update(generate_trend_panel(monitor))
                layout["processes"].update(generate_process_panel())

                tick += 1
                time.sleep(0.5)  # Throttle to prevent consuming CPU to monitor CPU
        except KeyboardInterrupt:
            # Cleanly exit when user presses Ctrl+C
            db_conn.close()


if __name__ == "__main__":
    main()