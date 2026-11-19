"""投資会議の段取りが崩れないことを、AI を呼ばずに確かめる。"""

from saburo import config, loop
from saburo.finance import roles, team


class FakeLoop:
    """loop.run の代わり。誰に何を渡したかを記録し、決まった報告を返す。"""

    def __init__(self, audits):
        self.calls = []
        self.audits = list(audits)

    def __call__(self, *, system, messages, tools, executor, effort, persist=None, stream_text=True):
        role = next(r for r, spec in roles.ROLES.items() if system.startswith(spec["prompt"]))
        task = messages[0]["content"]
        self.calls.append((role, task))
        if role == "auditor":
            text = self.audits.pop(0)
        else:
            text = f"<<{role}の報告>>"
        yield {"type": "status", "text": "考え中…"}
        if stream_text:
            yield {"type": "text", "text": text}
        yield {"type": "done", "text": text}


def run(monkeypatch, audits, rounds=2):
    fake = FakeLoop(audits)
    monkeypatch.setattr(loop, "run", fake)
    monkeypatch.setattr(config, "AUDIT_ROUNDS", rounds)
    events = list(team.run_meeting("トヨタ株を検討"))
    shown = "".join(e["text"] for e in events if e["type"] == "text")
    return fake, shown, events


def tasks_of(fake, role):
    return [t for r, t in fake.calls if r == role]


def test_information_barriers(monkeypatch):
    fake, _, _ = run(monkeypatch, ["判定: 承認\n問題なし"])
    for r in roles.RESEARCH_ROLES:
        (task,) = tasks_of(fake, r)
        assert "の報告>>" not in task  # 調査担当は誰の報告も見ない
    (pf,) = tasks_of(fake, "portfolio")
    (rk,) = tasks_of(fake, "risk")
    assert "<<fundamentals の報告>>".replace(" ", "") in pf
    assert "<<risk" not in pf and "<<portfolio" not in rk
    (audit_task,) = tasks_of(fake, "auditor")
    # 監査役はブリーフと最終案だけ。作成過程（各担当の報告）は見ない。
    assert "<<ceoの報告>>" in audit_task
    for r in (*roles.RESEARCH_ROLES, *roles.PLANNING_ROLES, "order_desk"):
        assert f"<<{r}の報告>>" not in audit_task
    assert "トヨタ株を検討" in audit_task


def test_order_of_stages(monkeypatch):
    fake, _, _ = run(monkeypatch, ["判定: 承認"])
    order = [r for r, _ in fake.calls]
    assert set(order[:3]) == set(roles.RESEARCH_ROLES)
    assert set(order[3:5]) == set(roles.PLANNING_ROLES)
    assert order[5:] == ["order_desk", "ceo", "auditor"]


def test_audit_shown_verbatim_and_revision_loop(monkeypatch):
    first = "判定: 差し戻し\n| 高 | 数量 | 単元未満 |"
    fake, shown, _ = run(monkeypatch, [first, "判定：条件付き承認\n軽微"], rounds=2)
    assert first in shown  # 監査報告は原文のまま
    assert [r for r, _ in fake.calls][-3:] == ["auditor", "reviser", "auditor"]
    (revise_task,) = tasks_of(fake, "reviser")
    assert first in revise_task
    # 再監査は修正版を検査する
    assert "<<reviserの報告>>" in tasks_of(fake, "auditor")[1]
    assert "監査の最終判定: 条件付き承認" in shown
    assert roles.HONESTY_NOTE in shown


def test_single_audit_round_does_not_revise(monkeypatch):
    fake, shown, _ = run(monkeypatch, ["判定: 差し戻し"], rounds=1)
    assert "reviser" not in [r for r, _ in fake.calls]
    assert "監査の最終判定: 差し戻し" in shown


def test_parse_verdict():
    assert team.parse_verdict("判定: 承認") == "承認"
    assert team.parse_verdict("**判定：** 条件付き承認") == "条件付き承認"
    assert team.parse_verdict("判定: 差し戻し") == "差し戻し"
    assert team.parse_verdict("問題なさそうです") == "不明"
