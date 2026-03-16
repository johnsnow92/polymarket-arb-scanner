"""Display formatting for scan results (table and JSON output)."""

import json
import logging

from tabulate import tabulate

logger = logging.getLogger(__name__)


def _fmt_roi(roi) -> str:
    """Format ROI value for display — handles both string and float formats."""
    if isinstance(roi, str):
        return roi
    if isinstance(roi, (int, float)):
        return f"{roi * 100:.2f}%"
    return ""


def _fmt_confidence(conf) -> str:
    """Format confidence value — handles strings (HIGH/MEDIUM/LOW) and floats."""
    if isinstance(conf, str):
        return conf
    if isinstance(conf, (int, float)):
        return f"{conf:.0%}"
    return ""


def display_results(all_opportunities: list[dict], json_output: bool = False):
    """Display scan results as table or JSON."""
    print("\n" + "=" * 80)
    print(f"  RESULTS: {len(all_opportunities)} opportunities found")
    print("=" * 80 + "\n")

    if not all_opportunities:
        print("  No opportunities above the minimum profit threshold.")
        print("  Try lowering --min-profit or check back later.")
        return

    if json_output:
        output = []
        for opp in all_opportunities:
            entry = {
                "type": opp.get("type", ""),
                "market": opp.get("market", ""),
                "prices": opp.get("prices", ""),
                "total_cost": opp.get("total_cost", ""),
                "net_profit": f"${opp.get('net_profit', 0):.4f}",
                "net_roi": _fmt_roi(opp.get("net_roi", "")),
            }
            if "gross_spread" in opp:
                entry["gross_spread"] = opp["gross_spread"]
            if "fees" in opp:
                entry["fees"] = opp["fees"]
            if "kalshi" in opp:
                entry["kalshi_market"] = opp["kalshi"]
                entry["match_score"] = opp.get("match", "")
            if "confidence" in opp:
                entry["confidence"] = _fmt_confidence(opp["confidence"])
            if "_clob_depth" in opp:
                entry["depth"] = opp["_clob_depth"]
            if "volume" in opp:
                entry["volume"] = opp["volume"]
            # Layer 2-4 specific fields
            if "_stale_age" in opp:
                entry["stale_age_seconds"] = opp["_stale_age"]
            if "_consensus" in opp:
                entry["consensus_prob"] = opp["_consensus"]
            if "_num_sources" in opp:
                entry["signal_sources"] = opp["_num_sources"]
            if "_spread" in opp:
                entry["mm_spread"] = opp["_spread"]
            if "_inventory" in opp:
                entry["mm_inventory"] = opp["_inventory"]
            output.append(entry)
        print(json.dumps(output, indent=2))
    else:
        has_cross = any("kalshi" in opp for opp in all_opportunities)
        has_confidence = any("confidence" in opp for opp in all_opportunities)
        has_depth = any("_clob_depth" in opp for opp in all_opportunities)
        has_platforms = any(opp.get("_platforms_checked") for opp in all_opportunities)
        table_data = []
        for opp in all_opportunities:
            row = [
                opp.get("type", ""),
                opp.get("market", ""),
            ]
            if has_cross:
                row.append(opp.get("kalshi", ""))
                row.append(opp.get("match", ""))
            if has_confidence:
                row.append(_fmt_confidence(opp.get("confidence", "")))
            if has_platforms:
                platforms = opp.get("_platforms_checked", [])
                row.append(", ".join(platforms) if platforms else "")
            row.extend([
                opp.get("prices", ""),
                opp.get("total_cost", ""),
                f"${opp.get('net_profit', 0):.4f}",
                _fmt_roi(opp.get("net_roi", "")),
                opp.get("volume", ""),
            ])
            if has_depth:
                depth = opp.get("_clob_depth")
                row.append(f"{depth:.0f}" if depth is not None else "")
            table_data.append(row)

        headers = ["Type", "Market"]
        if has_cross:
            headers.extend(["Kalshi", "Match"])
        if has_confidence:
            headers.append("Conf")
        if has_platforms:
            headers.append("Platforms")
        headers.extend(["Prices", "Cost", "Net Profit", "ROI", "Volume"])
        if has_depth:
            headers.append("Depth")
        print(tabulate(table_data, headers=headers, tablefmt="grid", maxcolwidths=50))

    print("\n  Disclaimer: Prices are snapshots. Verify on-chain before trading.")
    print("  Opportunities may close within milliseconds.\n")
