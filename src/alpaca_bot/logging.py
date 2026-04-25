from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with a StreamHandler.

    Idempotent: does nothing if handlers are already configured on the root logger.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given name."""
    return logging.getLogger(name)
