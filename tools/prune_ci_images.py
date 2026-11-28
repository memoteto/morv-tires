"""Prune verified, expired CI image branches without touching source branches."""
import base64
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta

IMAGE = re.compile(r"(ui-audit-images|print-images-win)-([1-9][0-9]*)-([1-9][0-9]*)-([0-9a-f]{40})")
RETENTION = timedelta(days=30)


def api(path):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def pages(path):
    result = subprocess.run(["gh", "api", "--paginate", "--slurp", path],
                            check=True, capture_output=True, text=True)
    return [item for page in json.loads(result.stdout) for item in page]


def date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def eligible(name, sha, commit, metadata, run, main, open_heads, open_branches, now):
    """Require an expired, generated image commit belonging to a completed, unprotected run."""
    match = IMAGE.fullmatch(name)
    if match is None:
        return False
    prefix, run_id, attempt, source = match.groups()
    if source == main or source in open_heads or run.get("head_branch") in open_branches:
        return False
    author = "ui-audit" if prefix == "ui-audit-images" else "print-samples"
    if (commit["sha"] != sha or commit["parents"] or commit["commit"]["author"]["name"] != author
            or commit["commit"]["author"]["email"] != f"{author}@users.noreply.github.com"
            or date(commit["commit"]["committer"]["date"]) > now - RETENTION):
        return False
    return (metadata.get("source_sha") == source
            and metadata.get("run_id") == run_id and metadata.get("run_attempt") == attempt
            and run.get("id") == int(run_id) and run.get("head_sha") == source
            and run.get("status") == "completed"
            and run.get("run_attempt", 0) >= int(attempt)
            and date(run["updated_at"]) <= now - RETENTION)


def protection(repo):
    main = api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    pulls = pages(f"repos/{repo}/pulls?state=open&per_page=100")
    return main, {p["head"]["sha"] for p in pulls}, {p["head"]["ref"] for p in pulls}


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    now = datetime.now(UTC)
    dry_run = os.environ.get("DRY_RUN", "true") != "false"
    branches = pages(f"repos/{repo}/branches?per_page=100")
    for branch in branches:
        name, sha = branch["name"], branch["commit"]["sha"]
        match = IMAGE.fullmatch(name)
        if match is None or branch["protected"]:
            continue
        try:
            commit = api(f"repos/{repo}/commits/{sha}")
            if date(commit["commit"]["committer"]["date"]) > now - RETENTION:
                continue
            payload = api(f"repos/{repo}/contents/source-commit.txt?ref={sha}")
            metadata = dict(line.split("=", 1) for line in
                            base64.b64decode(payload["content"]).decode("utf-8").splitlines())
            run = api(f"repos/{repo}/actions/runs/{match[2]}")
            main_sha, heads, refs = protection(repo)
            if not eligible(name, sha, commit, metadata, run, main_sha, heads, refs, now):
                continue
        except (subprocess.CalledProcessError, KeyError, ValueError, TypeError) as exc:
            print(f"Preserved {name}: verification unavailable ({type(exc).__name__})", flush=True)
            continue
        print(f"{'Would delete' if dry_run else 'Deleting'} {name}", flush=True)
        if not dry_run:
            ref = f"refs/heads/{name}"
            # Lease prevents deleting a ref that changed since the verification above.
            subprocess.run(["git", "push", f"--force-with-lease={ref}:{sha}", "origin", f":{ref}"],
                           check=True)


if __name__ == "__main__":
    main()
