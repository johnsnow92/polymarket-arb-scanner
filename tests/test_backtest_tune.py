"""Tests for the backtest grid-sweep tuning (backtest.run_tuning_sweep et al.).

Uses a fake SnapshotRecorder so the sweep runs deterministically without a DB.
Snapshots use opp_type 'MarketMake', which BacktestEngine scores via the stored
net_profit (no fees.py dependency), keeping P&L deterministic across machines.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest


class _FakeRecorder:
    def __init__(self, snaps):
        self._snaps = snaps

    def get_snapshots(self, start, end, opp_type_filter=None):
        if opp_type_filter:
            return [s for s in self._snaps if s.get("opp_type", "").startswith(opp_type_filter)]
        return list(self._snaps)

    def close(self):
        pass


def _snap(net_profit=0.10, price_a=0.4, price_b=0.5, opp_type="MarketMake", market="M1"):
    return {
        "net_profit": net_profit,
        "gross_spread": 0,
        "fees": 0,
        "opp_type": opp_type,
        "market": market,
        "price_a": price_a,
        "price_b": price_b,
        "timestamp": "2026-06-01T00:00:00Z",
    }


def _engine(snaps):
    return backtest.BacktestEngine(recorder=_FakeRecorder(snaps), initial_balance=1000.0)


_START = "2026-06-01T00:00:00+00:00"
_END = "2026-06-02T00:00:00+00:00"
_FIXED_TS = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)


class TestParseGrid:
    def test_plain_fractions(self):
        assert backtest._parse_grid("0,0.01,0.05") == [0.0, 0.01, 0.05]

    def test_percentages_above_one_converted(self):
        assert backtest._parse_grid("5,10") == [0.05, 0.10]

    def test_dedupes_and_sorts(self):
        assert backtest._parse_grid("0.05, 0.0, 0.05") == [0.0, 0.05]

    def test_ignores_blanks(self):
        assert backtest._parse_grid("0.01, ,0.02,") == [0.01, 0.02]


class TestRunTuningSweep:
    def test_one_cell_per_combination_in_order(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.5, 0.0], min_profit_grid=[0.01, 0.0],
        )
        assert len(cells) == 4
        # deterministic ascending (min_roi, min_profit) order
        assert [(c.min_roi, c.min_profit) for c in cells] == [
            (0.0, 0.0), (0.0, 0.01), (0.5, 0.0), (0.5, 0.01),
        ]

    def test_threshold_filters_trades(self):
        # roi = 0.10 / 0.9 ≈ 0.111: passes at 0.05, filtered at 0.5
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.05, 0.5], min_profit_grid=[0.0],
        )
        by_roi = {c.min_roi: c for c in cells}
        assert by_roi[0.05].total_trades == 2
        assert by_roi[0.5].total_trades == 0
        assert by_roi[0.05].total_pnl > 0
        assert by_roi[0.5].total_pnl == 0.0

    def test_reusable_across_cells(self):
        # Recorder is read each cell; results must be repeatable, not drained.
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.0, 0.0], min_profit_grid=[0.0],
        )
        # duplicate grid value dedupes to a single cell
        assert len(cells) == 1
        assert cells[0].total_trades == 2


class TestBestCell:
    def test_picks_highest_pnl_with_trades(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.0, 0.5], min_profit_grid=[0.0],
        )
        best = backtest._best_cell(cells)
        assert best is not None
        assert best.min_roi == 0.0  # the 0.5 cell has 0 trades, ineligible

    def test_none_when_no_trades(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap()]), _START, _END,
            min_roi_grid=[0.9], min_profit_grid=[0.0],
        )
        assert backtest._best_cell(cells) is None


class TestRenderMarkdown:
    def test_contains_table_and_recommendation(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.0, 0.5], min_profit_grid=[0.0],
        )
        md = backtest.render_sweep_markdown(cells, generated_at=_FIXED_TS)
        assert "# Backtest tuning sweep — 2026-06-07 12:00 UTC" in md
        assert "| min_roi | min_profit | trades | win_rate | total_pnl | max_dd | sharpe |" in md
        assert "**Recommended:**" in md
        assert "(best)" in md

    def test_deterministic(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap()]), _START, _END,
            min_roi_grid=[0.0], min_profit_grid=[0.0],
        )
        a = backtest.render_sweep_markdown(cells, generated_at=_FIXED_TS)
        b = backtest.render_sweep_markdown(cells, generated_at=_FIXED_TS)
        assert a == b

    def test_no_trades_message(self):
        cells = backtest.run_tuning_sweep(
            _engine([_snap()]), _START, _END,
            min_roi_grid=[0.9], min_profit_grid=[0.0],
        )
        md = backtest.render_sweep_markdown(cells, generated_at=_FIXED_TS)
        assert "No cell booked any trades" in md


class TestWriteReport:
    def test_writes_markdown_file(self, tmp_path):
        cells = backtest.run_tuning_sweep(
            _engine([_snap(), _snap()]), _START, _END,
            min_roi_grid=[0.0], min_profit_grid=[0.0],
        )
        path = backtest.write_sweep_report(cells, str(tmp_path))
        assert os.path.exists(path)
        assert path.endswith("backtest_tuning_report.md")
        content = open(path).read()
        assert content.startswith("# Backtest tuning sweep")
