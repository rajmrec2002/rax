"""
Standard logging setup.

Replaces the old custom TerminalLogger that hijacked sys.stdout/stderr.
Uses Python's standard logging module with rotating file handler.
"""

import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Optional

LOG_DIR = os.path.expanduser("~/.rax-logic/logs")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3

_DEBUG_MODE = os.environ.get('RAX_DEBUG', '').lower() in ('1', 'true', 'yes')


def setup_logging(level: Optional[int] = None) -> None:
    """
    Configure root logger with console + rotating file handler.

    Parameters
    ----------
    level : int, optional
        Logging level. Defaults to DEBUG in debug mode, INFO otherwise.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(LOG_DIR, f"log_{timestamp}.txt")

    numeric_level = level if level is not None else (
        logging.DEBUG if _DEBUG_MODE else logging.INFO
    )

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Avoid duplicate handlers on repeated calls
    if root.handlers:
        return

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE)

    # Console – use original stdout (not hijacked)
    console = logging.StreamHandler(sys.__stdout__)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File – rotating
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger(__name__).info("Logging to: %s", log_path)


def debug_print(*args, stage: Optional[str] = None, **kwargs) -> None:
    """
    Log at DEBUG level. Drop-in replacement for the old debug_print.

    Parameters
    ----------
    *args : Any
        Values to log.
    stage : str, optional
        If provided, prints a prominent stage header.
    """
    logger = logging.getLogger('rax')
    if not logger.isEnabledFor(logging.DEBUG):
        return

    if stage is not None:
        sep = '=' * 60
        logger.debug('\n%s\nSTAGE: %s\n%s', sep, stage, sep)
    else:
        msg = ' '.join(str(a) for a in args)
        logger.debug(msg)
