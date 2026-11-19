"""外出先から使うための起動コマンド: サブロー ＋ Cloudflare Tunnel をまとめて動かす。

起動: python -m saburo.remote

- .env に SABURO_TUNNEL_TOKEN がなければ「クイックトンネル」で起動する。
  アカウント不要ですぐ使えるが、URL は起動のたびに変わる。
- SABURO_TUNNEL_TOKEN（Cloudflare の画面で作った名前付きトンネルのトークン）があれば、
  毎回同じ URL（SABURO_PUBLIC_URL）で使える。
- 動いている間は、パソコンがスリープしないようにする（Mac / Windows）。
"""

import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading

from . import config, server

QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

INSTALL_HELP = """\
cloudflared（Cloudflare Tunnel のプログラム）が見つかりません。次の方法で入れてから、もう一度実行してください。

  Mac:     brew install cloudflared
           （Homebrew がない場合は https://brew.sh の1行を先に実行）
  Windows: winget install --id Cloudflare.cloudflared
           （入れたあと、PowerShell を開き直してください）

ほかの方法: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
"""


def _port() -> int:
    return int(os.environ.get("SABURO_PORT", "8000"))


def _tunnel_command(cloudflared: str) -> list[str]:
    token = os.environ.get("SABURO_TUNNEL_TOKEN", "").strip()
    if token:
        # トークンはコマンドラインではなく環境変数で渡す（他のプログラムから見えにくくする）
        return [cloudflared, "tunnel", "--no-autoupdate", "run"]
    return [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{_port()}"]


def _announce(url: str) -> None:
    line = "=" * 64
    print(f"\n{line}\n  外出先からはこの URL を開いてください:\n\n    {url}\n\n"
          f"  （スマホのブラウザでブックマークしておくと便利です）\n{line}\n", flush=True)
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "public_url.txt").write_text(url + "\n", encoding="utf-8")
    except OSError:
        pass


def _watch_tunnel(proc: subprocess.Popen, quick: bool) -> None:
    """cloudflared の出力から URL を見つけて表示する。エラーらしき行はそのまま見せる。"""
    announced = False
    for raw in proc.stdout:
        line = raw.rstrip()
        if quick and not announced:
            m = QUICK_URL.search(line)
            if m:
                _announce(m.group(0))
                announced = True
                continue
        if " ERR " in line or "error" in line.lower():
            print(f"[cloudflared] {line}", flush=True)
        elif "Registered tunnel connection" in line:
            print("[cloudflared] Cloudflare とつながりました。", flush=True)
    print("[cloudflared] トンネルが止まりました。サブローも止めて、もう一度起動してください。", flush=True)


def _keep_awake(pid: int) -> subprocess.Popen | None:
    """パソコンがスリープすると外から使えなくなるため、動いている間はスリープを防ぐ。"""
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        return subprocess.Popen(["caffeinate", "-i", "-w", str(pid)])
    if sys.platform == "win32":
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    return None


def main():
    import uvicorn

    server.check_startup(public=True)
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise SystemExit(INSTALL_HELP)

    token = os.environ.get("SABURO_TUNNEL_TOKEN", "").strip()
    env = dict(os.environ)
    if token:
        env["TUNNEL_TOKEN"] = token
    proc = subprocess.Popen(
        _tunnel_command(cloudflared), env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    threading.Thread(target=_watch_tunnel, args=(proc, not token), daemon=True).start()
    awake = _keep_awake(os.getpid())

    if token:
        url = os.environ.get("SABURO_PUBLIC_URL", "").strip()
        _announce(url or "（Cloudflare の画面で設定した URL）")
    else:
        print("クイックトンネルを準備しています（URL は起動のたびに変わります）…", flush=True)
    print("止めるときは Ctrl + C を押してください。", flush=True)

    try:
        # 外からの接続は cloudflared 経由だけにするため、サーバーは自分のパソコン内（127.0.0.1）でだけ待つ
        uvicorn.run(server.app, host="127.0.0.1", port=_port(), log_level="warning")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if awake:
            awake.terminate()


if __name__ == "__main__":
    main()
