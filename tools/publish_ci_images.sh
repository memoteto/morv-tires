#!/usr/bin/env bash
# Publish one immutable image branch per workflow run, attempt and source commit.
set -euo pipefail

output_dir="$1"
prefix="$2"
author="$3"
image_branch="${prefix}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${SOURCE_SHA}"
git check-ref-format --branch "$image_branch"
cd "$output_dir"
printf 'source_ref=%s\nsource_sha=%s\ncheckout_sha=%s\nrun_id=%s\nrun_attempt=%s\n' \
  "$SOURCE_REF" "$SOURCE_SHA" "$GITHUB_SHA" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" > source-commit.txt
git init -q -b "$image_branch"
git config user.name "$author"
git config user.email "$author@users.noreply.github.com"
git add -A
git commit -q -m "$prefix from $SOURCE_REF @ $SOURCE_SHA"
git push "https://x-access-token:${AUDIT_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "HEAD:refs/heads/$image_branch"
printf '### %s\n- [Images for this run and attempt](%s/%s/tree/%s)\n- Source: `%s`\n- Checkout: `%s`\n' \
  "$prefix" "$GITHUB_SERVER_URL" "$GITHUB_REPOSITORY" "$image_branch" "$SOURCE_SHA" "$GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
