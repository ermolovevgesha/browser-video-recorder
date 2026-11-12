from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from db import init_db
from web_service import UPLOAD_FOLDER, app as application

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
init_db()
