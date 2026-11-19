"""Claude とのやりとりの共通ループ。メインのサブローも、各担当エージェントもこれを使う。"""

from collections.abc import Callable, Iterator

import anthropic

from . import config

client = anthropic.Anthropic()

BETAS = ["server-side-fallback-2026-07-01"]

Executor = Callable[[str, object], tuple[str, bool]]
Persist = Callable[[str, list], None]

STATUS_BY_TOOL = {
    "remember": "📝 記憶を整理しています…",
    "forget": "📝 記憶を整理しています…",
    "record_decision": "📒 決定事項を記録しています…",
    "update_decision": "📒 決定事項を更新しています…",
    "calculate": "🧮 計算しています…",
    "position_size": "🧮 発注数量を計算しています…",
    "get_quote": "📈 株価を取得しています…",
    "get_price_history": "📈 株価の推移を取得しています…",
}


def _dump(content) -> list[dict]:
    return [block.to_dict() for block in content]


def run(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    executor: Executor,
    effort: str,
    persist: Persist | None = None,
    stream_text: bool = True,
) -> Iterator[dict]:
    """エージェントループを回し、イベントを順に返す。

    イベント: {"type": "text", "text"} / {"type": "status", "text"} /
             {"type": "error", "text"} / {"type": "done", "text": 最終回答の全文}
    messages はこの関数の中で追記される（呼び出し側で保存したいときは persist を渡す）。
    """

    def keep(role: str, content: list) -> None:
        messages.append({"role": role, "content": content})
        if persist:
            persist(role, content)

    json_retries = 0
    final_text: list[str] = []

    while True:
        try:
            with client.beta.messages.stream(
                model=config.MODEL,
                max_tokens=64000,
                betas=BETAS,
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
                system=system,
                tools=tools,
                messages=messages,
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        if stream_text:
                            yield {"type": "text", "text": event.text}
                    elif event.type == "content_block_start":
                        kind = event.content_block.type
                        if kind == "thinking":
                            yield {"type": "status", "text": "考え中…"}
                        elif kind == "server_tool_use":
                            name = getattr(event.content_block, "name", "")
                            label = "📄 資料を読んでいます…" if name == "web_fetch" else "🔍 Webで調べています…"
                            yield {"type": "status", "text": label}
                        elif kind == "tool_use":
                            name = event.content_block.name
                            yield {"type": "status", "text": STATUS_BY_TOOL.get(name, "🔧 作業中…")}
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

        content = _dump(response.content)
        texts = [b.text for b in response.content if b.type == "text"]

        if response.stop_reason == "pause_turn":
            # サーバー側ツール（Web検索など）が途中で一時停止した。続きを生成させる。
            final_text.extend(texts)
            keep("assistant", content)
            continue

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or response.stop_reason != "tool_use":
            final_text.extend(texts)
            keep("assistant", content)
            if response.stop_reason == "max_tokens":
                yield {"type": "error", "text": "（長すぎたため途中で切れました）"}
            yield {"type": "done", "text": "".join(final_text).strip()}
            return

        # ツールを呼ぶ前の前置きは最終回答に含めない。
        final_text.clear()
        tool_results = []
        for block in tool_uses:
            try:
                result, is_error = executor(block.name, block.input)
            except Exception as e:  # ツールの不具合でループ全体を止めない
                result, is_error = f"ツール実行中にエラー: {e}", True
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
                "is_error": is_error,
            })
        # アシスタントのツール呼び出しと結果は必ずペアで保存する。
        keep("assistant", content)
        keep("user", tool_results)
