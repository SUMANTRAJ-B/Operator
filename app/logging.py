"""Structured logging system for Operator."""

import logging
import sys
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler

from app.config import get_settings


_logger: Optional[logging.Logger] = None


def setup_logger(
    name: str = "operator",
    level: Optional[str] = None,
    log_to_file: Optional[bool] = None,
    log_dir: Optional[Path] = None,
) -> logging.Logger:
    """Initialize and return configured structured logger.

    Uses Rich for colored terminal output and optionally writes formatted logs to disk.
    """
    global _logger
    settings = get_settings()

    log_level = (level or settings.log_level).upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(numeric_level)
    logger.handlers.clear()

    # Rich Console Handler
    console = Console(stderr=True)
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        markup=True,
    )
    rich_handler.setLevel(numeric_level)
    logger.addHandler(rich_handler)

    # Optional File Handler
    should_log_to_file = settings.log_to_file if log_to_file is None else log_to_file
    if should_log_to_file:
        target_dir = log_dir or settings.log_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "operator.log"

        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(filename)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(numeric_level)
        logger.addHandler(file_handler)

    logger.propagate = False
    _logger = logger
    return logger


def get_logger(name: str = "operator") -> logging.Logger:
    """Retrieve the logger, configuring it with defaults if not already setup."""
    global _logger
    if _logger is None:
        return setup_logger(name=name)
    if name != "operator":
        return logging.getLogger(f"operator.{name}")
    return _logger
