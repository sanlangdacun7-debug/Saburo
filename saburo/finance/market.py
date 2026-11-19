"""株価データの取得。値には必ず「出典」と「いつ時点か」を付けて返す。

データ元: Yahoo Finance の公開チャート API（API キー不要、非公式）。
銘柄コードの例: トヨタ 7203.T / Apple AAPL / 日経平均 ^N225 / S&P500 ^GSPC /
               ドル円 JPY=X / 米10年債利回り ^TNX / 米13週財務省証券 ^IRX
"""

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .calc import price_stats

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE_NAME = "Yahoo Finance チャートAPI（非公式・遅延あり得る）"
RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y"}
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,20}$")


class MarketDataError(RuntimeError):
    pass


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if re.fullmatch(r"\d{4}|\d{3}[A-Z]", symbol):
        # 東証の4桁コード（例 7203、130A）は .T を付ける
        symbol += ".T"
    if not _SYMBOL.match(symbol):
        raise MarketDataError(f"銘柄コードの形式が正しくありません: {symbol}")
    return symbol


def _fetch_chart(symbol: str, range_: str, interval: str = "1d") -> dict:
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol)) + "?" + urllib.parse.urlencode(
        {"range": range_, "interval": interval}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Saburo)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.load(res)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise MarketDataError(f"{symbol} は見つかりませんでした。銘柄コードを確認してください。") from None
        raise MarketDataError(f"株価サーバーがエラーを返しました (HTTP {e.code})。") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise MarketDataError(f"株価サーバーに接続できませんでした: {e}") from None
    except json.JSONDecodeError:
        raise MarketDataError("株価サーバーの応答を読み取れませんでした。") from None

    chart = data.get("chart") or {}
    if chart.get("error"):
        raise MarketDataError(f"{symbol}: {chart['error'].get('description', '取得できませんでした')}")
    results = chart.get("result") or []
    if not results:
        raise MarketDataError(f"{symbol} のデータがありませんでした。")
    return {"result": results[0], "url": url}


def _ts(epoch, tz_offset: int) -> str | None:
    if epoch is None:
        return None
    tz = datetime.timezone(datetime.timedelta(seconds=tz_offset or 0))
    return datetime.datetime.fromtimestamp(epoch, tz).isoformat(timespec="minutes")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def get_quote(symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    fetched = _fetch_chart(symbol, "5d")
    r = fetched["result"]
    meta = r.get("meta", {})
    offset = meta.get("gmtoffset", 0)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") if meta.get("previousClose") is None else meta.get("previousClose")
    # 前日終値は日足の最後から2本目のほうが正確なことがある
    closes = [c for c in ((r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
    if len(closes) >= 2:
        prev = closes[-2]
    if price is None:
        raise MarketDataError(f"{symbol} の現在値がありませんでした。")
    out = {
        "銘柄コード": symbol,
        "名称": meta.get("longName") or meta.get("shortName"),
        "取引所": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "通貨": meta.get("currency"),
        "現在値": price,
        "前日終値": prev,
        "前日比": round(price - prev, 4) if prev else None,
        "前日比_%": round((price / prev - 1) * 100, 2) if prev else None,
        "当日高値": meta.get("regularMarketDayHigh"),
        "当日安値": meta.get("regularMarketDayLow"),
        "52週高値": meta.get("fiftyTwoWeekHigh"),
        "52週安値": meta.get("fiftyTwoWeekLow"),
        "価格の時刻_現地": _ts(meta.get("regularMarketTime"), offset),
        "取得時刻": _now(),
        "出典": SOURCE_NAME,
        "出典URL": fetched["url"],
        "注意": "リアルタイムではなく遅延値の可能性があります。発注前は証券会社の画面で再確認してください。",
    }
    if symbol in {"^TNX", "^IRX", "^FVX", "^TYX"}:
        out["注意"] += " 利回り指数は単位（% か %×10 か）を別の情報源でも確認してください。"
    return out


def get_price_history(symbol: str, range_: str = "1y") -> dict:
    symbol = normalize_symbol(symbol)
    if range_ not in RANGES:
        raise MarketDataError(f"期間は {', '.join(sorted(RANGES))} のどれかで指定してください。")
    fetched = _fetch_chart(symbol, range_)
    r = fetched["result"]
    meta = r.get("meta", {})
    quote = (r.get("indicators", {}).get("quote") or [{}])[0]
    times = r.get("timestamp") or []
    closes = quote.get("close") or []
    rows = [(t, c, h, lo) for t, c, h, lo in zip(times, closes, quote.get("high") or closes,
                                                  quote.get("low") or closes) if c is not None]
    if len(rows) < 2:
        raise MarketDataError(f"{symbol} の価格履歴が足りませんでした。")
    offset = meta.get("gmtoffset", 0)
    stats = price_stats([r_[1] for r_ in rows], [r_[2] for r_ in rows], [r_[3] for r_ in rows])
    return {
        "銘柄コード": symbol,
        "名称": meta.get("longName") or meta.get("shortName"),
        "通貨": meta.get("currency"),
        "期間": range_,
        "最初の日付": _ts(rows[0][0], offset)[:10],
        "最後の日付": _ts(rows[-1][0], offset)[:10],
        "指標_コードで計算": stats,
        "直近10営業日の終値": [
            {"日付": _ts(t, offset)[:10], "終値": round(c, 4)} for t, c, _, _ in rows[-10:]
        ],
        "取得時刻": _now(),
        "出典": SOURCE_NAME,
        "出典URL": fetched["url"],
    }
