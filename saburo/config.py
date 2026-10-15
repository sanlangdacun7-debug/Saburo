"""環境変数と .env から設定を読み込む。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

MODEL = os.environ.get("SABURO_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("SABURO_EFFORT", "medium")
OWNER_NAME = os.environ.get("SABURO_OWNER_NAME", "オーナー")
PASSWORD = os.environ.get("SABURO_PASSWORD", "")
DATA_DIR = Path(os.environ.get("SABURO_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "saburo.db"
PERSONA_PATH = Path(os.environ.get("SABURO_PERSONA_FILE", ROOT / "persona.md"))
