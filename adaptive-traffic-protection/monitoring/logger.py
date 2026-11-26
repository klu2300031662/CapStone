"""
Structured logging configuration for the Adaptive Traffic Protection Platform.

Provides a consistent log format across all components. Import get_logger()
wherever a named logger is needed.
"""

import logging
import sys
import os


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured logger with the given name.

    Log level is controlled by the LOG_LEVEL environment variable
    (default: INFO). The format includes timestamp, level, logger name,
    and message so log lines are easy to grep in Docker output.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False

    return logger
