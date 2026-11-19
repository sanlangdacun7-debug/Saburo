"""Web 画面。パスワードでログインしたあなただけが使える。

起動: python -m saburo.server  →  http://localhost:8000
"""

import os
import secrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import agent, config, jobs, security, store

STATIC = config.ROOT / "saburo" / "static"
COOKIE = "saburo_session"

# 公開時に中身を知られないよう、API の説明ページ（/docs など）は出さない。
app = FastAPI(title="Saburo", docs_url=None, redoc_url=None, openapi_url=None)
limiter = security.LoginLimiter()
runner = jobs.JobRunner()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if security.is_https(request.url.scheme, _peer(request), request.headers):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def _peer(request: Request) -> str | None:
    return request.client.host if request.client else None


def require_login(request: Request) -> None:
    if not security.check_session_token(request.cookies.get(COOKIE, "")):
        raise HTTPException(status_code=401, detail="ログインしてください")


class LoginBody(BaseModel):
    password: str


class ChatBody(BaseModel):
    message: str
    conversation_id: int | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    if not config.PASSWORD:
        raise HTTPException(500, "SABURO_PASSWORD が設定されていません")
    client = security.client_address(_peer(request), request.headers)
    wait = limiter.retry_after(client)
    if wait:
        minutes = (wait + 59) // 60
        raise HTTPException(429, f"ログインの失敗が続いたため、約{minutes}分間ログインできません")
    if not secrets.compare_digest(body.password.encode(), config.PASSWORD.encode()):
        limiter.failed(client)
        time.sleep(1)  # 総当たりを遅くする
        raise HTTPException(401, "パスワードが違います")
    limiter.succeeded(client)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, security.new_session_token(), httponly=True, samesite="strict",
        secure=security.is_https(request.url.scheme, _peer(request), request.headers),
        max_age=security.SESSION_MAX_AGE,
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/api/logout-all")
def logout_all(request: Request):
    """スマホをなくしたときなど、すべての端末のログインを無効にする。"""
    require_login(request)
    security.rotate_secret()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(request: Request):
    require_login(request)
    return {"owner": config.OWNER_NAME}


@app.get("/api/conversations")
def conversations(request: Request):
    require_login(request)
    return store.list_conversations()


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: int, request: Request):
    require_login(request)
    if not store.conversation_exists(conversation_id):
        raise HTTPException(404, "会話が見つかりません")
    return agent.visible_history(conversation_id)


@app.delete("/api/conversations/{conversation_id}")
def remove_conversation(conversation_id: int, request: Request):
    require_login(request)
    store.delete_conversation(conversation_id)
    return {"ok": True}


@app.get("/api/memories")
def memories(request: Request):
    require_login(request)
    return store.list_memories()


@app.delete("/api/memories/{memory_id}")
def remove_memory(memory_id: int, request: Request):
    require_login(request)
    store.delete_memory(memory_id)
    return {"ok": True}


@app.get("/api/decisions")
def decisions(request: Request):
    require_login(request)
    return store.list_decisions()


@app.delete("/api/decisions/{decision_id}")
def remove_decision(decision_id: int, request: Request):
    require_login(request)
    store.delete_decision(decision_id)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatBody, request: Request):
    """返答づくりを裏側で始め、途中経過を取りに来るための番号（job_id）を返す。"""
    require_login(request)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "メッセージが空です")
    conversation_id = body.conversation_id
    if conversation_id is None or not store.conversation_exists(conversation_id):
        conversation_id = store.create_conversation(agent.title_from(text))
    try:
        job = runner.start(conversation_id, lambda: agent.chat(conversation_id, text))
    except jobs.Busy:
        raise HTTPException(409, "この会話はまだ返答を作っています。終わるまでお待ちください") from None
    return {"job_id": job.id, "conversation_id": conversation_id}


@app.get("/api/jobs/{job_id}")
def job_events(job_id: str, request: Request, after: int = 0):
    """after 番目以降の途中経過を返す。新しいものがなければ最大20秒待ってから返す。"""
    require_login(request)
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(404, "処理が見つかりません（サーバーが再起動した可能性があります）")
    return job.read(max(0, after), wait=20)


@app.get("/api/conversations/{conversation_id}/job")
def running_job(conversation_id: int, request: Request):
    """この会話で作成中の返答があれば、その job_id を返す（画面を開き直したとき用）。"""
    require_login(request)
    job = runner.active_for(conversation_id)
    return {"job_id": job.id if job else None}


def check_startup(public: bool) -> None:
    problem = security.password_problem(public=public)
    if problem:
        raise SystemExit(problem)


def main():
    import uvicorn

    host = os.environ.get("SABURO_HOST", "127.0.0.1")
    port = int(os.environ.get("SABURO_PORT", "8000"))
    check_startup(public=host not in security.LOCAL_ADDRESSES)
    print(f"サブロー起動中: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
