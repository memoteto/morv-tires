---
name: morv-pr-merge
description: Use for reviewed Morv Tires changes on isolated branches to open a PR, verify branch and PR CI, and merge only with explicit owner approval.
---

# Branch, PR, CI and merge

Follow the approval, scope, and verification rules in `CLAUDE.md`. A green check is evidence, not authorization to merge.

## Steps
1. Verify branch, commit, current main, and open PR scopes. Use a dedicated branch from an up-to-date main; never edit main directly or discard unrelated work. Review the exact diff with `morv-review`.
2. Run targeted lint and regression tests. Rely on full CI when sufficient; run the full local suite only for a demonstrated need. A deliberately failing diagnostic test may be pushed on an isolated branch, never merged while failing.
3. Commit in English with accurate attribution and a current session link only if available. Do not copy stale identities or session URLs.
4. Push the branch and verify that CI for that exact head completed successfully. Open a PR with the scope, test evidence, limitations and risks. Use `gh api` if `gh pr create` is unavailable.
5. Verify the PR-specific workflow as well as branch CI for the current head. Inspect actual jobs, including lint, both OS test jobs, audit, ui-audit, Windows build and any additional applicable checks. A pending, failed, cancelled or skipped required job is not success. Read relevant failure logs and fix the root cause within the approved scope. Report the current status if checks are still running; do not claim success.
6. Before merging: verify the current head and main, clean mergeability, all applicable checks and review findings, and explicit owner authorization for this PR. For UI changes require the owner's actual-device visual approval per `CLAUDE.md`. If the tested commit or base changed, verify the updated diff and rerun the required checks; seek renewed approval if the approved scope changed.
7. Use the agreed merge method with an expected head SHA. Verify the merge response and new main commit. Verify CI on that main commit before starting the next stage.
8. Report what changed, what passed, what remains and any risk that affects acceptance.

## Rules
- No automatic merge from green CI, prior approvals of different PRs, or skill invocation alone.
- No unresolved critical or accounting, stock, security or data-integrity issue at merge.
- No force-push or destructive cleanup; do not change unrelated files or weaken tests.
- Green CI does not prove absence of defects or correct rendered layout.
