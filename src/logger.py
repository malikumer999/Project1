import logging
import os

import psutil

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class _LoggerPrefixFilter(logging.Filter):
    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def filter(self, record):
        return record.name.startswith(self.prefix)


class _JobPostFilter(logging.Filter):
    def filter(self, record):
        return record.name == "discord" and "Posted job" in record.getMessage()


def _add_file_handler(filename, logger_prefix=None, level=logging.INFO):
    handler = logging.FileHandler(os.path.join("logs", filename))
    handler.setLevel(level)
    handler.setFormatter(_formatter)
    if logger_prefix:
        handler.addFilter(_LoggerPrefixFilter(logger_prefix))
    logging.getLogger().addHandler(handler)


def _add_job_log_handler():
    handler = logging.FileHandler(os.path.join("logs", "bot.log"))
    handler.setLevel(logging.INFO)
    handler.setFormatter(_formatter)
    handler.addFilter(_JobPostFilter())
    logging.getLogger().addHandler(handler)


_add_file_handler("scraper.log", "scraper")
_add_file_handler("discord.log", "discord")
_add_file_handler("errors.log", level=logging.WARNING)
_add_job_log_handler()

def get_logger(name):
    return logging.getLogger(name)


def format_uptime(seconds):
    """Render a running duration as 'X hours, Y minutes' (hours only while exact)."""
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} hour{'s' if hours != 1 else ''}, {minutes} minute{'s' if minutes != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{total_minutes} minute{'s' if total_minutes != 1 else ''}"


def check_memory_usage(logger=None, warning_threshold_mb=500):
    """Log this process's RSS and warn when it exceeds the configured limit."""
    logger = logger or get_logger("resource")
    memory_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    logger.info("Memory usage: %.2f MB", memory_mb)
    if memory_mb > warning_threshold_mb:
        logger.warning("High memory usage detected: %.2f MB", memory_mb)
    return memory_mb