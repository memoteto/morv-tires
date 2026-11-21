---
name: morv-pr-merge
description: Use after a Morv Tires change is committed locally and reviewed, to branch, push, open the pull request with gh api, wait for every CI job, and squash merge. Use only when a review found no unresolved critical issue.
---

# Branch, PR, CI and merge

Owner decision (CLAUDE.md, 2026-11-16, reconfirmed): merge directly without asking, but only when every check is green and `morv-review` left no unresolved critical finding. If either condition fails, stop and report; do not merge.

## Steps
1. Before pushing run `ruff check morv tests` and `python run_tests.py` locally. Both must pass. Do not push a known failure.
2. Branch from updated `origin/main` (`git checkout main && git pull origin main && git checkout -b <feature/...>`). One step per branch.
3. Commit message in English. End with exactly:
   `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
   `Claude-Session: https://claude.ai/code/session_011Q8V1gQHL4yrCfAb5ZDZqv`
4. Push, then open the PR with `gh api repos/memoteto/morv-tires/pulls -f title=... -f head=... -f base=main -f body=...` (`gh pr create` is blocked). Body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and the session URL.
5. Poll `gh api repos/memoteto/morv-tires/commits/$sha/check-runs` until nothing is `in_progress` or `queued`. Jobs: lint, test (ubuntu), test (windows), build-windows (10 to 15 minutes). A run longer than the tool timeout goes to the background; keep polling with a fresh command.
6. If a job fails, read its log (`actions/jobs/$id/logs`), fix the root cause with `morv-bugfix`, push, and poll again. Never disable or loosen a test.
7. Only when all four jobs are `success`: `gh api -X PUT repos/memoteto/morv-tires/pulls/N/merge -f merge_method=squash`.
8. Report in at most 3 lines: what changed, what was tested, next step. Add any residual risk that matters for the merge decision, even if it makes the report longer.

## Rules
- A skipped `build-windows` is not success; wait for it.
- Green CI is not proof the code is free of defects; say what CI does not cover (UI visual layout, security review).
- Never force-push, never delete branches or files, never merge a red or pending PR.

## Good example 1
All four jobs `success`, review found no critical issue: squash merge, reply "merged #58; 692 tests passed locally and in CI; next: lock-screen warning".

## Good example 2
Ubuntu fails on a UI test using a future date: read the log, fix the test date, push, wait for all four jobs again, then merge.

## Bad example
Merging while `test (windows)` is still `in_progress` because ubuntu passed. Why it is bad: a Windows-only failure lands on `main` and the owner ships a broken build.
