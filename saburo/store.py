"""会話履歴と長期記憶を SQLite に保存する。"""

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- 会話 ---

def create_conversation(title: str) -> int:
    now = time.time()
    with connect() as db:
        cur = db.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return cur.lastrowid


def list_conversations() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def conversation_exists(conversation_id: int) -> bool:
    with connect() as db:
        return db.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone() is not None


def delete_conversation(conversation_id: int) -> None:
    with connect() as db:
        db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def append_message(conversation_id: int, role: str, content) -> None:
    now = time.time()
    with connect() as db:
        db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, json.dumps(content, ensure_ascii=False), now),
        )
        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )


def load_messages(conversation_id: int) -> list[dict]:
    """API にそのまま渡せる形 ({role, content}) で履歴を返す。"""
    with connect() as db:
        rows = db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


# --- 長期記憶 ---

def add_memory(content: str) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO memories (content, created_at) VALUES (?, ?)", (content, time.time())
        )
        return cur.lastrowid


def delete_memory(memory_id: int) -> bool:
    with connect() as db:
        return db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0


def list_memories() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id, content, created_at FROM memories ORDER BY id").fetchall()
        return [dict(r) for r in rows]
