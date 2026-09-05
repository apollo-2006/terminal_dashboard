# TerminalDash

A real-time system monitoring dashboard that runs right in your terminal. Built with Python and [Rich](https://github.com/Textualize/rich), it gives you a live view of your CPU, memory, network, GPU, and top processes — no browser tab, no bloated GUI app.

![Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

> Built for Windows, where the GPU sensor work actually matters. CPU, memory, network,
> trends and process panels run fine on Linux and inside WSL; the GPU panel detects WSL
> and says so, because the hypervisor blocks PCIe sensor access.

## Features

- **Per-core CPU usage** with color-coded load bars (green → yellow → red)
- **Memory & swap** usage with live GB counters
- **Network I/O** — real-time upload/download speed and session totals
- **GPU monitoring** — live temperature, hot spot temp, load, power draw, and VRAM usage for AMD GPUs (via LibreHardwareMonitor), with automatic fallback for other setups
- **60-second sparkline trends** for CPU and RAM history
- **Top 5 processes** by RAM usage
- **SQLite logging** — CPU/RAM history is persisted to `system_metrics.db` for later analysis

GPU sensors are polled every 2 seconds and cached between frames, rather than on every
one of the two repaints per second — an HTTP round trip per frame is wasted work when
LibreHardwareMonitor is up, and a one-second stall per frame when it is not.

## Download

Grab the latest pre-built `.exe` from the [Releases page](../../releases/latest) — no Python install needed.

> **Note:** Since this is an unsigned executable, Windows SmartScreen may show a "Windows protected your PC" warning on first launch. Click **More info → Run anyway** to proceed.

## GPU Monitoring Setup (AMD GPUs)

Standard Windows APIs don't expose real GPU sensor data for AMD cards (no live temp/load, and VRAM reporting is capped at 4GB). To get real numbers, TerminalDash reads from [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)'s built-in web server:

1. Download and run **LibreHardwareMonitor** as Administrator
2. In the **Options** menu, enable **Remote Web Server** (default port 8085)
3. Leave it running in the background, then launch TerminalDash

If LibreHardwareMonitor isn't running, TerminalDash automatically falls back to basic Windows WMI data (static info only, no live temps/load). The reason for each fallback is written to `gpu_debug.log`, throttled to one entry per distinct reason per minute.

## Running from Source

```bash
git clone https://github.com/apollo-2006/terminal_dashboard.git
cd terminal_dashboard
pip install -r requirements.txt
python dashboard.py
```

### Building the executable

```bash
python -m PyInstaller --onefile --hidden-import=GPUtil --hidden-import=requests --hidden-import=charset_normalizer --hidden-import=idna --hidden-import=certifi --name TerminalDash dashboard.py
```

The compiled `.exe` will be in the `dist/` folder.

## Requirements

- Windows 10/11
- Python 3.10+ (only needed if running/building from source)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (optional, for real GPU sensor data on AMD cards)

## License

MIT
