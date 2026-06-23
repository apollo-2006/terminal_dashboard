# terminal_dashboard
A live, terminal-based hardware monitor written in Python. Renders real-time CPU, RAM, GPU, and network metrics in a clean dashboard UI — built as a more capable, visually driven alternative to htop.

## Features
* **CPU Monitoring**: Per-core usage displayed as live progress bars with average load tracking.
* **Memory Tracking**: Real-time RAM and Swap usage with human-readable byte formatting.
* **AMD GPU Support**: Pulls live GPU load, VRAM usage, and temperature via `pyamdgpuinfo` for Radeon architecture.
* **Network I/O**: Calculates live upload/download speeds using time-delta polling, with total transfer counters.
* **Rich Terminal UI**: Full dashboard layout powered by the `rich` library with a live refresh loop.

## Build & Run
```bash
# Clone the repository
git clone https://github.com/apollo-2006/terminal_dashboard.git
cd terminal_dashboard

# Create and activate a virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the dashboard
python3 dashboard.py
```

Press `Ctrl+C` to exit cleanly.

## Dependencies
* `psutil`
* `rich`
* `pyamdgpuinfo`

## Author
**Abir Deol**
