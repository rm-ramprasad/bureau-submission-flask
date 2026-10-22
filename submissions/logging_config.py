"""One logging setup shared by the web app, the worker and the CLI."""

from __future__ import annotations

import logging

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=FORMAT)
