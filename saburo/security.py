"""ネットに公開しても大丈夫なようにするための守り。

- ログインの総当たり対策（同じ相手からの失敗が続いたら一定時間締め出す。全体の失敗が多すぎても締め出す）
- ログインの合言葉（クッキー）に有効期限と署名を付ける。「全端末からログアウト」で一斉に無効化できる
- 公開時に弱いパスワードでは起動させない
"""

import hashlib
import hmac
import secrets
import threading
import time

from . import config

SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30日

# 同じ相手: 15分で5回失敗したら15分締め出す
PER_CLIENT_LIMIT = 5
PER_CLIENT_WINDOW = 15 * 60
# 全体: 1時間で30回失敗したら、全員のログインを1時間止める（大勢から狙われたとき用）
GLOBAL_LIMIT = 30
GLOBAL_WINDOW = 60 * 60

MIN_PUBLIC_PASSWORD_LENGTH = 12
LOCAL_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


class LoginLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        self._global: list[float] = []

    def _prune(self, now: float) -> None:
        self._global = [t for t in self._global if now - t < GLOBAL_WINDOW]
        for key in list(self._fails):
            kept = [t for t in self._fails[key] if now - t < PER_CLIENT_WINDOW]
            if kept:
                self._fails[key] = kept
            else:
                del self._fails[key]

    def retry_after(self, client: str) -> int:
        """締め出し中なら残り秒数、そうでなければ 0。"""
        with self._lock:
            now = self._clock()
            self._prune(now)
            waits = []
            if len(self._global) >= GLOBAL_LIMIT:
                waits.append(GLOBAL_WINDOW - (now - self._global[-GLOBAL_LIMIT]))
            fails = self._fails.get(client, [])
            if len(fails) >= PER_CLIENT_LIMIT:
                waits.append(PER_CLIENT_WINDOW - (now - fails[-PER_CLIENT_LIMIT]))
            return max(0, int(max(waits, default=0)) + (1 if waits else 0))

    def failed(self, client: str) -> None:
        with self._lock:
            now = self._clock()
            self._fails.setdefault(client, []).append(now)
            self._global.append(now)

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


def client_address(peer: str | None, headers) -> str:
    """相手の住所（IP）。Cloudflare Tunnel 経由なら、Cloudflare が付ける本当の相手の IP を使う。

    ヘッダーは偽造できるので、手元の cloudflared（同じパソコン）から来た接続のときだけ信じる。
    """
    peer = peer or "unknown"
    if peer in LOCAL_ADDRESSES:
        forwarded = headers.get("cf-connecting-ip")
        if forwarded:
            return forwarded.strip()
    return peer


def is_https(scheme: str, peer: str | None, headers) -> bool:
    if scheme == "https":
        return True
    return (peer or "") in LOCAL_ADDRESSES and headers.get("x-forwarded-proto", "").lower() == "https"


# --- ログインの合言葉（クッキー） ---

def _secret_path():
    return config.DATA_DIR / "session_secret"


def _server_secret() -> bytes:
    path = _secret_path()
    try:
        return bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError):
        return rotate_secret()


def rotate_secret() -> bytes:
    """署名の鍵を作り直す。これまでのログインはすべて無効になる（全端末からログアウト）。"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path = _secret_path()
    path.write_text(secret.hex())
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


def _sign(issued: int) -> str:
    # パスワードも署名に混ぜる。パスワードを変えると全ログインが無効になる。
    key = _server_secret() + hashlib.sha256(config.PASSWORD.encode()).digest()
    return hmac.new(key, f"saburo-session:{issued}".encode(), hashlib.sha256).hexdigest()


def new_session_token(now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    return f"{issued}.{_sign(issued)}"


def check_session_token(token: str, now: float | None = None) -> bool:
    if not config.PASSWORD or not token or "." not in token:
        return False
    issued_text, signature = token.split(".", 1)
    if not issued_text.isdigit():
        return False
    issued = int(issued_text)
    age = (now if now is not None else time.time()) - issued
    if age < -60 or age > SESSION_MAX_AGE:
        return False
    return hmac.compare_digest(signature, _sign(issued))


def password_problem(public: bool) -> str | None:
    """起動してはいけないほど弱いパスワードなら理由を返す。"""
    password = config.PASSWORD
    if not password:
        return "SABURO_PASSWORD を .env に設定してください。"
    if password in {"change-me", "password", "saburo"}:
        return "SABURO_PASSWORD が初期値のままです。.env で変更してください。"
    if public and len(password) < MIN_PUBLIC_PASSWORD_LENGTH:
        return (
            f"外出先から使う（ネットに公開する）ときは、パスワードを {MIN_PUBLIC_PASSWORD_LENGTH} 文字以上にしてください。"
            "例: 英単語を4つ並べる、または `python -c \"import secrets; print(secrets.token_urlsafe(16))\"` で作る"
        )
    return None
