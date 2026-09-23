"""Append-only JSONL logging for real user-testing sessions (V1)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USER_TESTING_ROOT = PROJECT_ROOT / "data" / "user_testing"
PROMPT_VERSION = "grounded_answer_v1"
DEFAULT_TESTER_ID = "local_tester"
FEEDBACK_CATEGORIES = frozenset(
    {
        "incorrect",
        "unsupported_claim",
        "incomplete",
        "irrelevant",
        "wrong_source",
        "unclear",
        "formatting",
        "other",
    }
)


class FeedbackPersistenceError(ValueError):
    """A controlled failure while updating a user-testing feedback record."""


def sanitize_tester_id(value: str | None) -> str:
    """Return a conservative directory-safe tester identifier."""
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "").strip())
    normalized = normalized.strip("_-")
    return normalized[:64] or DEFAULT_TESTER_ID


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_session_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"session_{timestamp}_{secrets.token_hex(3)}"


def _repository_head(repository_path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _evidence_metadata(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "repository": source.get("repository"),
            "source_path": source.get("source_path"),
            "source_role": source.get("source_role"),
            "symbol_or_section_title": source.get("symbol_or_section_title"),
            "line_start": source.get("start_line"),
            "line_end": source.get("end_line"),
        }
        for rank, source in enumerate(sources, start=1)
    ]


def _validate_session_file(session_file: Path, user_testing_root: Path) -> Path:
    root = user_testing_root.resolve()
    candidate = session_file.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise FeedbackPersistenceError("The interaction session is not available for feedback.") from error
    return candidate


@contextmanager
def _session_lock(session_file: Path):
    """Use one stable lock path for both append and atomic replacement."""
    lock_file = session_file.with_name(f".{session_file.name}.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _update_feedback_fields(
    *,
    session_file: Path,
    interaction_id: str,
    changes: dict[str, Any],
    user_testing_root: Path,
) -> dict[str, Any]:
    """Atomically update only an existing interaction's feedback fields."""
    session_file = _validate_session_file(session_file, user_testing_root)
    if not interaction_id:
        raise FeedbackPersistenceError("No logged interaction is available for feedback.")
    if not session_file.is_file():
        raise FeedbackPersistenceError("The interaction session is not available for feedback.")

    temporary_path: Path | None = None
    with _session_lock(session_file):
        try:
            with session_file.open("r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
        except (OSError, json.JSONDecodeError) as error:
            raise FeedbackPersistenceError("The interaction session could not be updated safely.") from error
        if not all(isinstance(record, dict) for record in records):
            raise FeedbackPersistenceError("The interaction session could not be updated safely.")

        matches = [record for record in records if record.get("interaction_id") == interaction_id]
        if len(matches) != 1:
            message = (
                "No logged interaction is available for feedback."
                if not matches
                else "The interaction session could not be updated safely."
            )
            raise FeedbackPersistenceError(message)

        matches[0].update(changes)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{session_file.name}.", suffix=".tmp", dir=session_file.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, session_file)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return matches[0]


def mark_interaction_helpful(
    *,
    session_file: Path,
    interaction_id: str,
    user_testing_root: Path = DEFAULT_USER_TESTING_ROOT,
) -> dict[str, Any]:
    """Persist a Helpful rating without changing other feedback fields."""
    return _update_feedback_fields(
        session_file=session_file,
        interaction_id=interaction_id,
        changes={"rating": "helpful"},
        user_testing_root=user_testing_root,
    )


def save_needs_review_feedback(
    *,
    session_file: Path,
    interaction_id: str,
    category: str,
    comment: str | None,
    user_testing_root: Path = DEFAULT_USER_TESTING_ROOT,
) -> dict[str, Any]:
    """Persist review feedback while retaining any suggested correction."""
    if category not in FEEDBACK_CATEGORIES:
        raise FeedbackPersistenceError("Select a valid feedback category before saving.")
    return _update_feedback_fields(
        session_file=session_file,
        interaction_id=interaction_id,
        changes={
            "rating": "needs_review",
            "feedback_category": category,
            "feedback_comment": comment.strip() if comment and comment.strip() else None,
        },
        user_testing_root=user_testing_root,
    )


def save_suggested_correction(
    *,
    session_file: Path,
    interaction_id: str,
    correction: str,
    user_testing_root: Path = DEFAULT_USER_TESTING_ROOT,
) -> dict[str, Any]:
    """Persist a non-empty correction while retaining review feedback fields."""
    if not correction or not correction.strip():
        raise FeedbackPersistenceError("Enter a correction before saving.")
    return _update_feedback_fields(
        session_file=session_file,
        interaction_id=interaction_id,
        changes={"rating": "needs_review", "suggested_correction": correction.strip()},
        user_testing_root=user_testing_root,
    )


def append_successful_interaction(
    *,
    session_state: dict[str, Any] | None,
    tester_id: str | None,
    query: str,
    raw_answer: str,
    sources: list[dict[str, Any]],
    model: str | None,
    base_url: str | None,
    top_k: int,
    user_testing_root: Path = DEFAULT_USER_TESTING_ROOT,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Append one successful interaction and return updated session state."""
    safe_tester_id = sanitize_tester_id(tester_id)
    if session_state and session_state.get("tester_id") == safe_tester_id:
        active_session = dict(session_state)
    else:
        active_session = {
            "tester_id": safe_tester_id,
            "session_id": _new_session_id(),
            "sequence": 0,
        }

    sequence = int(active_session.get("sequence", 0)) + 1
    session_id = str(active_session["session_id"])
    interaction_id = f"{safe_tester_id}_{session_id}_{sequence:04d}"
    record = {
        "interaction_id": interaction_id,
        "session_id": session_id,
        "tester_id": safe_tester_id,
        "timestamp": _timestamp(),
        "query": query,
        "raw_answer": raw_answer,
        "rendered_answer": None,
        "sources": sources,
        "retrieved_evidence_metadata": _evidence_metadata(sources),
        "model": model,
        "base_url": base_url,
        "top_k": top_k,
        "prompt_version": PROMPT_VERSION,
        "retrieval_version": None,
        "chunking_version": None,
        "status": "successful",
        "rating": None,
        "feedback_category": None,
        "feedback_comment": None,
        "suggested_correction": None,
        "review_status": "unreviewed",
        "sphinxsys_commit": _repository_head(PROJECT_ROOT / "repos" / "SPHinXsys"),
        "sphinxsim_commit": _repository_head(PROJECT_ROOT / "repos" / "SPHinXsim"),
    }

    session_file = user_testing_root / safe_tester_id / f"{session_id}.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    with _session_lock(session_file):
        with session_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    active_session["sequence"] = sequence
    return active_session, session_file, record
