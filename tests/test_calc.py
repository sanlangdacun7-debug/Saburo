import pytest

from saburo.finance import calc


@pytest.mark.parametrize("expr, expected", [
    ("1+2*3", 7),
    ("(3150-2980)/2980*100", (3150 - 2980) / 2980 * 100),
    ("1000000*(1+0.05)**5", 1000000 * 1.05 ** 5),
    ("round(1234.5678, 2)", 1234.57),
    ("max([1, 5, 3])", 5),
    ("sqrt(252)", 252 ** 0.5),
    ("-5 + 2", -3),
])
def test_safe_eval(expr, expected):
    assert calc.safe_eval(expr) == pytest.approx(expected)


@pytest.mark.parametrize("expr", [
    "1,234",               # 桁区切り: 黙って (1, 234) にならないこと
    "1,234 * 3",
    "__import__('os')",
    "open('x')",
    "a + 1",
    "(1).__class__",
    "10 ** 10000",
    "1/0",
    "(1,2)+3",
    "[1, 2]",
])
def test_safe_eval_rejects(expr):
    with pytest.raises(calc.CalcError):
        calc.safe_eval(expr)


def test_position_size_by_risk():
    # 値幅 100円、許容損失 5万円 → 500株
    r = calc.position_size(entry_price=3000, stop_price=2900, max_loss=50_000)
    assert r["数量"] == 500
    assert r["損切り時の損失額_手数料込"] == 50_000
    assert r["数量を決めた制約"] == "許容損失から"


def test_position_size_rounds_down_to_lot_and_respects_budget():
    r = calc.position_size(entry_price=3000, stop_price=2900, max_loss=58_000, budget=1_000_000)
    # 許容損失からは 580 → 500株、予算からは 333 → 300株
    assert r["各制約での上限数量"] == {"許容損失から": 500, "予算から": 300}
    assert r["数量"] == 300
    assert r["数量を決めた制約"] == "予算から"
    assert r["買付金額_手数料込"] <= 1_000_000


def test_position_size_fees_never_exceed_max_loss():
    r = calc.position_size(entry_price=150.0, stop_price=140.0, max_loss=1000, lot_size=1, fee_rate=0.005)
    assert r["損切り時の損失額_手数料込"] <= 1000
    # 1株あたり損失 = 値幅10 + 手数料 0.005*(150+140) = 11.45 → 1000/11.45 = 87.3 → 87株
    assert r["数量"] == 87


def test_position_size_zero_warns():
    r = calc.position_size(entry_price=3000, stop_price=2000, max_loss=10_000)
    assert r["数量"] == 0 and "注意" in r


@pytest.mark.parametrize("kwargs", [
    dict(entry_price=100, stop_price=110, max_loss=1000),
    dict(entry_price=100, stop_price=90, max_loss=0),
    dict(entry_price=-1, stop_price=90, max_loss=1000),
    dict(entry_price=100, stop_price=90, max_loss=1000, lot_size=0),
])
def test_position_size_rejects(kwargs):
    with pytest.raises(calc.CalcError):
        calc.position_size(**kwargs)


def test_price_stats():
    closes = [100, 110, 99, 120, 108] + [100 + i for i in range(200)]
    s = calc.price_stats(closes)
    assert s["直近終値"] == 299
    assert s["移動平均_5日"] == pytest.approx(sum(closes[-5:]) / 5)
    assert s["移動平均_200日"] == pytest.approx(sum(closes[-200:]) / 200)
    assert s["騰落率_5日_%"] == round((299 / closes[-6] - 1) * 100, 2)
    # 120 → 99... の下落はなく、最大下落は 120 → 100 = -16.67%
    assert s["期間中の最大下落率_%"] == pytest.approx(-16.67, abs=0.01)
    assert s["期間高値"] == 299 and s["期間安値"] == 99


def test_price_stats_short_series_has_none_for_long_sma():
    s = calc.price_stats([10, 11, 12])
    assert s["移動平均_200日"] is None and s["騰落率_20日_%"] is None
