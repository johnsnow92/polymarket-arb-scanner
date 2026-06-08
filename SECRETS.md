# Secrets management (Infisical)

This project reads **every** credential through `os.getenv` in `config.py`. That
means a secrets manager can inject them as environment variables with **zero code
changes** — `config.py` neither knows nor cares whether the values came from a
local `.env`, the shell, or Infisical.

We use [Infisical](https://infisical.com) as the source of truth for secrets.
Local `.env` files are being retired (the 2026-06 machine migration showed how
fragile they are — they live in exactly one place and silently vanish).

## One-time setup

```bash
# 1. Authenticate the CLI (once per machine)
infisical login

# 2. Link this repo to its Infisical project (writes .infisical.json — safe to commit,
#    it contains only the workspace + default-environment IDs, no secrets)
cd ~/Dev/polymarket-arb-scanner
infisical init
```

## Migrating the existing secrets into Infisical

Do this yourself — these values are live credentials. Two options:

- **Dashboard (recommended):** open the project in the Infisical web UI →
  **Secrets → Add → Import .env** and drag in your existing `.env`. Put local
  values in the `dev` environment and production values in `prod`.
- **CLI, per key:** `infisical secrets set POLYMARKET_PRIVATE_KEY="..." --env=dev`

Once the secrets are in Infisical and verified, delete the local `.env`
(it's already gitignored, so it was never in version control).

## Daily local development

```bash
# Inject dev secrets for the duration of one command
infisical run --env=dev -- python scanner.py
infisical run --env=dev -- python scanner.py --continuous --interval 60
infisical run --env=dev -- pytest tests/ -v

# Inspect what's set without revealing values in shell history
infisical secrets --env=dev
```

No `python-dotenv` / `.env` required — `infisical run` populates the environment
before the process starts, and `config.py`'s `os.getenv` calls pick it up.

## Production (Railway)

Prefer the **native Infisical → Railway sync integration** (Infisical dashboard →
Integrations → Railway): Infisical pushes the `prod` secrets straight into the
Railway service's environment. Nothing changes in the Dockerfile or entrypoint,
and the container needs neither the Infisical CLI nor an Infisical token.

If you'd rather pull at runtime instead of syncing, create a **Machine Identity**
(Universal Auth) in Infisical, set its access token as `INFISICAL_TOKEN` in
Railway, and change the container entrypoint to:

```bash
infisical run --projectId <project-id> --env=prod -- python scanner.py --continuous
```

## CI (GitHub Actions)

The test workflow mocks external SDKs and does not need real credentials. If a
future job needs secrets, use the official action with a machine identity:

```yaml
- uses: Infisical/secrets-action@v1
  with:
    method: universal
    client-id:     ${{ secrets.INFISICAL_CLIENT_ID }}
    client-secret: ${{ secrets.INFISICAL_CLIENT_SECRET }}
    env-slug: prod
    project-slug: <project-slug>
```

## Leak scanning

Catch accidentally-committed secrets before they land:

```bash
infisical scan                      # scan the whole git history
infisical scan git-changes          # scan only staged/uncommitted changes (use as a pre-commit hook)
```

## Rolling this out to the rest of the workspace

Use **one Infisical project per repo** (clean per-project access and machine
identities) with `dev` + `prod` environments each. Repeat the
`infisical init` → import-`.env` → `infisical run` flow in each `~/Dev` and
`~/Business` project. Add `infisical scan git-changes` as a global pre-commit
hook so no repo can reintroduce plaintext secrets.
