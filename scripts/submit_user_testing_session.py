#!/usr/bin/env python3
"""Safely submit exactly one non-local user-testing session through Git."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_TESTING_RELATIVE = Path("data") / "user_testing"
REQUIRED_FIELDS = (
    "interaction_id",
    "session_id",
    "tester_id",
    "timestamp",
    "query",
    "raw_answer",
    "status",
    "review_status",
)
TESTER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
KNOWN_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "llm_api_key",
        "authorization",
        "authorization_header",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "password",
    }
)


class SubmissionError(ValueError):
    """A controlled submission failure."""


class PushError(SubmissionError):
    def __init__(self, commit: str):
        super().__init__("Push failed; the local commit was preserved. Check authentication, permissions, and remote state.")
        self.commit = commit


@dataclass(frozen=True)
class SessionInfo:
    absolute_path: Path
    relative_path: Path
    tester_id: str
    session_id: str
    interaction_count: int


@dataclass(frozen=True)
class SubmissionResult:
    session: SessionInfo
    branch: str
    remote: str
    commit: str | None
    push_status: str
    dry_run: bool


def _git(project_root: Path, arguments: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise SubmissionError("Git repository validation failed.")
    return completed


def _contains_secret_field(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in KNOWN_SECRET_FIELDS:
                return str(key)
            nested_match = _contains_secret_field(nested_value)
            if nested_match:
                return nested_match
    elif isinstance(value, list):
        for nested_value in value:
            nested_match = _contains_secret_field(nested_value)
            if nested_match:
                return nested_match
    return None


def validate_session(session_argument: str | Path, *, project_root: Path = PROJECT_ROOT) -> SessionInfo:
    """Validate an allowed, non-local session and its complete JSONL content."""
    project_root = project_root.resolve()
    user_testing_root = (project_root / USER_TESTING_RELATIVE).resolve()
    requested_path = Path(session_argument)
    candidate = (project_root / requested_path if not requested_path.is_absolute() else requested_path).resolve()
    try:
        relative_to_user_testing = candidate.relative_to(user_testing_root)
        relative_to_project = candidate.relative_to(project_root)
    except ValueError as error:
        raise SubmissionError("Session file must be inside data/user_testing/.") from error

    if not candidate.is_file():
        raise SubmissionError("Session path must be an existing file.")
    if len(relative_to_user_testing.parts) != 2:
        raise SubmissionError("Session file must be directly inside one tester directory.")
    tester_directory, filename = relative_to_user_testing.parts
    if tester_directory == "local_tester":
        raise SubmissionError("data/user_testing/local_tester/ is development-only and cannot be submitted.")
    if not TESTER_ID_PATTERN.fullmatch(tester_directory):
        raise SubmissionError("Tester directory name is not valid for submission.")
    if not filename.startswith("session_") or not filename.endswith(".jsonl"):
        raise SubmissionError("Session filename must match session_*.jsonl.")

    try:
        lines = candidate.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise SubmissionError("Session file must be UTF-8 readable.") from error

    interaction_ids: set[str] = set()
    session_id: str | None = None
    interaction_count = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise SubmissionError(f"Session JSONL is invalid at line {line_number}.") from error
        if not isinstance(record, dict):
            raise SubmissionError(f"Session JSONL line {line_number} must be a JSON object.")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise SubmissionError(f"Session JSONL line {line_number} is missing required fields.")
        if record.get("tester_id") != tester_directory:
            raise SubmissionError("Record tester_id does not match the tester directory.")
        record_session_id = record.get("session_id")
        if not isinstance(record_session_id, str) or not record_session_id:
            raise SubmissionError("Record session_id must be a non-empty string.")
        if session_id is None:
            session_id = record_session_id
        elif session_id != record_session_id:
            raise SubmissionError("All records in a session file must use the same session_id.")
        interaction_id = record.get("interaction_id")
        if not isinstance(interaction_id, str) or not interaction_id:
            raise SubmissionError("Record interaction_id must be a non-empty string.")
        if interaction_id in interaction_ids:
            raise SubmissionError("Duplicate interaction_id found in the session file.")
        secret_field = _contains_secret_field(record)
        if secret_field:
            raise SubmissionError(f"Session record contains prohibited secret field {secret_field!r}.")
        interaction_ids.add(interaction_id)
        interaction_count += 1

    if interaction_count == 0 or session_id is None:
        raise SubmissionError("Session file contains no interaction records.")
    if session_id != Path(filename).stem:
        raise SubmissionError("Record session_id does not match the session filename.")
    return SessionInfo(candidate, relative_to_project, tester_directory, session_id, interaction_count)


def _validate_git_repository(project_root: Path, remote: str) -> str:
    project_root = project_root.resolve()
    top_level = _git(project_root, ["rev-parse", "--show-toplevel"]).stdout.strip()
    if Path(top_level).resolve() != project_root:
        raise SubmissionError("Project root is not the active Git work tree.")
    branch = _git(project_root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if branch.returncode != 0 or not branch.stdout.strip():
        raise SubmissionError("Submission requires a checked-out branch.")
    remote_check = _git(project_root, ["remote", "get-url", remote], check=False)
    if remote_check.returncode != 0:
        raise SubmissionError(f"Requested remote {remote!r} does not exist.")
    return branch.stdout.strip()


def _staged_paths(project_root: Path) -> list[str]:
    staged = _git(project_root, ["diff", "--cached", "--name-only", "-z"]).stdout
    return [path for path in staged.split("\0") if path]


def _validate_index(project_root: Path, intended_path: str) -> None:
    unrelated = [path for path in _staged_paths(project_root) if path != intended_path]
    if unrelated:
        raise SubmissionError("Git index contains staged files other than the requested session file.")


def _verify_exact_staging(project_root: Path, intended_path: str) -> None:
    if _staged_paths(project_root) != [intended_path]:
        raise SubmissionError("Exact staging verification failed for the requested session file.")


def _is_ignored(project_root: Path, relative_path: str) -> bool:
    return _git(project_root, ["check-ignore", "--quiet", "--", relative_path], check=False).returncode == 0


def _already_committed(project_root: Path, relative_path: str) -> bool:
    tracked = _git(project_root, ["ls-files", "--error-unmatch", "--", relative_path], check=False)
    if tracked.returncode != 0:
        return False
    return _git(project_root, ["diff", "--quiet", "HEAD", "--", relative_path], check=False).returncode == 0


def _commit_files(project_root: Path, commit: str) -> list[str]:
    changed = _git(project_root, ["diff-tree", "--no-commit-id", "--name-only", "-r", commit]).stdout.splitlines()
    return [path for path in changed if path]


def submit_session(
    *,
    session_argument: str | Path,
    remote: str,
    dry_run: bool = False,
    no_push: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> SubmissionResult:
    """Validate, exact-stage, commit, and optionally push one tester session."""
    project_root = project_root.resolve()
    session = validate_session(session_argument, project_root=project_root)
    branch = _validate_git_repository(project_root, remote)
    relative_path = session.relative_path.as_posix()
    _validate_index(project_root, relative_path)
    if _is_ignored(project_root, relative_path):
        raise SubmissionError("Requested session file is ignored and cannot be submitted.")
    if _already_committed(project_root, relative_path):
        return SubmissionResult(session, branch, remote, None, "nothing to submit", dry_run)

    commit_message = f"user-testing: submit {session.tester_id} {session.session_id}"
    if dry_run:
        return SubmissionResult(session, branch, remote, None, "dry run", True)

    _git(project_root, ["add", "--", relative_path])
    _verify_exact_staging(project_root, relative_path)
    _git(project_root, ["commit", "-m", commit_message])
    commit = _git(project_root, ["rev-parse", "HEAD"]).stdout.strip()
    if _commit_files(project_root, commit) != [relative_path]:
        raise SubmissionError("Commit safety verification failed; inspect the local commit before pushing.")
    if no_push:
        return SubmissionResult(session, branch, remote, commit, "not requested", False)
    pushed = _git(project_root, ["push", remote, f"{branch}:{branch}"], check=False)
    if pushed.returncode != 0:
        raise PushError(commit)
    return SubmissionResult(session, branch, remote, commit, "pushed", False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="Session JSONL path under data/user_testing/<tester_id>/.")
    parser.add_argument("--remote", required=True, help="Existing Git remote to receive the current branch.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show the planned submission without Git mutation.")
    parser.add_argument("--no-push", action="store_true", help="Create the validated local commit but do not push it.")
    return parser.parse_args()


def _print_summary(result: SubmissionResult) -> None:
    print("Submission prepared" if result.dry_run or result.commit else "Submission succeeded")
    print(f"Tester: {result.session.tester_id}")
    print(f"Session: {result.session.session_id}")
    print(f"File: {result.session.relative_path.as_posix()}")
    print(f"Interactions: {result.session.interaction_count}")
    print(f"Commit: {result.commit or '(none)'}")
    print(f"Remote: {result.remote}/{result.branch}")
    print(f"Push status: {result.push_status}")
    if result.dry_run:
        print(f"Would stage: {result.session.relative_path.as_posix()}")
        print(f"Would commit: user-testing: submit {result.session.tester_id} {result.session.session_id}")


def main() -> int:
    args = parse_args()
    try:
        result = submit_session(
            session_argument=args.session,
            remote=args.remote,
            dry_run=args.dry_run,
            no_push=args.no_push,
        )
    except PushError as error:
        print(f"Submission failed: {error}", file=sys.stderr)
        print(f"Local commit preserved: {error.commit}", file=sys.stderr)
        return 1
    except SubmissionError as error:
        print(f"Submission failed: {error}", file=sys.stderr)
        return 1
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
