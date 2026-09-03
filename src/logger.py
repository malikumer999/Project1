import logging
import os

import psutil

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)

def get_logger(name):
    return logging.getLogger(name)


def check_memory_usage(logger=None, warning_threshold_mb=50):
    """Log this process's RSS and warn when it exceeds the configured limit."""
    logger = logger or get_logger("resource")
    memory_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    logger.info("Memory usage: %.2f MB", memory_mb)
    if memory_mb > warning_threshold_mb:
        logger.warning("High memory usage detected: %.2f MB", memory_mb)
    return memory_mb