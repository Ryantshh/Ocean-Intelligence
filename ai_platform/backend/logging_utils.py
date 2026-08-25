"""Shared logging setup for the retrieval agent.

Logs to the console and, additionally, to a timestamped file under the repo's
runs/ directory -- one file per process (the file is opened the first time
``get_logger`` is called for a given stage), so every request served in one
``uvicorn`` run lands in the same log instead of scattering one file per
question.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "runs"


def get_logger(stage: str) -> logging.Logger:
    logger = logging.getLogger(stage)
    if logger.handlers:
        return logger  # already configured for this process

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        RUNS_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        file_handler = logging.FileHandler(RUNS_DIR / f"{stage}_{timestamp}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("could not create log file under %s; logging to console only", RUNS_DIR)

    return logger
