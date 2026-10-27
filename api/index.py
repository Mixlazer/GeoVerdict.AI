from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/geoverdict.db")
os.environ.setdefault("ENVIRONMENT", "prod")

from app.main import app as app  # noqa: E402
