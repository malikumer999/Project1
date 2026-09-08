"""Dashboard runner: standalone process that hosts the web UI and controls main.py.

Run with:  python run_dashboard.py
"""

import sys


def main():
    from src.dashboard import start_dashboard
    print("[dashboard-runner] Starting dashboard server…")
    print("[dashboard-runner] Use this dashboard to Start / Stop / Restart the scraper.")
    print("[dashboard-runner] Press Ctrl+C here to stop the dashboard itself.")
    try:
        start_dashboard(open_browser=True)
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[dashboard-runner] Shutting down.")


if __name__ == "__main__":
    main()
