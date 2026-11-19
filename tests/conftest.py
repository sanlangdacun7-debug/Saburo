import os
import sys
import tempfile
from pathlib import Path

# テストは本物の API もネットも使わない。データは一時フォルダに置く。
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["SABURO_DATA_DIR"] = tempfile.mkdtemp(prefix="saburo-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
