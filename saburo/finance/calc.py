"""計算はAIの暗算に任せず、ここでコードとして実行する。

- safe_eval: 四則演算・べき乗・関数だけを許す安全な電卓
- position_size: 許容損失から発注数量を決める
- price_stats: 終値の列から移動平均・騰落率・最大下落率・ボラティリティを出す
"""

import ast
import math
import operator

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "pow": pow,
}
_CONSTS = {"pi": math.pi, "e": math.e}


class CalcError(ValueError):
    pass


def safe_eval(expression: str) -> float | int:
    """数式を安全に評価する。変数・属性・import などは使えない。"""
    if len(expression) > 2000:
        raise CalcError("式が長すぎます（2000文字まで）。")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"式を読み取れません: {e.msg}") from None
    if isinstance(tree.body, ast.Tuple):
        # 「1,234」は Python では (1, 234) になり、黙って誤った値になるため拒否する。
        raise CalcError("桁区切りのカンマは使えません。1234567 のように書いてください。")
    result = _eval(tree.body)
    if isinstance(result, list):
        raise CalcError("結果が数値になりません。リストは min/max/sum などの引数にだけ使えます。")
    return result


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise CalcError("指数が大きすぎます。")
        try:
            return _BIN[type(node.op)](left, right)
        except ZeroDivisionError:
            raise CalcError("0 で割っています。") from None
        except TypeError:
            raise CalcError("数値どうしの計算になっていません（桁区切りのカンマなど）。") from None
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        if node.keywords:
            raise CalcError("キーワード引数は使えません。")
        args = [_eval(a) for a in node.args]
        try:
            return _FUNCS[node.func.id](*args)
        except (TypeError, ValueError) as e:
            raise CalcError(f"{node.func.id} の使い方が正しくありません: {e}") from None
    raise CalcError(
        "使えない要素が含まれています。数値、+ - * / // % **、括弧、"
        f"関数 {', '.join(sorted(_FUNCS))}、定数 pi, e のみ使えます。"
    )


def position_size(
    *,
    entry_price: float,
    stop_price: float,
    max_loss: float,
    lot_size: int = 100,
    budget: float | None = None,
    fee_rate: float = 0.0,
) -> dict:
    """許容損失（損切り時の最大損失額）から、買える数量を単元に合わせて切り捨てで出す。"""
    if entry_price <= 0 or stop_price <= 0:
        raise CalcError("価格は正の数で指定してください。")
    if stop_price >= entry_price:
        raise CalcError("買いの損切り価格は買値より低くしてください。")
    if max_loss <= 0:
        raise CalcError("許容損失は正の数で指定してください。")
    if lot_size < 1:
        raise CalcError("単元株数は1以上で指定してください。")
    if not 0 <= fee_rate < 0.1:
        raise CalcError("手数料率は 0〜0.1（10%未満）で指定してください。")

    # 1株あたりの損失 = 値幅 + 買いと売りの手数料
    loss_per_share = (entry_price - stop_price) + fee_rate * (entry_price + stop_price)
    by_risk = math.floor(max_loss / loss_per_share / lot_size) * lot_size
    limits = {"許容損失から": by_risk}
    if budget is not None:
        cost_per_share = entry_price * (1 + fee_rate)
        limits["予算から"] = math.floor(budget / cost_per_share / lot_size) * lot_size
    shares = max(0, min(limits.values()))
    binding = min(limits, key=limits.get)

    result = {
        "数量": shares,
        "数量を決めた制約": binding,
        "各制約での上限数量": limits,
        "単元株数": lot_size,
        "損切りまでの下落率_%": round((entry_price - stop_price) / entry_price * 100, 3),
        "1株あたり損失_手数料込": round(loss_per_share, 4),
        "買付金額_手数料込": round(shares * entry_price * (1 + fee_rate), 2),
        "損切り時の損失額_手数料込": round(shares * loss_per_share, 2),
    }
    if shares == 0:
        result["注意"] = "条件内では1単元も買えません。許容損失・予算・損切り幅を見直してください。"
    return result


def sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def price_stats(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> dict:
    """日足終値の列（古い順）から、よく使う指標をまとめて計算する。"""
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        raise CalcError("終値が2本以上必要です。")
    last = closes[-1]

    def ret(days: int):
        if len(closes) <= days:
            return None
        return round((last / closes[-1 - days] - 1) * 100, 2)

    peak, max_dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        max_dd = min(max_dd, c / peak - 1)

    daily = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(daily) / len(daily)
    var = sum((r - mean) ** 2 for r in daily) / (len(daily) - 1) if len(daily) > 1 else 0.0
    hi = max(h for h in (highs or closes) if h is not None)
    lo = min(v for v in (lows or closes) if v is not None)

    out = {
        "直近終値": last,
        "本数": len(closes),
        "期間高値": hi,
        "期間安値": lo,
        "高値からの下落率_%": round((last / hi - 1) * 100, 2),
        "安値からの上昇率_%": round((last / lo - 1) * 100, 2),
        "騰落率_5日_%": ret(5),
        "騰落率_20日_%": ret(20),
        "騰落率_60日_%": ret(60),
        "騰落率_期間_%": round((last / closes[0] - 1) * 100, 2),
        "期間中の最大下落率_%": round(max_dd * 100, 2),
        "年率ボラティリティ_%": round(math.sqrt(var) * math.sqrt(252) * 100, 2),
    }
    for n in (5, 25, 50, 75, 200):
        value = sma(closes, n)
        out[f"移動平均_{n}日"] = round(value, 4) if value is not None else None
        out[f"移動平均_{n}日との乖離_%"] = round((last / value - 1) * 100, 2) if value else None
    return out
