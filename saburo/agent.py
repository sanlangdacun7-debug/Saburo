"""サブローの頭脳: Claude API とのやりとり、ツール実行、記憶の管理。"""

import datetime
from collections.abc import Iterator

import anthropic

from . import config, store

client = anthropic.Anthropic()

BETAS = ["server-side-fallback-2026-07-01"]

TOOLS = [
    {
        "name": "remember",
        "description": (
            "オーナーについて後で役立つ情報（好み・目標・予定・決定事項・人間関係など）を"
            "長期記憶に保存する。1回の呼び出しで1つの事実を、簡潔な日本語の1文で保存する。"
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
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
]


def build_system_prompt() -> str:
    persona = config.PERSONA_PATH.read_text(encoding="utf-8").replace("{owner}", config.OWNER_NAME)
    memories = store.list_memories()
    if memories:
        memory_text = "\n".join(f"- [ID {m['id']}] {m['content']}" for m in memories)
    else:
        memory_text = "（まだありません）"
    today = datetime.date.today().isoformat()
    return f"{persona}\n\n## 覚えていること\n{memory_text}\n\n今日の日付: {today}"


def run_tool(name: str, args) -> tuple[str, bool]:
    """クライアント側ツールを実行し、(結果テキスト, エラーかどうか) を返す。"""
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
        if not isinstance(memory_id, int):
            return "memory_id は整数で指定してください。", True
        if store.delete_memory(memory_id):
            return f"ID {memory_id} の記憶を削除しました。", False
        return f"ID {memory_id} の記憶は見つかりませんでした。", True
    return f"未知のツールです: {name}", True


def _dump(content) -> list[dict]:
    return [block.to_dict() for block in content]


def chat(conversation_id: int, user_text: str) -> Iterator[dict]:
    """ユーザーの発言を処理し、画面に流すイベントを順に返す。

    イベント: {"type": "text", "text"} / {"type": "status", "text"} /
             {"type": "error", "text"} / {"type": "done"}
    """
    store.append_message(conversation_id, "user", [{"type": "text", "text": user_text}])
    messages = store.load_messages(conversation_id)
    system = build_system_prompt()
    json_retries = 0

    while True:
        try:
            with client.beta.messages.stream(
                model=config.MODEL,
                max_tokens=64000,
                betas=BETAS,
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": config.EFFORT},
                cache_control={"type": "ephemeral"},
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "text": event.text}
                    elif event.type == "content_block_start":
                        kind = event.content_block.type
                        if kind == "thinking":
                            yield {"type": "status", "text": "考え中…"}
                        elif kind == "server_tool_use":
                            yield {"type": "status", "text": "🔍 Webで調べています…"}
                        elif kind == "tool_use":
                            yield {"type": "status", "text": "📝 記憶を整理しています…"}
                response = stream.get_final_message()
            json_retries = 0
        except ValueError:
            # ツール入力の JSON が壊れていた。同じターンをやり直す（回数制限あり）。
            json_retries += 1
            if json_retries > 2:
                yield {"type": "error", "text": "応答の解析に失敗しました。もう一度送ってください。"}
                return
            continue
        except anthropic.AuthenticationError:
            yield {"type": "error", "text": "API キーが無効です。.env の ANTHROPIC_API_KEY を確認してください。"}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "text": "混み合っています。少し待ってからもう一度送ってください。"}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "text": f"API エラー ({e.status_code}): {e.message}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "text": "ネットワークに接続できませんでした。"}
            return

        if response.stop_reason == "refusal":
            yield {"type": "error", "text": "この内容にはお答えできませんでした。言い方を変えて試してください。"}
            return

        assistant_content = _dump(response.content)

        if response.stop_reason == "pause_turn":
            # Web検索が途中で一時停止した。続きを生成させる。
            messages.append({"role": "assistant", "content": assistant_content})
            store.append_message(conversation_id, "assistant", assistant_content)
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or response.stop_reason != "tool_use":
            store.append_message(conversation_id, "assistant", assistant_content)
            if response.stop_reason == "max_tokens":
                yield {"type": "error", "text": "（長すぎたため途中で切れました）"}
            yield {"type": "done"}
            return

        tool_results = []
        for block in tool_uses:
            result, is_error = run_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
                "is_error": is_error,
            })
        # アシスタントのツール呼び出しと結果は必ずペアで保存する。
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})
        store.append_message(conversation_id, "assistant", assistant_content)
        store.append_message(conversation_id, "user", tool_results)


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
