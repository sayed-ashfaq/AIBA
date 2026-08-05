import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = "logs"
LOG_PREFIX = "nl2sql"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(processName)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, f"{LOG_PREFIX}.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )

    def namer(default_name: str) -> str:
        parts = default_name.rsplit(".", 1)
        if len(parts) == 2:
            try:
                date = datetime.strptime(parts[1], "%Y-%m-%d")
                return os.path.join(LOG_DIR, f"{LOG_PREFIX}{date.strftime('%d%m%y')}.log")
            except ValueError:
                pass
        return default_name

    file_handler.namer = namer
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # uvicorn --reload's watchfiles logs "N changes detected" at INFO for every filesystem event
    # it sees, including writes to this file handler's own log file inside the watched directory
    # — left at INFO that's a self-sustaining feedback loop (log write -> detected change -> log
    # write -> ...) that fills the log file forever without ever actually triggering a reload.
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def log_duration(label: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logging.info("%s took %.2fs", label, elapsed)
