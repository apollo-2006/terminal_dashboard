import psutil
import time
import platform
from datetime import datetime

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.text import Text
from rich.align import Align

# Try to load AMD GPU tools gracefully (catches missing /dev/dri/ in WSL)
try:
    import pyamdgpuinfo

    try:
        HAS_GPU = pyamdgpuinfo.detect_gpus() > 0
    except Exception:
        # Catches FileNotFoundError if /dev/dri/ is missing
        HAS_GPU = False
except ImportError:
    HAS_GPU = False


class HardwareMonitor:
    """Handles the stateful tracking of hardware metrics (especially network deltas)."""

    def __init__(self):
        self.last_net_io = psutil.net_io_counters()
        self.last_time = time.time()

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


def generate_cpu_panel() -> Panel:
    """Renders progress bars for every logical CPU core."""
    cpu_percentages = psutil.cpu_percent(interval=None, percpu=True)

    progress = Progress(
        TextColumn("[bold blue]Core {task.fields[core]}"),
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

    avg_cpu = sum(cpu_percentages) / len(cpu_percentages)
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


# --- Main Dashboard Setup ---

def make_layout() -> Layout:
    """Defines the grid structure of the dashboard UI."""
    layout = Layout(name="root")

    # Split into Top Header, Middle Body
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1)
    )

    # Split Body into Left and Right Columns
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

    return layout


def main():
    monitor = HardwareMonitor()
    layout = make_layout()

    # Pre-warm psutil CPU counter to ensure accurate first reading
    psutil.cpu_percent(interval=0.1)

    # Initialize Live context manager (Runs the render loop automatically)
    with Live(layout, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                layout["header"].update(generate_header())
                layout["cpu"].update(generate_cpu_panel())
                layout["memory"].update(generate_memory_panel(monitor))
                layout["network"].update(generate_network_panel(monitor))
                layout["gpu"].update(generate_gpu_panel())

                time.sleep(0.5)  # Throttle to prevent consuming CPU to monitor CPU
        except KeyboardInterrupt:
            # Cleanly exit when user presses Ctrl+C
            pass


if __name__ == "__main__":
    main()