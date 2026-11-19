import pytest

from saburo.finance import market


def fake_chart(closes, meta=None):
    times = [1_700_000_000 + 86400 * i for i in range(len(closes))]
    return {
        "result": {
            "meta": {"currency": "JPY", "regularMarketPrice": closes[-1], "gmtoffset": 32400,
                     "regularMarketTime": times[-1], "shortName": "TEST", **(meta or {})},
            "timestamp": times,
            "indicators": {"quote": [{"close": closes, "high": closes, "low": closes}]},
        },
        "url": "https://example.invalid/chart",
    }


@pytest.mark.parametrize("raw, expected", [
    ("7203", "7203.T"), ("130a", "130A.T"), ("aapl", "AAPL"), ("^N225", "^N225"), ("JPY=X", "JPY=X"),
])
def test_normalize_symbol(raw, expected):
    assert market.normalize_symbol(raw) == expected


def test_normalize_symbol_rejects_garbage():
    with pytest.raises(market.MarketDataError):
        market.normalize_symbol("7203; rm -rf /")


def test_quote_uses_previous_daily_close(monkeypatch):
    monkeypatch.setattr(market, "_fetch_chart", lambda s, r, i="1d": fake_chart([100, 102, None, 105, 110]))
    q = market.get_quote("7203")
    assert q["銘柄コード"] == "7203.T"
    assert q["現在値"] == 110 and q["前日終値"] == 105
    assert q["前日比_%"] == round((110 / 105 - 1) * 100, 2)
    assert q["出典URL"] and q["取得時刻"] and q["価格の時刻_現地"].endswith("+09:00")


def test_history_computes_stats_and_skips_missing(monkeypatch):
    closes = [100 + i for i in range(30)]
    closes[10] = None
    monkeypatch.setattr(market, "_fetch_chart", lambda s, r, i="1d": fake_chart(closes))
    h = market.get_price_history("AAPL", "3mo")
    assert h["指標_コードで計算"]["本数"] == 29
    assert len(h["直近10営業日の終値"]) == 10
    with pytest.raises(market.MarketDataError):
        market.get_price_history("AAPL", "10y")
