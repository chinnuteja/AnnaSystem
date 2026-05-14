from __future__ import annotations

from pathlib import Path
import sys

from fastapi import FastAPI

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.api.routes import router
from app.api.webhook import router as webhook_router


app = FastAPI(title=settings.app_name)
app.include_router(router)
app.include_router(webhook_router)
