"""Shared logging setup for the gold loader, kept intact in the Lambda zip
(see infra/smu_gold_loader.yaml's build step) so it deploys alongside the
rest of scripts/gold_loader/ without any extra packaging step.

Always logs to the console -- in Lambda that's captured by CloudWatch for
free. Additionally logs to a timestamped file under the repo's runs/
directory, but only for local runs: the deployed zip has no repo checked
out and Lambda's filesystem is read-only outside /tmp, so the file handler
is skipped there rather than failing.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "runs"


def get_logger(stage: str) -> logging.Logger:
    logger = logging.getLogger(stage)
    if logger.handlers:
        return logger  # already configured, e.g. handler() invoked twice in one process

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        try:
            RUNS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            file_handler = logging.FileHandler(RUNS_DIR / f"{stage}_{timestamp}.log", encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            logger.warning("could not create log file under %s; logging to console only", RUNS_DIR)

    return logger
