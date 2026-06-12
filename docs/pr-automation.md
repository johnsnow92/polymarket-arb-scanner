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

- Never `gh pr merge --admin` (blocked by a local PreToolUse hook anyway).
- Never force-push to `master` (blocked twice: deny rule + danger-blocker).
- Failing tests are fixed at the root cause, never skipped.

## New repo setup

```bash
bash ~/.claude/scripts/setup-repo-automations.sh [branch] [required-check]
```

Sets the branch protection above, enables repo auto-merge, and commits
`.coderabbit.yaml` to the default branch.
