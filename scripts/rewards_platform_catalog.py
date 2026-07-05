#!/usr/bin/env python3
"""Generate the read-only market rewards platform catalog.

This script is intentionally account-agnostic. It refreshes local catalog files
from the last reviewed platform records and does not authenticate, trade, claim,
sign, or touch account state.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

DEFAULT_JSON_OUTPUT = Path("data/rewards-platforms/latest.json")
DEFAULT_MD_OUTPUT = Path("data/rewards-platforms/latest.md")
DEFAULT_CSV_OUTPUT = Path("data/rewards-platforms/latest.csv")

SEED_RECORDS = [
    {
        "platform": "Kalshi",
        "program": "Liquidity Incentive Program",
        "category": "prediction_market",
        "reward_type": "liquidity_pool",
        "source_status": "primary_verified",
        "safe_automation_score": 67,
        "required_work": "Post qualifying resting limit orders during active incentive windows.",
        "reward_timing": "Program-specific daily pools.",
        "competition": "Medium to high depending on target size and market liquidity.",
        "capital_intensity": "medium",
        "monitorability": "public_api",
        "execution_risk": "high",
        "automation_mode": "monitor_and_approval_ticket",
        "can_codex_capture": "No",
        "why_not_autonomous": "Capturing requires live orders that can fill and create regulated-market inventory risk.",
        "safe_next_action": "Rank pools from the public API and draft manual order tickets.",
        "official_urls": [
            "https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program",
            "https://external-api.kalshi.com/trade-api/v2/incentive_programs",
        ],
    },
    {
        "platform": "Polymarket US",
        "program": "Liquidity Incentive Program",
        "category": "prediction_market",
        "reward_type": "liquidity_pool",
        "source_status": "primary_verified",
        "safe_automation_score": 69,
        "required_work": "Place resting limit orders close to the best price on eligible Polymarket US contracts.",
        "reward_timing": "Daily reward periods, midnight-to-midnight ET.",
        "competition": "Medium to high depending on event and target size.",
        "capital_intensity": "medium_high",
        "monitorability": "public_docs",
        "execution_risk": "high",
        "automation_mode": "monitor_and_paper_score",
        "can_codex_capture": "No",
        "why_not_autonomous": "Requires live orders, possible fills, account access, and legal/TOS eligibility checks.",
        "safe_next_action": "Model reward density and adverse-selection risk before manual review.",
        "official_urls": [
            "https://docs.polymarket.us/incentives/overview",
            "https://docs.polymarket.us/incentives/liquidity",
            "https://docs.polymarket.us/api-reference/market/overview",
        ],
    },
    {
        "platform": "Polymarket US",
        "program": "Volume Incentive Program",
        "category": "prediction_market",
        "reward_type": "volume_pool",
        "source_status": "primary_verified",
        "safe_automation_score": 42,
        "required_work": "Execute eligible taker trades on reward-enabled contracts.",
        "reward_timing": "Contract-specific reward pools.",
        "competition": "High; rewards depend on share of eligible taker volume.",
        "capital_intensity": "medium_high",
        "monitorability": "public_docs",
        "execution_risk": "very_high",
        "automation_mode": "monitor_and_manual_no_go_by_default",
        "can_codex_capture": "No",
        "why_not_autonomous": "Requires filled trades and can incentivize uneconomic volume.",
        "safe_next_action": "Track reward pools as information only; reject unless net EV survives fees, spread, and slippage.",
        "official_urls": [
            "https://docs.polymarket.us/incentives/volume",
            "https://docs.polymarket.us/api-reference/market/overview",
        ],
    },
    {
        "platform": "Merkl",
        "program": "Live DeFi Campaigns",
        "category": "defi_incentive_aggregator",
        "reward_type": "lp_lending_airdrop_points",
        "source_status": "primary_verified",
        "safe_automation_score": 73,
        "required_work": "Participate in eligible DeFi campaigns after reviewing chain, action, APR, TVL, and risk.",
        "reward_timing": "Periodic campaign rewards.",
        "competition": "Varies by campaign.",
        "capital_intensity": "low",
        "monitorability": "public_api",
        "execution_risk": "high",
        "automation_mode": "monitor_rank_and_manual_wallet_ticket",
        "can_codex_capture": "No",
        "why_not_autonomous": "Capturing requires wallet signatures and on-chain transactions.",
        "safe_next_action": "Build campaign monitor and manual wallet-action tickets.",
        "official_urls": [
            "https://docs.merkl.xyz/merkl-mechanisms/incentive-mechanisms",
            "https://app.merkl.xyz/",
        ],
    },
]

FIELDS = [
    "platform",
    "program",
    "category",
    "reward_type",
    "source_status",
    "safe_automation_score",
    "required_work",
    "reward_timing",
    "competition",
    "capital_intensity",
    "monitorability",
    "execution_risk",
    "automation_mode",
    "can_codex_capture",
    "why_not_autonomous",
    "safe_next_action",
    "official_urls",
]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _load_records(source: Path) -> list[dict]:
    if not source.exists():
        return SEED_RECORDS

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return SEED_RECORDS

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        return SEED_RECORDS
    return _merge_seed_records(records)


def _merge_seed_records(records: list[dict]) -> list[dict]:
    """Keep reviewed records while refreshing seed-owned platform/program rows."""
    merged = {
        (str(row.get("platform", "")).lower(), str(row.get("program", "")).lower()): dict(row)
        for row in records
    }
    for row in SEED_RECORDS:
        merged[(str(row.get("platform", "")).lower(), str(row.get("program", "")).lower())] = dict(row)
    return list(merged.values())


def _normalize_urls(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        return [part for part in value.split() if part.startswith("http")]
    return []


def _short(text: str, max_len: int = 150) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def render_markdown(records: list[dict], generated_at: str, limit: int) -> str:
    ranked = sorted(records, key=lambda row: int(row.get("safe_automation_score") or 0), reverse=True)
    lines = [
        "# Market Rewards Platform Catalog",
        "",
        f"Generated: {generated_at}",
        "Safety boundary: Read-only discovery, ranking, monitoring, and approval-ticket generation only. No unattended trading, staking, lending, borrowing, bridging, wallet signing, order placement, claiming, referral spam, account creation, or KYC/account actions.",
        "",
        "## Thesis",
        "",
        "The best automation target is autonomous discovery, source verification, reward-density scoring, risk math, and approval-ready tickets. Programs that pay meaningful rewards usually require live orders, filled trades, wallet transactions, or account-level opt-ins, so capture remains behind an explicit manual gate.",
        "",
        "## Highest Safe Automation Value",
        "",
        "| Rank | Platform | Program | Score | Category | Capital | Risk | Safe automation | Required work |",
        "| ---: | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(ranked[:limit], start=1):
        lines.append(
            "| {rank} | {platform} | {program} | {score} | {category} | {capital} | {risk} | {mode} | {work} |".format(
                rank=idx,
                platform=row.get("platform", ""),
                program=row.get("program", ""),
                score=row.get("safe_automation_score", ""),
                category=row.get("category", ""),
                capital=row.get("capital_intensity", ""),
                risk=row.get("execution_risk", ""),
                mode=row.get("automation_mode", ""),
                work=_short(str(row.get("required_work", ""))).replace("|", "/"),
            )
        )

    lines.extend([
        "",
        "## Platform Catalog",
        "",
        "| Platform | Program | Reward type | Source | Can Codex capture? | Why not autonomous | Sources |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for row in ranked:
        urls = _normalize_urls(row.get("official_urls"))
        source_links = "<br>".join(f"[source]({url})" for url in urls) or "Needs primary-source verification"
        lines.append(
            "| {platform} | {program} | {rtype} | {status} | {capture} | {why} | {sources} |".format(
                platform=row.get("platform", ""),
                program=row.get("program", ""),
                rtype=row.get("reward_type", ""),
                status=row.get("source_status", ""),
                capture=row.get("can_codex_capture", "No"),
                why=_short(str(row.get("why_not_autonomous", ""))).replace("|", "/"),
                sources=source_links,
            )
        )

    counts: dict[str, int] = {}
    for row in records:
        category = str(row.get("category") or "unknown")
        counts[category] = counts.get(category, 0) + 1

    lines.extend(["", "## Category Counts", ""])
    for category, count in sorted(counts.items()):
        lines.append(f"- {category}: {count}")

    lines.extend([
        "",
        "## Automation Buildout",
        "",
        "1. Discovery monitor: refresh this catalog and the Kalshi public rewards digest on a schedule.",
        "2. Source verifier: flag programs with `secondary_needs_verification` until primary official docs are found.",
        "3. Reward scorer: estimate reward density, capital requirement, fees, gas, slippage, borrow/liquidation risk, and eligibility.",
        "4. Approval ticket: produce a human-review checklist with max loss, expected reward, required action, and exact source links.",
        "5. Execution gate: no live orders, account changes, wallet signatures, claims, or trades unless explicitly approved in a separate execution workflow.",
        "",
    ])
    return "\n".join(lines)


def write_csv(records: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in records:
            normalized = {field: row.get(field, "") for field in FIELDS}
            normalized["official_urls"] = " ".join(_normalize_urls(row.get("official_urls")))
            writer.writerow(normalized)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a read-only market rewards platform catalog.")
    parser.add_argument("--limit", type=int, default=12, help="Rows in the top-ranked table.")
    parser.add_argument("--source-json", type=Path, default=DEFAULT_JSON_OUTPUT, help="Existing reviewed catalog JSON to refresh from.")
    parser.add_argument("--output", type=Path, default=DEFAULT_MD_OUTPUT, help="Markdown output path.")
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV_OUTPUT, help="CSV output path.")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT, help="JSON output path.")
    parser.add_argument("--stdout-only", action="store_true", help="Print only; do not write files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    generated_at = _utc_now().isoformat(timespec="seconds")
    records = _load_records(args.source_json)
    rendered = render_markdown(records, generated_at, args.limit)

    print(rendered)
    if not args.stdout_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        write_csv(records, args.csv_output)
        args.json_output.write_text(
            json.dumps({"generated_at": generated_at, "records": records}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote {args.output}")
        print(f"Wrote {args.csv_output}")
        print(f"Wrote {args.json_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
