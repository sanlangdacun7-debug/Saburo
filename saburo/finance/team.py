"""投資会議: 役割ごとに独立した AI 社員を、決まった順番で動かす。

流れ（コードで強制する。AI の気分で省略されない）:
  1. ブリーフ作成（議題・オーナーの決定事項・未決定事項・記憶）… コードで作る
  2. 調査担当3名を並行（ファンダ / マクロ / テクニカル）… 互いの報告は見せない
  3. ポートフォリオ担当とリスク担当を並行 … 互いの報告は見せない
  4. オーダーデスク
  5. CEO が最終案を統合
  6. 監査役が「ブリーフと最終案だけ」を見て独立に検査（作成過程は見せない）
  7. 承認でなければ、CEO が修正し、監査役が再検査（回数は設定で変更可）
監査役の報告は要約も削除もせず、原文のままオーナーに届ける。
"""

import datetime
import queue
import re
import threading
from collections.abc import Iterator

from .. import config, loop, store
from . import roles
from .tools import FINANCE_TOOLS, WEB_FETCH, WEB_SEARCH, run_finance_tool

TEAM_TOOLS = [*FINANCE_TOOLS, WEB_SEARCH, WEB_FETCH]
_VERDICT = re.compile(r"判定\s*[:：]\s*\**\s*(条件付き承認|差し戻し|承認)")


def parse_verdict(audit_text: str) -> str:
    """監査報告の冒頭から判定を読む。読めなければ安全側に倒して「不明」。"""
    m = _VERDICT.search(audit_text[:400])
    return m.group(1) if m else "不明"


def build_brief(agenda: str, today: str) -> str:
    decisions = store.list_decisions()
    decided = [d for d in decisions if d["status"] == "決定"]
    pending = [d for d in decisions if d["status"] == "未決定"]
    memories = store.list_memories()

    def lines(items, key="content"):
        return "\n".join(f"- [ID {i['id']}] {i[key]}" for i in items) or "（なし）"

    return f"""# ブリーフ（{today}）

## 議題
{agenda}

## オーナーの決定事項（覆さないこと）
{lines(decided)}

## 未決定事項（決定済みとして扱わないこと）
{lines(pending)}

## オーナーについて分かっていること
{lines(memories)}

## オーナーの呼び名
{config.OWNER_NAME}
"""


def run_member(role: str, task: str, today: str, *, stream_text: bool = False) -> Iterator[dict]:
    """担当1名を動かす。status にはその担当の名前を付ける。最後に done(text) を返す。"""
    name = roles.ROLES[role]["name"]
    messages = [{"role": "user", "content": task}]
    for event in loop.run(
        system=roles.system_prompt(role, today),
        messages=messages,
        tools=TEAM_TOOLS,
        executor=run_finance_tool,
        effort=config.TEAM_EFFORT or roles.ROLES[role]["effort"],
        stream_text=stream_text,
    ):
        if event["type"] == "status":
            yield {"type": "status", "text": f"{name}: {event['text']}"}
        elif event["type"] == "error":
            yield {"type": "member_error", "role": role, "text": f"{name}: {event['text']}"}
        else:
            yield event


def _parallel(jobs: dict[str, str], today: str) -> Iterator[dict]:
    """複数の担当を同時に動かし、進捗を流す。戻り値は {role: 報告本文}。"""
    events: queue.Queue = queue.Queue()
    results: dict[str, str] = {}

    def worker(role: str, task: str) -> None:
        text, error = "", ""
        try:
            for ev in run_member(role, task, today):
                if ev["type"] == "done":
                    text = ev["text"]
                elif ev["type"] == "member_error":
                    error = ev["text"]
                else:
                    events.put(ev)
        except Exception as e:  # 1人の失敗で会議全体を止めない
            error = f"{roles.ROLES[role]['name']}: 内部エラー {e}"
        results[role] = text or f"（{roles.ROLES[role]['name']}は報告を出せませんでした。{error}）"
        events.put({"type": "status", "text": f"✅ {roles.ROLES[role]['name']}の報告が届きました"})
        events.put(None)

    threads = [threading.Thread(target=worker, args=(r, t), daemon=True) for r, t in jobs.items()]
    for t in threads:
        t.start()
    remaining = len(threads)
    while remaining:
        ev = events.get()
        if ev is None:
            remaining -= 1
        else:
            yield ev
    return results


def _section(title: str, body: str) -> str:
    return f"\n\n## {title}\n\n{body.strip()}\n"


def _reports(results: dict[str, str], order) -> str:
    return "".join(_section(f"{roles.ROLES[r]['name']}の報告", results[r]) for r in order)


def run_meeting(agenda: str) -> Iterator[dict]:
    """投資会議を開く。画面に流すイベントを返す（text / status / error / done）。"""
    today = datetime.date.today().isoformat()
    brief = build_brief(agenda, today)
    record = [f"# 投資会議の記録 {datetime.datetime.now().isoformat(timespec='seconds')}\n", brief]

    yield {"type": "text", "text": "🏢 **投資会議を開きます**（全担当が順番に調べるため、数分かかります）\n\n"}

    # 2. 調査担当（互いの報告は見せない）
    yield {"type": "status", "text": "調査担当3名が並行で調べています…"}
    research = yield from _parallel(
        {r: f"{brief}\n\nあなたの担当分を調べて報告してください。" for r in roles.RESEARCH_ROLES}, today
    )
    research_text = _reports(research, roles.RESEARCH_ROLES)
    record.append(research_text)

    # 3. ポートフォリオとリスク（互いの報告は見せない）
    yield {"type": "status", "text": "ポートフォリオ担当とリスク担当が並行で検討しています…"}
    planning = yield from _parallel(
        {r: f"{brief}\n\n# 調査担当の報告{research_text}\n\nあなたの担当分を検討して報告してください。"
         for r in roles.PLANNING_ROLES},
        today,
    )
    planning_text = _reports(planning, roles.PLANNING_ROLES)
    record.append(planning_text)

    # 4. オーダーデスク
    yield {"type": "status", "text": "オーダーデスクが発注指示書の草案を作っています…"}
    desk = yield from _parallel(
        {"order_desk": f"{brief}\n\n# 調査担当の報告{research_text}\n\n# 運用とリスクの報告{planning_text}\n\n"
                       "発注指示書の草案を作ってください。"},
        today,
    )
    desk_text = _reports(desk, ("order_desk",))
    record.append(desk_text)

    # 5. CEO の最終案（画面にそのまま流す）
    yield {"type": "status", "text": "CEOが最終案をまとめています…"}
    yield {"type": "text", "text": "## 最終案（CEO）\n\n"}
    ceo_task = (f"{brief}\n\n# 調査担当の報告{research_text}\n\n# 運用とリスクの報告{planning_text}\n\n"
                f"# オーダーデスクの草案{desk_text}\n\nオーナーに出す最終案を書いてください。")
    final = yield from _stream_member("ceo", ceo_task, today)
    if not final:
        yield {"type": "error", "text": "CEOが最終案を出せなかったため、会議を中断しました。"}
        return
    record.append(_section("最終案（CEO）", final))

    # 6〜7. 監査（ブリーフと最終案だけを渡す。作成過程は見せない）
    rounds = max(1, config.AUDIT_ROUNDS)
    verdict = "不明"
    for n in range(1, rounds + 1):
        yield {"type": "status", "text": f"監査役が独立に検査しています（{n}回目）…"}
        yield {"type": "text", "text": f"\n\n---\n\n## 監査役の報告（原文・{n}回目）\n\n"}
        audit_task = f"{brief}\n\n# 検査対象の最終案\n\n{final}\n\n検査して報告してください。"
        audit = yield from _stream_member("auditor", audit_task, today)
        if not audit:
            yield {"type": "error", "text": "監査役が報告を出せませんでした。この最終案は監査を通っていません。"}
            verdict = "監査未完了"
            break
        record.append(_section(f"監査役の報告（{n}回目）", audit))
        verdict = parse_verdict(audit)
        if verdict == "承認" or n == rounds:
            break
        # CEO が指摘に対応して修正する（監査報告は上に原文のまま残る）
        yield {"type": "status", "text": "CEOが監査の指摘に対応しています…"}
        yield {"type": "text", "text": "\n\n---\n\n## 監査を受けた修正版（CEO）\n\n"}
        revise_task = (f"{brief}\n\n# あなたの最終案\n\n{final}\n\n# 監査役の報告（原文）\n\n{audit}\n\n"
                       "指摘に対応した修正版を書いてください。")
        revised = yield from _stream_member("reviser", revise_task, today)
        if not revised:
            yield {"type": "error", "text": "修正版を作れませんでした。上の最終案と監査報告をご覧ください。"}
            break
        final = revised
        record.append(_section("監査を受けた修正版（CEO）", final))

    closing = f"\n\n---\n\n**監査の最終判定: {verdict}**\n\n{roles.HONESTY_NOTE}\n"
    path = _save_record(record + [closing])
    if path:
        closing += f"\n（各担当の報告書の全文: `{path}`）\n"
    yield {"type": "text", "text": closing}
    yield {"type": "done", "text": ""}


def _stream_member(role: str, task: str, today: str) -> Iterator[dict]:
    text = ""
    for ev in run_member(role, task, today, stream_text=True):
        if ev["type"] == "done":
            text = ev["text"]
        elif ev["type"] == "member_error":
            yield {"type": "error", "text": ev["text"]}
        else:
            yield ev
    return text


def _save_record(parts: list[str]) -> str | None:
    try:
        folder = config.DATA_DIR / "meetings"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.datetime.now():%Y%m%d-%H%M%S}.md"
        path.write_text("\n".join(parts), encoding="utf-8")
        return str(path.relative_to(config.ROOT)) if path.is_relative_to(config.ROOT) else str(path)
    except OSError:
        return None
