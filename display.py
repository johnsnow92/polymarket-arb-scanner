"""Display formatting for scan results (table and JSON output)."""

import json

from tabulate import tabulate


def display_results(all_opportunities: list[dict], json_output: bool = False):
    """Display scan results as table or JSON."""
    print("\n" + "=" * 80)
    print(f"  RESULTS: {len(all_opportunities)} arbitrage opportunities found")
    print("=" * 80 + "\n")

    if not all_opportunities:
        print("  No opportunities above the minimum profit threshold.")
        print("  Try lowering --min-profit or check back later.")
        return

    if json_output:
        output = []
        for opp in all_opportunities:
            entry = {
                "type": opp["type"],
                "market": opp["market"],
                "prices": opp["prices"],
                "total_cost": opp["total_cost"],
                "gross_spread": opp["gross_spread"],
                "fees": opp["fees"],
                "net_profit": f"${opp['net_profit']:.4f}",
                "net_roi": opp["net_roi"],
                "volume": opp.get("volume", ""),
            }
            if "kalshi" in opp:
                entry["kalshi_market"] = opp["kalshi"]
                entry["match_score"] = opp["match"]
                entry["confidence"] = opp.get("confidence", "")
            if "_clob_depth" in opp:
                entry["depth"] = opp["_clob_depth"]
            output.append(entry)
        print(json.dumps(output, indent=2))
    else:
        has_cross = any("kalshi" in opp for opp in all_opportunities)
        has_depth = any("_clob_depth" in opp for opp in all_opportunities)
        table_data = []
        for opp in all_opportunities:
            row = [
                opp["type"],
                opp["market"],
            ]
            if has_cross:
                row.append(opp.get("kalshi", ""))
                row.append(opp.get("match", ""))
                row.append(opp.get("confidence", ""))
            row.extend([
                opp["prices"],
                opp["total_cost"],
                f"${opp['net_profit']:.4f}",
                opp["net_roi"],
                opp.get("volume", ""),
            ])
            if has_depth:
                depth = opp.get("_clob_depth")
                row.append(f"{depth:.0f}" if depth is not None else "")
            table_data.append(row)

        headers = ["Type", "Polymarket"]
        if has_cross:
            headers.extend(["Kalshi", "Match", "Conf"])
        headers.extend(["Prices", "Cost", "Net Profit", "ROI", "Volume"])
        if has_depth:
            headers.append("Depth")
        print(tabulate(table_data, headers=headers, tablefmt="grid", maxcolwidths=50))

    print(f"\n  Disclaimer: Prices are snapshots. Verify on-chain before trading.")
    print(f"  Opportunities may close within milliseconds.\n")
