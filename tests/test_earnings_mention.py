"""Unit tests for the earnings-mention OOS logger (earnings_mention.py).

Pure/deterministic: a fake duck-typed client supplies all market data, so these
run with no network, no API keys, and no live KalshiClient.
"""

from datetime import datetime, timezone

import pytest

import earnings_mention as em


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, events=None, settled=None):
        self._events = events or []
        self._settled = settled or {}

    def fetch_all_events(self, *a, **k):
        return self._events

    def get_market_price(self, market):
        return market.get("_yes"), market.get("_no")

    def fetch_market(self, ticker):
        return self._settled.get(ticker)


def _market(ticker, title, close, yes=0.30, no=0.70, **extra):
    m = {
        "ticker": ticker,
        "title": title,
        "close_time": close,
        "_yes": yes,
        "_no": no,
        "volume": extra.pop("volume", 1000),
    }
    m.update(extra)
    return m


NOW = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
CLOSE_12H = "2026-06-26T00:00:00Z"   # 12h after NOW -> inside [close-24h, close-6h]
CLOSE_2H = "2026-06-25T14:00:00Z"    # 2h after NOW  -> too close (<6h)
CLOSE_30H = "2026-06-26T18:00:00Z"   # 30h after NOW -> too early (>24h)


# --------------------------------------------------------------------------- #
# classify_market
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
# in_snapshot_window
# --------------------------------------------------------------------------- #
def test_in_snapshot_window_inside():
    assert em.in_snapshot_window({"close_time": CLOSE_12H}, NOW) is True


def test_in_snapshot_window_too_close():
    assert em.in_snapshot_window({"close_time": CLOSE_2H}, NOW) is False


def test_in_snapshot_window_too_early():
    assert em.in_snapshot_window({"close_time": CLOSE_30H}, NOW) is False


def test_in_snapshot_window_missing_close():
    assert em.in_snapshot_window({}, NOW) is False


# --------------------------------------------------------------------------- #
# snapshot_open_markets
# --------------------------------------------------------------------------- #
def test_snapshot_open_markets_filters():
    events = [{
        "markets": [
            _market("MENTION-IN", "Will Apple mention AI?", CLOSE_12H, yes=0.30),
            _market("MENTION-OUT", "Will Apple mention AI?", CLOSE_2H),       # out of window
            _market("BTC-IN", "Will BTC be above 100k?", CLOSE_12H),         # not a mention market
        ]
    }]
    snaps = em.snapshot_open_markets(FakeClient(events=events), NOW)
    assert [s.ticker for s in snaps] == ["MENTION-IN"]
    assert snaps[0].yes_price == 0.30
    assert snaps[0].hours_to_close == 12.0


# --------------------------------------------------------------------------- #
# resolve_settlements
# --------------------------------------------------------------------------- #
def test_resolve_settlements():
    pending = [
        em.Snapshot("A", NOW.isoformat(), 12.0, 0.30, 0.70, 1000, "S"),
        em.Snapshot("B", NOW.isoformat(), 12.0, 0.40, 0.60, 1000, "S"),
        em.Snapshot("C", NOW.isoformat(), 12.0, 0.25, 0.75, 1000, "S"),
    ]
    settled = {
        "A": {"status": "settled", "result": "yes"},
        "B": {"status": "finalized", "result": "no"},
        "C": {"status": "active", "result": ""},   # not settled yet -> skipped
    }
    resolved = em.resolve_settlements(FakeClient(settled=settled), pending)
    assert {r.ticker: r.outcome for r in resolved} == {"A": 1.0, "B": 0.0}


# --------------------------------------------------------------------------- #
# compute_oos_stats
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
# verdict (pre-registered 8/3 gate)
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
