"""Focused, network-free tests for the upstream update checker."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_upstream_updates.py"
SPEC = importlib.util.spec_from_file_location("check_upstream_updates", SCRIPT_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

VALIDATED = "a" * 40
LATEST = "b" * 40


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git", "ls-remote"], returncode, stdout=stdout, stderr=stderr)


class UpstreamUpdateCheckerTests(unittest.TestCase):
    def write_config(self, content: object) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "baseline.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def config(self, baseline_commit: str = VALIDATED) -> dict[str, object]:
        return {
            "Example": {
                "remote": "https://example.invalid/repository.git",
                "branch": "main",
                "baseline_commit": baseline_commit,
            }
        }

    def test_equal_commit_reports_no_update(self) -> None:
        runner = Mock(return_value=completed(f"{VALIDATED}\trefs/heads/main\n"))
        report = checker.check_updates(self.write_config(self.config()), runner)
        self.assertFalse(report["Example"]["update_available"])
        self.assertEqual(report["Example"]["latest_commit"], VALIDATED)

    def test_different_commit_reports_update(self) -> None:
        runner = Mock(return_value=completed(f"{LATEST}\trefs/heads/main\n"))
        report = checker.check_updates(self.write_config(self.config()), runner)
        self.assertTrue(report["Example"]["update_available"])
        self.assertEqual(report["Example"]["latest_commit"], LATEST)

    def test_missing_or_malformed_remote_result_fails(self) -> None:
        config = self.write_config(self.config())
        for output in ("", "not-a-sha\trefs/heads/main\n", f"{LATEST}\trefs/heads/other\n"):
            with self.subTest(output=output):
                with self.assertRaises(checker.UpstreamCheckError):
                    checker.check_updates(config, Mock(return_value=completed(output)))

    def test_git_failure_fails_clearly(self) -> None:
        runner = Mock(return_value=completed("", returncode=2, stderr="remote unavailable"))
        with self.assertRaises(checker.UpstreamCheckError):
            checker.check_updates(self.write_config(self.config()), runner)

    def test_malformed_or_missing_required_config_fails(self) -> None:
        malformed = self.write_config({"Example": {"remote": "https://example.invalid/repository.git"}})
        with self.assertRaises(checker.UpstreamCheckError):
            checker.check_updates(malformed, Mock())
        invalid_sha = self.write_config(self.config("not-a-sha"))
        with self.assertRaises(checker.UpstreamCheckError):
            checker.check_updates(invalid_sha, Mock())
        invalid_json = self.write_config("not used")
        invalid_json.write_text("{", encoding="utf-8")
        with self.assertRaises(checker.UpstreamCheckError):
            checker.check_updates(invalid_json, Mock())


if __name__ == "__main__":
    unittest.main()
