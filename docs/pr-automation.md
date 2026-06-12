# PR Automation Pipeline

How pull requests get reviewed and merged in this repo. No manual merge step —
the gates do the gatekeeping.

## Flow

1. **PR created** (usually by Claude Code). A local PostToolUse hook immediately
   arms GitHub native auto-merge (`gh pr merge --auto --squash --delete-branch`).
   The PR cannot merge yet — the gates below are unsatisfied.
2. **Reviews run automatically:**
   - **CodeRabbit** reviews every push. With `request_changes_workflow: true`
     (see `.coderabbit.yaml`), it submits a formal *Request changes* review
     while findings are open and flips to *Approve* once all comments are
     resolved.
   - **GitHub Copilot** code review adds a second, advisory lens (it never
     approves or blocks).
3. **Fix loop:** `/autofix` collects all reviewer findings, verifies each
   against current code, fixes the valid ones, runs the test suite, pushes,
   and repeats until the review converges (max 5 rounds).
4. **Merge fires automatically** when every gate is green.

## Gates (branch protection on `master`)

| Gate | Setting |
|------|---------|
| CI | `test` check required, strict (branch must be up to date) |
| Review | 1 approval required; CodeRabbit's formal approval counts |
| Staleness | approvals dismissed on every new push |
| Threads | all review conversations must be resolved |
| Admins | `enforce_admins` on — no direct pushes to `master`, no admin bypass |

## Hard rules

- Never `gh pr merge --admin` — a local Claude Code PreToolUse hook
  (`~/.claude/hooks/danger-blocker.js`) pattern-matches the command and rejects
  it before it executes, so the branch-protection bypass can't be used.
- Never force-push to `master` — blocked twice locally: a permission deny rule
  in Claude Code's `settings.local.json` rejects `git push --force`, and the
  same danger-blocker hook blocks force pushes that target protected branches.
- Failing tests are fixed at the root cause, never skipped.

## New repo setup

```bash
# bash ~/.claude/scripts/setup-repo-automations.sh [branch] [required-check]
bash ~/.claude/scripts/setup-repo-automations.sh master test
```

- `branch` — the branch to protect (defaults to the repo's default branch).
- `required-check` — the name of the CI check that must pass before merge,
  matching the job name in your workflow file (defaults to `test`, the job
  name in `.github/workflows/test.yml`).

Sets the branch protection above, enables repo auto-merge, and commits
`.coderabbit.yaml` to the default branch.
