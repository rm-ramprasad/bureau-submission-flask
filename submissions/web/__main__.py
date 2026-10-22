"""python -m submissions.web [--host 127.0.0.1] [--port 5000]  (development server)"""

from __future__ import annotations

import argparse
import logging

from submissions.config import Settings
from submissions.logging_config import configure_logging
from submissions.web import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="Bureau submissions UI (development server)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    app = create_app(settings)
    logging.getLogger("submissions.web").info("Downloads are read from %s",
                                              app.extensions["submissions"].store.describe())
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
