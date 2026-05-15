"""Logging setup shared by scripts and future layers."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import PROJECT_ROOT, ensure_project_path

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging(
    name: str | None = None,
    *,
    level: str | int | None = None,
    log_file: str | os.PathLike[str] | None = "output/run.log",
    console: bool = True,
) -> logging.Logger:
    """Configure and return a logger.

    Repeated calls are idempotent for the selected logger: existing handlers are
    removed before new file/console handlers are attached.
    """

    logger = logging.getLogger(name)
    resolved_level = level or os.getenv("LOG_LEVEL", "INFO")
    logger.setLevel(resolved_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if log_file:
        file_path = ensure_project_path(log_file, PROJECT_ROOT)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
