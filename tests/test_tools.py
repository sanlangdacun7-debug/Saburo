import json

from saburo import agent, store
from saburo.finance.tools import run_finance_tool


def test_calculate_tool_returns_result():
    text, err = run_finance_tool("calculate", {"expression": "100*1.1", "label": "x"})
    assert not err and json.loads(text)["結果"] == 110.00000000000001


def test_tool_errors_tell_model_not_to_guess():
    text, err = run_finance_tool("calculate", {"expression": "1,234", "label": "x"})
    assert err and "未確認" in text
    text, err = run_finance_tool("position_size", {"entry_price": "3000", "stop_price": 2900, "max_loss": 1})
    assert err
    text, err = run_finance_tool("position_size", {"entry_price": 3000, "stop_price": 2900, "max_loss": 1, "lot_size": True})
    assert err


def test_decision_ledger_tools():
    _, err = agent.run_tool("record_decision", {"content": "トヨタは買わない", "status": "決定"})
    assert not err
    _, err = agent.run_tool("record_decision", {"content": "NISA枠の配分", "status": "たぶん"})
    assert err
    d = store.list_decisions()[-1]
    _, err = agent.run_tool("update_decision", {"decision_id": d["id"], "status": "取消", "note": "撤回"})
    assert not err
    assert all(x["id"] != d["id"] for x in store.list_decisions())
    assert any(x["id"] == d["id"] for x in store.list_decisions(include_cancelled=True))


def test_system_prompt_lists_decisions_separately():
    store.add_decision("米国ETFを月3万円積み立てるか", "未決定")
    prompt = agent.build_system_prompt()
    decided, pending = prompt.split("## 未決定事項")
    assert "米国ETFを月3万円" in pending and "米国ETFを月3万円" not in decided


def test_meeting_prefix():
    assert agent.meeting_agenda("/会議 トヨタを検討") == "トヨタを検討"
    assert agent.meeting_agenda("/会議") == ""
    assert agent.meeting_agenda("会議しよう") is None
