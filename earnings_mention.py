"""Earnings-mention NO-harvest out-of-sample (OOS) logger — detection only.

Re-runs the round-2 earnings-mention pilot out-of-sample on newly-settled Kalshi
company-KPI / earnings-mention markets to confirm the in-sample finding that
11-50c YES contracts are systematically ~10pts too rich at T-24h (R2-1: +10.3pts,
z=2.41, n=240). This module is a deterministic statistical logger: it snapshots
the YES price in the [close-24h, close-6h] window, joins each snapshot to the
realized settlement, and computes a richness z-score. It places NO orders and
involves NO LLM in any path. See docs/plans/08-earnings-mention-oos.md.

The client is duck-typed (any object exposing ``fetch_all_events``,
``get_market_price`` and ``fetch_market``) so the core logic is unit-testable
without network, keys, or the live KalshiClient.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import fmean, stdev
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)

# --- pre-registered constants (do not move without re-registering the gate) ---
BAND_LO = 0.11          # YES price band lower bound (R2-1: 11-50c)
BAND_HI = 0.50          # YES price band upper bound
WINDOW_OPEN_H = 24.0    # snapshot window opens at close-24h
WINDOW_CLOSE_H = 6.0    # snapshot window closes at close-6h
PURSUE_GAP_PTS = 9.0    # OOS richness gap (points) to PURSUE
KILL_GAP_PTS = 4.5      # OOS richness gap below which to KILL
MIN_N = 100             # min OOS contracts before a terminal verdict
PURSUE_Z = 2.0          # min z-score to PURSUE

# Conservative (false-negative-biased) classifier defaults. Calibrate
# SERIES_PREFIXES against the in-sample pilot market list before relying on it.
SERIES_PREFIXES: tuple[str, ...] = ()
TITLE_PATTERNS: tuple[str, ...] = (
    r"\bmention(s|ed)?\b",
    r"\bsay\b",
    r"\bsaid\b",
    r"how many times",
    r"number of times",
    r"\bon (the|its) .*earnings call\b",
)
_TITLE_RE = re.compile("|".join(TITLE_PATTERNS), re.IGNORECASE)


class _MarketClient(Protocol):
    def fetch_all_events(self, *a, **k) -> list[dict]: ...
    def get_market_price(self, market: dict) -> tuple[float | None, float | None]: ...
    def fetch_market(self, ticker: str) -> dict | None: ...


@dataclass(frozen=True)
class Snapshot:
    """A YES-price snapshot of one market taken inside the T-24h..T-6h window."""
    ticker: str
    snapshot_ts: str
    hours_to_close: float
    yes_price: float
    no_price: float
    volume: float
    series: str


@dataclass(frozen=True)
class Resolved:
    """A snapshot joined to its realized binary settlement outcome."""
    ticker: str
    yes_price: float
    outcome: float  # 1.0 if settled YES, 0.0 if settled NO
    series: str


@dataclass(frozen=True)
class OosStats:
    """Out-of-sample richness statistics over the 11-50c YES band."""
    n: int
    mean_richness_pts: float          # mean(yes_price - outcome) * 100, in points
    z: float
    by_category: dict[str, tuple[int, float]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def _parse_ts(value: str | None) -> datetime | None:
    """Parse a Kalshi ISO-8601 timestamp to a tz-aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        logger.debug("Failed to parse timestamp %r", value)
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _series_of(market: dict) -> str:
    return str(
        market.get("series_ticker")
        or market.get("event_ticker")
        or market.get("ticker", "")
    )


def classify_market(market: dict) -> bool:
    """True if *market* belongs to the company-KPI / earnings-mention family.

    Conservative by design: a false negative (skipping a real mention market)
    only costs sample, while a false positive pollutes the OOS estimate.
    """
    series = _series_of(market)
    if SERIES_PREFIXES and series.upper().startswith(
        tuple(p.upper() for p in SERIES_PREFIXES)
    ):
        return True
    text = " ".join(
        str(market.get(k, ""))
        for k in ("title", "subtitle", "yes_sub_title", "yes_subtitle")
    )
    return bool(_TITLE_RE.search(text))


def _close_time(market: dict) -> datetime | None:
    return _parse_ts(market.get("close_time") or market.get("expiration_time"))


def in_snapshot_window(market: dict, now: datetime) -> bool:
    """True if *now* falls inside [close-24h, close-6h] for *market*."""
    close = _close_time(market)
    if close is None:
        return False
    return (close - timedelta(hours=WINDOW_OPEN_H)) <= now <= (
        close - timedelta(hours=WINDOW_CLOSE_H)
    )


def _hours_to_close(market: dict, now: datetime) -> float:
    close = _close_time(market)
    if close is None:
        return float("nan")
    return (close - now).total_seconds() / 3600.0


# --------------------------------------------------------------------------- #
# Stage 1 — snapshot open mention/KPI markets in-window
# --------------------------------------------------------------------------- #
def snapshot_open_markets(client: _MarketClient, now: datetime) -> list[Snapshot]:
    """Snapshot every in-window mention/KPI market's YES price right now."""
    out: list[Snapshot] = []
    for event in client.fetch_all_events() or []:
        for market in event.get("markets", []) or []:
            if not classify_market(market):
                continue
            if not in_snapshot_window(market, now):
                continue
            yes_price, no_price = client.get_market_price(market)
            if yes_price is None or no_price is None:
                continue
            ticker = str(market.get("ticker", ""))
            if not ticker:
                continue
            out.append(
                Snapshot(
                    ticker=ticker,
                    snapshot_ts=now.isoformat(),
                    hours_to_close=round(_hours_to_close(market, now), 2),
                    yes_price=float(yes_price),
                    no_price=float(no_price),
                    volume=float(market.get("volume", 0) or 0),
                    series=_series_of(market),
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Stage 2 — resolve matured snapshots against realized settlement
# --------------------------------------------------------------------------- #
def resolve_settlements(
    client: _MarketClient, pending: Iterable[Snapshot]
) -> list[Resolved]:
    """Join each matured snapshot to its settled outcome via ``fetch_market``.

    Uses the per-market endpoint (any status) — NOT ``get_settlements``, which is
    account-scoped and only covers markets this account actually traded.
    """
    out: list[Resolved] = []
    for snap in pending:
        market = client.fetch_market(snap.ticker)
        if not market:
            continue
        status = str(market.get("status", "")).lower()
        result = str(market.get("result", "")).lower()
        if status not in ("settled", "finalized", "closed"):
            continue
        if result not in ("yes", "no"):
            continue
        out.append(
            Resolved(
                ticker=snap.ticker,
                yes_price=snap.yes_price,
                outcome=1.0 if result == "yes" else 0.0,
                series=snap.series,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Stage 3 — OOS richness statistics + verdict
# --------------------------------------------------------------------------- #
def _zscore(richness: list[float]) -> float:
    n = len(richness)
    if n < 2:
        return 0.0
    sd = stdev(richness)
    if sd == 0:
        return 0.0
    return fmean(richness) / (sd / math.sqrt(n))


def compute_oos_stats(resolved: Iterable[Resolved]) -> OosStats:
    """Mean YES richness and z-score over the 11-50c band, plus by-category."""
    band = [r for r in resolved if BAND_LO <= r.yes_price <= BAND_HI]
    richness = [r.yes_price - r.outcome for r in band]
    n = len(richness)
    mean_pts = (fmean(richness) * 100.0) if n else 0.0
    z = _zscore(richness)

    by_category: dict[str, list[float]] = {}
    for r in band:
        by_category.setdefault(r.series, []).append(r.yes_price - r.outcome)
    cat_stats = {
        series: (len(vals), round(fmean(vals) * 100.0, 2))
        for series, vals in by_category.items()
    }
    return OosStats(
        n=n,
        mean_richness_pts=round(mean_pts, 3),
        z=round(z, 3),
        by_category=cat_stats,
    )


def verdict(stats: OosStats) -> str:
    """Pre-registered gate toward the 8/3 decision review (no discretionary override).

    'pursue' if mean_richness_pts >= PURSUE_GAP_PTS (9.0) and z >= PURSUE_Z (2.0),
    provided n >= MIN_N (100). 'kill' if mean_richness_pts < KILL_GAP_PTS (4.5).
    Otherwise 'continue' — including whenever n < MIN_N, which is never terminal.
    """
    if stats.n < MIN_N:
        return "continue"
    if stats.mean_richness_pts >= PURSUE_GAP_PTS and stats.z >= PURSUE_Z:
        return "pursue"
    if stats.mean_richness_pts < KILL_GAP_PTS:
        return "kill"
    return "continue"
