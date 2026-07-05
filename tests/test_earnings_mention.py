"""Unit tests for the earnings-mention OOS logger (earnings_mention.py).

Pure/deterministic: a fake duck-typed client supplies all candlestick data, so
these run with no network, no API keys, and no live KalshiClient.
"""

from datetime import datetime, timedelta, timezone

import pytest

import earnings_mention as em


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeClient:
    """Records every fetch_candlesticks call and answers from a fixed table."""

    def __init__(self, candles=None):
        self._candles = candles or {}  # {ticker: [candle_dict, ...]}
        self.calls = []

    def fetch_candlesticks(self, series_ticker, ticker, start_ts, end_ts, period_interval=60):
        self.calls.append((series_ticker, ticker, start_ts, end_ts, period_interval))
        return self._candles.get(ticker, [])


def _candle(close_dollars=None, mean_dollars=None, yes_bid=None, yes_ask=None):
    c = {}
    price = {}
    if close_dollars is not None:
        price["close_dollars"] = close_dollars
    if mean_dollars is not None:
        price["mean_dollars"] = mean_dollars
    if price:
        c["price"] = price
    if yes_bid is not None:
        c["yes_bid"] = {"close_dollars": yes_bid}
    if yes_ask is not None:
        c["yes_ask"] = {"close_dollars": yes_ask}
    return c


CLOSE = "2026-07-06T00:00:00Z"


def _market(ticker="M1", title="Will Apple mention AI?", close=CLOSE, result="yes", **extra):
    m = {"ticker": ticker, "title": title, "close_time": close, "result": result}
    m.update(extra)
    return m


# --------------------------------------------------------------------------- #
# classify_market (unchanged by the redesign)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,expected", [
    ("Will Apple mention 'AI' on its earnings call?", True),
    ("How many times will Tesla say 'robotaxi'?", True),
    ("Will NVIDIA mentions exceed 5?", True),
    ("Will BTC be above $100k at year end?", False),
    ("Will the Fed cut rates in July?", False),
])
def test_classify_market(title, expected):
    assert em.classify_market({"title": title, "ticker": "X"}) is expected


# --------------------------------------------------------------------------- #
# has_valid_result
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("result,expected", [
    ("yes", True),
    ("no", True),
    ("YES", True),   # case-insensitive
    ("", False),
    ("void", False),
    (None, False),
])
def test_has_valid_result(result, expected):
    assert em.has_valid_result({"result": result}) is expected


def test_has_valid_result_missing_key():
    assert em.has_valid_result({}) is False


# --------------------------------------------------------------------------- #
# _series_ticker_for_candles
# --------------------------------------------------------------------------- #
def test_series_ticker_prefers_explicit_field():
    market = {"series_ticker": "KXEARNINGSMENTIONBA", "event_ticker": "IGNORED-26Q2"}
    assert em._series_ticker_for_candles(market) == "KXEARNINGSMENTIONBA"


def test_series_ticker_falls_back_to_event_ticker_prefix():
    market = {"event_ticker": "KXEARNINGSMENTIONBA-26Q2ER"}
    assert em._series_ticker_for_candles(market) == "KXEARNINGSMENTIONBA"


def test_series_ticker_empty_when_neither_present():
    assert em._series_ticker_for_candles({}) == ""


# --------------------------------------------------------------------------- #
# _candle_yes_price
# --------------------------------------------------------------------------- #
def test_candle_yes_price_prefers_close_dollars():
    assert em._candle_yes_price(_candle(close_dollars="0.2200")) == pytest.approx(0.22)


def test_candle_yes_price_falls_back_to_mean_dollars():
    assert em._candle_yes_price(_candle(mean_dollars="0.35")) == pytest.approx(0.35)


def test_candle_yes_price_falls_back_to_bid_ask_midpoint():
    candle = _candle(yes_bid="0.20", yes_ask="0.30")
    assert em._candle_yes_price(candle) == pytest.approx(0.25)


def test_candle_yes_price_none_when_nothing_usable():
    assert em._candle_yes_price({}) is None


def test_candle_yes_price_ignores_bad_types():
    assert em._candle_yes_price(_candle(close_dollars="not-a-number")) is None


# --------------------------------------------------------------------------- #
# price_at_t24h — replaces the old live-snapshot window entirely
# --------------------------------------------------------------------------- #
def test_price_at_t24h_uses_last_candle_in_window():
    market = _market(series_ticker="KXEARNINGSMENTIONBA")
    client = FakeClient(candles={
        "M1": [_candle(close_dollars="0.20"), _candle(close_dollars="0.28")],
    })
    price = em.price_at_t24h(client, market)
    assert price == pytest.approx(0.28)  # last candle in the window wins


def test_price_at_t24h_computes_correct_window_and_series():
    close_dt = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)
    market = _market(series_ticker="KXEARNINGSMENTIONBA")
    client = FakeClient(candles={"M1": [_candle(close_dollars="0.20")]})
    em.price_at_t24h(client, market)
    assert len(client.calls) == 1
    series, ticker, start_ts, end_ts, period = client.calls[0]
    assert series == "KXEARNINGSMENTIONBA"
    assert ticker == "M1"
    assert period == 60
    expected_start = int((close_dt - timedelta(hours=26)).timestamp())
    expected_end = int((close_dt - timedelta(hours=23)).timestamp())
    assert start_ts == expected_start
    assert end_ts == expected_end


def test_price_at_t24h_falls_back_to_event_ticker_series():
    market = _market(event_ticker="KXEARNINGSMENTIONBA-26Q2ER")
    client = FakeClient(candles={"M1": [_candle(close_dollars="0.20")]})
    assert em.price_at_t24h(client, market) == pytest.approx(0.20)


def test_price_at_t24h_none_when_no_close_time():
    market = {"ticker": "M1", "series_ticker": "S"}
    assert em.price_at_t24h(FakeClient(), market) is None


def test_price_at_t24h_none_when_no_ticker():
    market = {"close_time": CLOSE, "series_ticker": "S"}
    assert em.price_at_t24h(FakeClient(), market) is None


def test_price_at_t24h_none_when_no_series_derivable():
    market = {"ticker": "M1", "close_time": CLOSE}  # no series_ticker, no event_ticker
    assert em.price_at_t24h(FakeClient(), market) is None


def test_price_at_t24h_none_when_no_candles_in_window():
    market = _market(series_ticker="S")
    assert em.price_at_t24h(FakeClient(candles={}), market) is None


def test_price_at_t24h_none_when_candle_unusable():
    market = _market(series_ticker="S")
    client = FakeClient(candles={"M1": [{}]})  # candle with no usable price fields
    assert em.price_at_t24h(client, market) is None


# --------------------------------------------------------------------------- #
# build_resolved
# --------------------------------------------------------------------------- #
def test_build_resolved_yes_outcome():
    market = _market(ticker="M1", result="yes", series_ticker="S")
    r = em.build_resolved(market, 0.32)
    assert r == em.Resolved(ticker="M1", yes_price=0.32, outcome=1.0, series="S")


def test_build_resolved_no_outcome():
    market = _market(ticker="M1", result="no", series_ticker="S")
    r = em.build_resolved(market, 0.32)
    assert r.outcome == 0.0


# --------------------------------------------------------------------------- #
# compute_oos_stats (unchanged by the redesign)
# --------------------------------------------------------------------------- #
def test_compute_oos_stats_band_filter():
    resolved = [
        em.Resolved("a", 0.05, 0.0, "S"),   # below band -> excluded
        em.Resolved("b", 0.30, 0.0, "S"),   # in band
        em.Resolved("c", 0.60, 0.0, "S"),   # above band -> excluded
        em.Resolved("d", 0.40, 1.0, "S"),   # in band
    ]
    stats = em.compute_oos_stats(resolved)
    assert stats.n == 2


def test_compute_oos_stats_known_numbers():
    # 5 contracts priced 0.30; one settles YES -> richness = [.3,.3,.3,.3,-.7]
    resolved = [em.Resolved(f"m{i}", 0.30, out, "S")
                for i, out in enumerate([0.0, 0.0, 0.0, 0.0, 1.0])]
    stats = em.compute_oos_stats(resolved)
    assert stats.n == 5
    assert stats.mean_richness_pts == pytest.approx(10.0)
    assert stats.z == pytest.approx(0.5)
    assert stats.by_category["S"] == (5, pytest.approx(10.0))


# --------------------------------------------------------------------------- #
# verdict (pre-registered 8/3 gate, unchanged by the redesign)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("n,pts,z,expected", [
    (120, 10.0, 2.5, "pursue"),     # gap>=9 and z>=2
    (120, 3.0, 1.0, "kill"),        # gap<4.5
    (120, 6.0, 1.0, "continue"),    # mid gap
    (120, 10.0, 1.5, "continue"),   # gap ok but z<2 (and >=4.5 so not kill)
    (50, 12.0, 3.0, "continue"),    # n<100 -> never terminal
])
def test_verdict(n, pts, z, expected):
    assert em.verdict(em.OosStats(n=n, mean_richness_pts=pts, z=z)) == expected
