"""uvicorn entrypoint: ``uvicorn app.main:app --app-dir backend``."""

from __future__ import annotations

import logging
import os

from app.api.main import create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
