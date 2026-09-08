"""Single entrypoint: starts the dashboard, scraper loop, and the Discord bot together."""

import os
import signal
import threading
import time

from src.dashboard import request_shutdown, start_dashboard
from src.discord_bot.bot import DISCORD_TOKEN, bot
from src.scheduler.scrape_loop import run_scrape_loop
from src.storage.database import get_connection

__all__ = ["main"]


def _print_stop_summary():
    conn = get_connection()
    try:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        posted_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_to_discord = 1").fetchone()[0]
        unposted_jobs = total_jobs - posted_jobs
        channel_count = conn.execute("SELECT COUNT(*) FROM discord_channels WHERE active = 1").fetchone()[0]
    finally:
        conn.close()
    print()
    print("=" * 60)
    print("  SYSTEM STOPPED")
    print("=" * 60)
    print(f"  Discord Channels (active): {channel_count}")
    print(f"  Total Jobs:                {total_jobs}")
    print(f"  Posted to Discord:         {posted_jobs}")
    print(f"  Queued (not yet posted):   {unposted_jobs}")
    print("=" * 60)


def main():
    """Start the dashboard, scraper loop, and Discord bot, then wait for shutdown."""
    started_at = time.monotonic()

    def _handle_shutdown(signum, _frame):
        elapsed = time.monotonic() - started_at
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        print(f"\n[main] Stop signal received (signal {signum}). Uptime: {h:02d}:{m:02d}:{s:02d}")
        request_shutdown()
        _print_stop_summary()
        try:
            bot.close()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_shutdown)

    if os.getenv("DISABLE_DASHBOARD", "0") != "1":
        start_dashboard(open_browser=True)
        print("[main] Dashboard opened in your default browser.")

    scraper_thread = threading.Thread(
        target=run_scrape_loop,
        kwargs={"install_signal_handlers": False},
        name="upwork-scraper",
        daemon=True,
    )
    scraper_thread.start()

    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        pass
    finally:
        request_shutdown()
        _print_stop_summary()
        os._exit(0)


if __name__ == "__main__":
    main()
