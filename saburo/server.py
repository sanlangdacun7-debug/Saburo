"""Web 画面。パスワードでログインしたあなただけが使える。

起動: python -m saburo.server  →  http://localhost:8000
"""

import hashlib
import hmac
import json
import os
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, config, store

STATIC = config.ROOT / "saburo" / "static"
COOKIE = "saburo_session"

app = FastAPI(title="Saburo")


def _session_token() -> str:
    # パスワードから導いたトークン。パスワードを変えると全ログインが無効になる。
    return hmac.new(config.PASSWORD.encode(), b"saburo-session", hashlib.sha256).hexdigest()


def require_login(request: Request) -> None:
    token = request.cookies.get(COOKIE, "")
    if not config.PASSWORD or not hmac.compare_digest(token, _session_token()):
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
    if not secrets.compare_digest(body.password.encode(), config.PASSWORD.encode()):
        raise HTTPException(401, "パスワードが違います")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, _session_token(), httponly=True, samesite="strict",
        secure=request.url.scheme == "https", max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.post("/api/logout")
def logout():
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


@app.post("/api/chat")
def chat(body: ChatBody, request: Request):
    require_login(request)
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "メッセージが空です")
    conversation_id = body.conversation_id
    if conversation_id is None or not store.conversation_exists(conversation_id):
        conversation_id = store.create_conversation(agent.title_from(text))

    def events():
        yield _sse({"type": "conversation", "id": conversation_id})
        try:
            for event in agent.chat(conversation_id, text):
                yield _sse(event)
        except Exception as e:  # 想定外のエラーも画面に出す
            yield _sse({"type": "error", "text": f"内部エラー: {e}"})

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def main():
    import uvicorn

    if not config.PASSWORD:
        raise SystemExit("SABURO_PASSWORD を .env に設定してください。")
    host = os.environ.get("SABURO_HOST", "127.0.0.1")
    port = int(os.environ.get("SABURO_PORT", "8000"))
    print(f"サブロー起動中: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
