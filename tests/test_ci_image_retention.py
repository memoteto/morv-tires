"""Check conservative retention decisions for generated CI image branches."""
import unittest
from datetime import UTC, datetime

from tools.prune_ci_images import eligible


class CiImageRetentionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 28, tzinfo=UTC)
        self.source = "a" * 40
        self.sha = "b" * 40
        self.name = f"ui-audit-images-123-1-{self.source}"
        self.commit = {"sha": self.sha, "parents": [], "commit": {
            "author": {"name": "ui-audit", "email": "ui-audit@users.noreply.github.com"},
            "committer": {"date": "2026-10-01T00:00:00Z"}}}
        self.meta = {"source_sha": self.source, "run_id": "123", "run_attempt": "1"}
        self.run = {"id": 123, "head_sha": self.source, "head_branch": "phase-closed",
                    "status": "completed", "run_attempt": 1, "updated_at": "2026-10-01T00:00:00Z"}

    def allowed(self, main="", heads=(), branches=()):
        return eligible(self.name, self.sha, self.commit, self.meta, self.run,
                        main, set(heads), set(branches), self.now)

    def test_old_verified_image_is_eligible(self):
        self.assertTrue(self.allowed())

    def test_current_main_and_open_pull_requests_are_preserved(self):
        self.assertFalse(self.allowed(main=self.source))
        self.assertFalse(self.allowed(heads=[self.source]))
        self.assertFalse(self.allowed(branches=["phase-closed"]))

    def test_recent_or_active_run_is_preserved(self):
        self.run["updated_at"] = "2026-11-20T00:00:00Z"
        self.assertFalse(self.allowed())
        self.run["updated_at"] = "2026-10-01T00:00:00Z"
        self.run["status"] = "in_progress"
        self.assertFalse(self.allowed())

    def test_recent_image_from_old_run_is_preserved(self):
        self.commit["commit"]["committer"]["date"] = "2026-11-20T00:00:00Z"
        self.assertFalse(self.allowed())

    def test_source_branch_and_mismatched_metadata_are_preserved(self):
        name = self.name
        self.name = "phase-ui-audit-images"
        self.assertFalse(self.allowed())
        self.name = name
        self.meta["run_id"] = "999"
        self.assertFalse(self.allowed())

    def test_non_generated_commit_is_preserved(self):
        self.commit["parents"] = [{"sha": "c" * 40}]
        self.assertFalse(self.allowed())
