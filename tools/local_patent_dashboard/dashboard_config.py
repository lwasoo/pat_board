"""Application paths and local runtime configuration."""
from __future__ import annotations

import json
import sys
from pathlib import Path


APP_NAME = "企业专利统计看板"



DEFAULT_HOST = "127.0.0.1"



DEFAULT_PORT = 8765



TASK_NAME = "企业专利统计看板-临期提醒"



MAX_XLSX_SIZE = 10 * 1024 * 1024



def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent



def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir())) / name



def load_config() -> dict:
    path = app_dir() / "local_config.json"
    config = {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "open_browser": True}
    if path.exists():
        try:
            config.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    host = str(config.get("host") or DEFAULT_HOST)
    if host not in {"127.0.0.1", "0.0.0.0"}:
        host = DEFAULT_HOST
    try:
        port = int(config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    return {"host": host, "port": port, "open_browser": bool(config.get("open_browser", True))}



DATA_DIR = app_dir() / "data"



DB_PATH = DATA_DIR / "patents.db"



NOTIFICATION_CONFIG_PATH = DATA_DIR / "notification_config.json"



STATIC_DIR = resource_path("static")


APP_SCRIPT = app_dir() / "local_dashboard.py"
