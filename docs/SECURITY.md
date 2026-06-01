# Security

> **Owner:** Jonathon Tamm · **Review cadence:** monthly, and on any change to secret handling or dashboard exposure.
> Each control below is tagged **[ENFORCED]** (with the code/Railway evidence that backs it) or **[ASPIRATIONAL/TODO]**. Do not read a TODO as an active guarantee.

## Secrets handling
- **[ENFORCED]** All credentials are env vars (`config.py`); nothing is hard-coded. `.env`, `.firecrawl/`, `decisions.jsonl` are git-ignored (PRs #23/#24).
- **[ENFORCED]** `validate_config()` runs at import and refuses startup when required credentials/config are missing or inconsistent (`config.py`, multiple `raise ConfigError`).
- **[ASPIRATIONAL/TODO]** Automated secret rotation. Today rotation is manual per platform (see `PLATFORM-MATRIX.md` authz columns). No rotation schedule is enforced in code.
- **[ASPIRATIONAL/TODO]** Secret-scanning pre-commit hook in this repo. (Global tooling exists outside the repo; not wired into CI here.)

## Custody-grade keys
- **[ENFORCED — design]** The only programmatic fund movement is the Gemini↔Polymarket USDC corridor, behind `AUTO_REBALANCE_ENABLED` (default off).
- **[RISK — documented]** Polymarket (wallet private key) and Gemini (API key+secret) are **not** trade/withdraw-separated — the same secret can move funds. Treat both as custody-grade. Compromise = the "Polymarket rewards-wallet drain" class of incident (a legacy private-key compromise drained a rewards wallet industry-side in 2026). Mitigations: minimal on-platform balances, the corridor stays default-off, keys never committed.

## Dashboard exposure
- **[ENFORCED]** `DASHBOARD_HOST` defaults to `127.0.0.1` (loopback). `DASHBOARD_PASS` default is empty (= no auth, loopback only).
- **[ENFORCED]** `validate_config()` raises `ConfigError` if `DASHBOARD_HOST` is non-loopback (e.g. `0.0.0.0` for Railway) while `DASHBOARD_PASS` is empty — you cannot expose the dashboard publicly without a password.
- **[ENFORCED]** Dashboard XSS surface in `dashboard_ui.py` was closed (Sprint 1, PR #28) — DOM construction via `textContent`, regression-guarded by `tests/test_dashboard_ui.py`.

## Error monitoring & redaction
- **[ENFORCED]** Sentry is wired at scanner entry points (PR #26).
- **[ASPIRATIONAL/TODO]** A formal log-redaction policy (guaranteeing no secret/PII reaches logs or Sentry). Logging uses `%`-style formatting and avoids logging secrets by convention, but there is no enforced redaction filter. Treat as a gap to close.

## Committed-artifact data boundary
Audit/research docs under `docs/audit/` cite only public URLs; no tokens, wallet addresses, account IDs, or credentialed responses are committed. The `gh`-sourced `CHANGELOG.md` uses public PR titles/numbers only.
