"""Structured JSON logs, one line per request."""

import json
import logging
import sys

_logger = logging.getLogger("oneclick")

if not _logger.handlers:  # uvicorn reloads import this module more than once
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log_request(trace: dict) -> None:
    """One JSON line per request: greppable in container logs, parseable by the eval harness."""
    _logger.info(json.dumps({"event": "request", **trace}, default=str, separators=(",", ":")))
