"""Logging configuration for mybot."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from mybot.utils.config import Config


def setup_logging(config: Config, *, console_output: bool = False) -> None:
    """Configure mybot loggers (file + optional console)."""
    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(format_str)
    console_formatter = logging.Formatter("%(levelname)s - %(name)s - %(message)s")

    root_logger = logging.getLogger("mybot")
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    config.logging_path.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.logging_path / "mybot.log",
        maxBytes=256 * 1024 * 128,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    if console_output:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)
