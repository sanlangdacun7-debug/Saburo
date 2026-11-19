"""サブローの頭脳: 会話の受け付け、ツール実行、記憶と決定事項の管理。

普段の会話はサブロー1人が答える。「/会議 議題」で投資組織（AI社員チーム）を招集する。
"""

import datetime
from collections.abc import Iterator

from . import config, loop, store
from .finance import team
from .finance.tools import FINANCE_TOOL_NAMES, FINANCE_TOOLS, WEB_FETCH, WEB_SEARCH, run_finance_tool

MEETING_PREFIXES = ("/会議", "/meeting")

OWNER_TOOLS = [
    {
        "name": "remember",
        "description": (
            "オーナーについて後で役立つ情報（好み・目標・予定・リスク許容度・保有方針・人間関係など）を"
            "長期記憶に保存する。1回の呼び出しで1つの事実を、簡潔な日本語の1文で保存する。"
            "投資の決定はここではなく record_decision に記録する。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "保存する事実（1文）"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "forget",
        "description": "古くなった、または間違っている記憶を ID を指定して削除する。",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "削除する記憶の ID"},
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_decision",
        "description": (
            "投資に関するオーナーの決定事項、または未決定事項を台帳に記録する。"
            "status='決定' はオーナーが明確に『決めた』と言ったときだけ使う。"
            "検討中・保留・AIの推し案は status='未決定'。1回に1件、具体的に（銘柄・金額・条件・期限）書く。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "決定・未決定の内容（1文）"},
                "status": {"type": "string", "enum": ["決定", "未決定"]},
            },
            "required": ["content", "status"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_decision",
        "description": (
            "台帳の項目の状態を変える。未決定→決定はオーナーが明確に決めたときだけ。"
            "オーナーが取りやめたら '取消'。note に理由を書く。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["決定", "未決定", "取消"]},
                "note": {"type": "string", "description": "変更の理由"},
            },
            "required": ["decision_id", "status"],
            "additionalProperties": False,
        },
    },
]

TOOLS = [*OWNER_TOOLS, *FINANCE_TOOLS, WEB_SEARCH, WEB_FETCH]


def build_system_prompt() -> str:
    persona = config.PERSONA_PATH.read_text(encoding="utf-8").replace("{owner}", config.OWNER_NAME)
    memories = store.list_memories()
    memory_text = "\n".join(f"- [ID {m['id']}] {m['content']}" for m in memories) or "（まだありません）"
    decisions = store.list_decisions()
    decided = "\n".join(f"- [ID {d['id']}] {d['content']}" for d in decisions if d["status"] == "決定")
    pending = "\n".join(f"- [ID {d['id']}] {d['content']}" for d in decisions if d["status"] == "未決定")
    today = datetime.date.today().isoformat()
    return (
        f"{persona}\n\n## 覚えていること\n{memory_text}\n\n"
        f"## オーナーの投資の決定事項\n{decided or '（なし）'}\n\n"
        f"## 未決定事項\n{pending or '（なし）'}\n\n"
        f"今日の日付: {today}"
    )


def run_tool(name: str, args) -> tuple[str, bool]:
    """クライアント側ツールを実行し、(結果テキスト, エラーかどうか) を返す。"""
    if name in FINANCE_TOOL_NAMES:
        return run_finance_tool(name, args)
    if not isinstance(args, dict):
        return "入力が不正です。", True
    if name == "remember":
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return "content は空でない文字列で指定してください。", True
        memory_id = store.add_memory(content.strip())
        return f"記憶しました (ID {memory_id})。", False
    if name == "forget":
        memory_id = args.get("memory_id")
        if not isinstance(memory_id, int) or isinstance(memory_id, bool):
            return "memory_id は整数で指定してください。", True
        if store.delete_memory(memory_id):
            return f"ID {memory_id} の記憶を削除しました。", False
        return f"ID {memory_id} の記憶は見つかりませんでした。", True
    if name == "record_decision":
        content, status = args.get("content"), args.get("status")
        if not isinstance(content, str) or not content.strip():
            return "content は空でない文字列で指定してください。", True
        if status not in ("決定", "未決定"):
            return "status は '決定' か '未決定' です。", True
        decision_id = store.add_decision(content.strip(), status)
        return f"台帳に記録しました (ID {decision_id}, {status})。", False
    if name == "update_decision":
        decision_id, status = args.get("decision_id"), args.get("status")
        note = args.get("note") or ""
        if not isinstance(decision_id, int) or isinstance(decision_id, bool):
            return "decision_id は整数で指定してください。", True
        if status not in store.DECISION_STATUSES or not isinstance(note, str):
            return "status は '決定' '未決定' '取消' のどれかです。", True
        if store.update_decision(decision_id, status, note):
            return f"ID {decision_id} を「{status}」にしました。", False
        return f"ID {decision_id} は見つかりませんでした。", True
    return f"未知のツールです: {name}", True


def meeting_agenda(text: str) -> str | None:
    """「/会議 議題」なら議題を返す。"""
    for prefix in MEETING_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None


def chat(conversation_id: int, user_text: str) -> Iterator[dict]:
    """ユーザーの発言を処理し、画面に流すイベントを順に返す。

    イベント: {"type": "text", "text"} / {"type": "status", "text"} /
             {"type": "error", "text"} / {"type": "done"}
    """
    agenda = meeting_agenda(user_text)
    if agenda is not None:
        yield from _meeting(conversation_id, user_text, agenda)
        return

    store.append_message(conversation_id, "user", [{"type": "text", "text": user_text}])
    messages = store.load_messages(conversation_id)
    for event in loop.run(
        system=build_system_prompt(),
        messages=messages,
        tools=TOOLS,
        executor=run_tool,
        effort=config.EFFORT,
        persist=lambda role, content: store.append_message(conversation_id, role, content),
    ):
        yield {"type": "done"} if event["type"] == "done" else event


def _meeting(conversation_id: int, user_text: str, agenda: str) -> Iterator[dict]:
    if not agenda:
        # 議題がなければ、直前までの会話の最後の発言を議題にする
        history = visible_history(conversation_id)
        last_user = next((m["text"] for m in reversed(history) if m["role"] == "user"), "")
        agenda = last_user
    if not agenda:
        yield {"type": "error", "text": "議題を書いてください。例: /会議 トヨタ株を100万円分買うか検討したい"}
        return

    store.append_message(conversation_id, "user", [{"type": "text", "text": user_text}])
    shown: list[str] = []
    for event in team.run_meeting(agenda):
        if event["type"] == "text":
            shown.append(event["text"])
        if event["type"] == "error":
            shown.append(f"\n\n⚠️ {event['text']}\n")
        if event["type"] != "done":
            yield event
    # 会議の結果は、画面に出したとおりの全文を会話に残す（次の会話で参照できるように）
    store.append_message(conversation_id, "assistant", [{"type": "text", "text": "".join(shown).strip()}])
    yield {"type": "done"}


def visible_history(conversation_id: int) -> list[dict]:
    """画面表示用に、テキスト部分だけを取り出した履歴を返す。"""
    out = []
    for msg in store.load_messages(conversation_id):
        texts = [b.get("text", "") for b in msg["content"] if b.get("type") == "text"]
        text = "".join(texts).strip()
        if not text:
            continue
        if out and out[-1]["role"] == msg["role"]:
            out[-1]["text"] += "\n\n" + text
        else:
            out.append({"role": msg["role"], "text": text})
    return out


def title_from(text: str) -> str:
    text = " ".join(text.split())
    return text[:30] + ("…" if len(text) > 30 else "")
