"""Configuration : variables d'environnement (et fichier .env à la racine)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NICHOIR_DATA_DIR", ROOT / "data"))
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "nichoir.sqlite3"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


CAR_FACTOR = float(get("NICHOIR_CAR_FACTOR", "1.25"))
PORT = int(get("NICHOIR_PORT", "8000"))
USER_AGENT = "LeNichoir/0.1 (agregateur personnel; contact: github.com/ClementDecaux/le-nichoir)"
