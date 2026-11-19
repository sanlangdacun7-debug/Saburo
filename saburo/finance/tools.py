"""株式の担当エージェントが使う道具（ツール）の定義と実行。

AI の弱点（暗算ミス・古い記憶の株価・出典のない数値）を、道具で補う。
"""

import json

from . import calc, market

WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
WEB_FETCH = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8}

FINANCE_TOOLS = [
    {
        "name": "calculate",
        "description": (
            "数式を正確に計算する電卓。金額・比率・数量・利回り・損益など、数値を出すときは暗算せず必ずこれを使う。"
            "使えるもの: 数値、+ - * / // % **、括弧、abs round min max sum sqrt log log10 exp floor ceil pow、"
            "定数 pi e。桁区切りのカンマは書かない（1234567 と書く）。"
            "label には何の計算かを日本語で書く（例: 'トヨタ100株の買付金額'）。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "計算式。例: (3150-2980)/2980*100"},
                "label": {"type": "string", "description": "何の計算か"},
            },
            "required": ["expression", "label"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_quote",
        "description": (
            "株・ETF・指数・為替・金利の最新値（遅延あり得る）を取得する。株価を書くときは記憶に頼らず必ずこれで確認する。"
            "コード例: 7203（東証は4桁でよい）, AAPL, ^N225, ^GSPC, JPY=X（ドル円）, ^TNX（米10年債利回り）。"
            "結果の『価格の時刻』と『出典』を回答にも書くこと。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "銘柄コード"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_price_history",
        "description": (
            "日足の価格履歴を取得し、移動平均(5/25/50/75/200日)・乖離率・騰落率・最大下落率・年率ボラティリティ・"
            "期間高値安値をコードで計算して返す。テクニカルの判断や値動きの説明はこの結果に基づいて行う。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "銘柄コード"},
                "range": {"type": "string", "enum": sorted(market.RANGES), "description": "期間（既定 1y）"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "position_size",
        "description": (
            "買いの発注数量を、損切り価格と許容損失額から単元に合わせて切り捨てで計算する。"
            "予算を渡すと予算の上限も考慮する。発注数量を提案するときは必ずこれを使う。"
            "日本株の単元は通常100株、米国株は1株。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_price": {"type": "number", "description": "買値（指値）"},
                "stop_price": {"type": "number", "description": "損切り（逆指値）価格。買値より低い"},
                "max_loss": {"type": "number", "description": "損切り時に許容する最大損失額"},
                "lot_size": {"type": "integer", "description": "単元株数（既定100）"},
                "budget": {"type": "number", "description": "使える予算（任意）"},
                "fee_rate": {"type": "number", "description": "片道の手数料率。0.001 = 0.1%（任意、既定0）"},
            },
            "required": ["entry_price", "stop_price", "max_loss"],
            "additionalProperties": False,
        },
    },
]

FINANCE_TOOL_NAMES = {t["name"] for t in FINANCE_TOOLS}


def _ok(data) -> tuple[str, bool]:
    return json.dumps(data, ensure_ascii=False, indent=1), False


def _num(args: dict, key: str, required: bool = True):
    value = args.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise calc.CalcError(f"{key} は数値で指定してください。")
    return value


def run_finance_tool(name: str, args) -> tuple[str, bool]:
    """(結果テキスト, エラーかどうか) を返す。"""
    if not isinstance(args, dict):
        return "入力が不正です。", True
    try:
        if name == "calculate":
            expression = args.get("expression")
            if not isinstance(expression, str) or not expression.strip():
                return "expression は空でない文字列で指定してください。", True
            value = calc.safe_eval(expression)
            return _ok({"計算": args.get("label", ""), "式": expression, "結果": value})
        if name == "get_quote":
            symbol = args.get("symbol")
            if not isinstance(symbol, str):
                return "symbol は文字列で指定してください。", True
            return _ok(market.get_quote(symbol))
        if name == "get_price_history":
            symbol = args.get("symbol")
            if not isinstance(symbol, str):
                return "symbol は文字列で指定してください。", True
            range_ = args.get("range") or "1y"
            return _ok(market.get_price_history(symbol, range_))
        if name == "position_size":
            lot = args.get("lot_size", 100)
            if isinstance(lot, bool) or not isinstance(lot, int):
                return "lot_size は整数で指定してください。", True
            return _ok(calc.position_size(
                entry_price=_num(args, "entry_price"),
                stop_price=_num(args, "stop_price"),
                max_loss=_num(args, "max_loss"),
                lot_size=lot,
                budget=_num(args, "budget", required=False),
                fee_rate=_num(args, "fee_rate", required=False) or 0.0,
            ))
    except (calc.CalcError, market.MarketDataError) as e:
        return f"{e}（値が取れない場合は推測で埋めず『未確認』と書くこと）", True
    return f"未知のツールです: {name}", True
