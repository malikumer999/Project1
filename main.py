"""Single entrypoint: starts the scraper loop and the Discord bot together."""

import threading

from src.discord_bot.bot import DISCORD_TOKEN, bot
from src.scheduler.scrape_loop import run_scrape_loop

__all__ = ["main"]


def main():
    """Run the Upwork scraper in a background thread and the bot on the main thread."""
    scraper_thread = threading.Thread(
        target=run_scrape_loop,
        kwargs={"install_signal_handlers": False},
        name="upwork-scraper",
        daemon=True,
    )
    scraper_thread.start()

    # The bot blocks until disconnection; Ctrl+C (or SIGTERM) ends the process.
    try:
        bot.run(DISCORD_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        pass
    finally:
        # The scraper daemon thread may be stuck inside a blocking Chrome/
        # selenium call, which can hang a normal interpreter shutdown.
        # Exit hard so Ctrl+C always works instantly.
        import os
        os._exit(0)


if __name__ == "__main__":
    main()
