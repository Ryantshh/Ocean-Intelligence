"""Shared logging setup for the gold loader, kept intact in the Lambda zip
(see infra/smu_gold_loader.yaml's build step) so it deploys alongside the
rest of scripts/gold_loader/ without any extra packaging step.

Logs to the console only -- in Lambda that's captured by CloudWatch for free,
and for local runs it keeps the repo's runs/ directory reserved for the
agent's logs (see ai_platform/backend/logging_utils.py) instead of being
interleaved with gold-loader output.
"""

import logging
import sys


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

    return logger
