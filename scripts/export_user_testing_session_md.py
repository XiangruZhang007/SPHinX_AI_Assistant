#!/usr/bin/env python3
"""Export validated user-testing JSONL sessions as deterministic Markdown reports."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_TESTING_ROOT = PROJECT_ROOT / "data" / "user_testing"
EXPORTS_DIRNAME = "markdown_exports"
EXCLUDED_BATCH_DIRECTORIES = frozenset({"review_batches", EXPORTS_DIRNAME})
DEVELOPMENT_ONLY_TESTERS = frozenset({"local_tester"})
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
CATEGORY_LABELS = {
    "unsupported_claim": "Unsupported Claim",
    "incomplete": "Incomplete",
    "irrelevant": "Irrelevant",
    "incorrect": "Incorrect",
    "wrong_source": "Wrong Source",
    "unclear": "Unclear",
    "formatting": "Formatting",
    "other": "Other",
    "__uncategorized__": "Uncategorized",
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)


class ExportError(ValueError):
    """A controlled export failure that leaves source session JSONL unchanged."""


@dataclass(frozen=True)
class SessionData:
    source_path: Path
    tester_id: str
    session_id: str
    records: list[dict[str, Any]]


def _not_recorded(value: Any) -> str:
    return "Not recorded" if value is None or value == "" else str(value)


def _resolve_under_user_testing(path_value: str | Path, user_testing_root: Path) -> Path:
    candidate = Path(path_value)
    candidate = candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)
    candidate = candidate.resolve()
    try:
        candidate.relative_to(user_testing_root.resolve())
    except ValueError as error:
        raise ExportError("Session path must be inside data/user_testing/.") from error
    return candidate


def _validate_session_path(path_value: str | Path, user_testing_root: Path) -> Path:
    path = _resolve_under_user_testing(path_value, user_testing_root)
    try:
        relative = path.relative_to(user_testing_root.resolve())
    except ValueError as error:
        raise ExportError("Session path must be inside data/user_testing/.") from error
    if len(relative.parts) != 2 or relative.parts[0] in EXCLUDED_BATCH_DIRECTORIES:
        raise ExportError("Session file must be directly inside a tester directory.")
    if not path.name.startswith("session_") or path.suffix != ".jsonl":
        raise ExportError("Session filename must match session_*.jsonl.")
    if path.is_symlink() or not path.exists() or not stat.S_ISREG(path.stat().st_mode):
        raise ExportError("Session path must be an existing regular file.")
    return path


def load_session(path_value: str | Path, *, user_testing_root: Path = USER_TESTING_ROOT) -> SessionData:
    """Load one complete session without changing source order or content."""
    session_path = _validate_session_path(path_value, user_testing_root)
    try:
        lines = session_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ExportError(f"Could not read UTF-8 session file: {session_path}.") from error

    records: list[dict[str, Any]] = []
    interaction_ids: set[str] = set()
    tester_id: str | None = None
    session_id: str | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ExportError(f"{session_path}:{line_number}: invalid JSON.") from error
        if not isinstance(record, dict):
            raise ExportError(f"{session_path}:{line_number}: JSONL value must be an object.")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ExportError(f"{session_path}:{line_number}: missing required field(s): {', '.join(missing)}.")
        record_tester_id = record["tester_id"]
        record_session_id = record["session_id"]
        interaction_id = record["interaction_id"]
        if not all(isinstance(value, str) and value for value in (record_tester_id, record_session_id, interaction_id)):
            raise ExportError(f"{session_path}:{line_number}: identifiers must be non-empty strings.")
        if tester_id is None:
            tester_id = record_tester_id
        elif tester_id != record_tester_id:
            raise ExportError(f"{session_path}:{line_number}: inconsistent tester_id.")
        if session_id is None:
            session_id = record_session_id
        elif session_id != record_session_id:
            raise ExportError(f"{session_path}:{line_number}: inconsistent session_id.")
        if interaction_id in interaction_ids:
            raise ExportError(f"{session_path}:{line_number}: duplicate interaction_id.")
        rating = record.get("rating")
        if rating not in (None, "helpful", "needs_review"):
            raise ExportError(f"{session_path}:{line_number}: unsupported rating value.")
        interaction_ids.add(interaction_id)
        records.append(record)

    if not records or tester_id is None or session_id is None:
        raise ExportError(f"{session_path}: session contains no interactions.")
    if tester_id != session_path.parent.name:
        raise ExportError(f"{session_path}: tester_id does not match tester directory.")
    return SessionData(session_path, tester_id, session_id, records)


def _inline_code(value: Any) -> str:
    text = _not_recorded(value)
    fence = "`" * max(1, max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}{text}{fence}"


def format_sources(sources: Any) -> list[str]:
    """Create compact reviewer-facing source lines without dumping JSON objects."""
    if not isinstance(sources, list) or not sources:
        return ["- Not recorded"]
    lines: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        repository = _not_recorded(source.get("repository"))
        source_path = _inline_code(source.get("source_path"))
        symbol = source.get("symbol_or_section_title") or source.get("symbol") or source.get("section_title")
        start_line = source.get("start_line")
        end_line = source.get("end_line")
        parts = [repository, source_path]
        if symbol:
            parts.append(_inline_code(symbol))
        if start_line is not None or end_line is not None:
            parts.append(f"lines {_not_recorded(start_line)}–{_not_recorded(end_line)}")
        lines.append("- " + " — ".join(parts))
    return lines or ["- Not recorded"]


def _answer_markdown(raw_answer: Any) -> str:
    """Preserve valid Markdown; protect only answers with an unmatched code fence."""
    text = _not_recorded(raw_answer)
    fences = re.findall(r"(?m)^\s*(`{3,})", text)
    if len(fences) % 2 == 0:
        return text
    outer_fence = "`" * max(4, max(len(fence) for fence in fences) + 1)
    return f"{outer_fence}text\n{text}\n{outer_fence}"


def _rating_bucket(record: dict[str, Any]) -> str:
    rating = record.get("rating")
    if rating == "helpful":
        return "helpful"
    if rating == "needs_review":
        return "needs_review"
    return "unrated"


def _category_key(record: dict[str, Any]) -> str:
    category = record.get("feedback_category")
    return category if isinstance(category, str) and category else "__uncategorized__"


def _category_heading(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def _metadata_lines(session: SessionData) -> list[str]:
    first = session.records[0]
    return [
        "## Session Metadata",
        "",
        f"- **Tester:** {_not_recorded(first.get('tester_id'))}",
        f"- **Session:** {_not_recorded(first.get('session_id'))}",
        f"- **Number of interactions:** {len(session.records)}",
        f"- **Model:** {_not_recorded(first.get('model'))}",
        f"- **Top-K:** {_not_recorded(first.get('top_k'))}",
        f"- **Prompt version:** {_not_recorded(first.get('prompt_version'))}",
        f"- **Retrieval version:** {_not_recorded(first.get('retrieval_version'))}",
        f"- **Chunking version:** {_not_recorded(first.get('chunking_version'))}",
        f"- **SPHinXsys commit:** {_not_recorded(first.get('sphinxsys_commit'))}",
        f"- **SPHinXsim commit:** {_not_recorded(first.get('sphinxsim_commit'))}",
    ]


def _interaction_lines(sequence: int, record: dict[str, Any], *, needs_review: bool) -> list[str]:
    heading = "####" if needs_review else "###"
    lines = [
        f"{heading} Interaction {sequence}",
        "",
        "**Interaction ID**",
        "",
        _inline_code(record.get("interaction_id")),
        "",
        "**Question**",
        "",
        _not_recorded(record.get("query")),
        "",
        "**Answer**",
        "",
        _answer_markdown(record.get("raw_answer")),
    ]
    if needs_review:
        lines.extend(
            [
                "",
                "**Feedback category**",
                "",
                _not_recorded(record.get("feedback_category")),
                "",
                "**Feedback comment**",
                "",
                _not_recorded(record.get("feedback_comment")),
                "",
                "**Suggested correction**",
                "",
                _not_recorded(record.get("suggested_correction")),
            ]
        )
    lines.extend(
        [
            "",
            "**Sources**",
            "",
            *format_sources(record.get("sources")),
            "",
            "**Review status**",
            "",
            _not_recorded(record.get("review_status")),
        ]
    )
    return lines


def render_session_markdown(session: SessionData) -> str:
    """Render a deterministic, derived report for one already-validated session."""
    buckets: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for sequence, record in enumerate(session.records, start=1):
        buckets[_rating_bucket(record)].append((sequence, record))
    lines = [
        "# User Testing Session",
        "",
        "> Tester feedback and suggested corrections are unreviewed evaluation input and must not be treated as approved repository knowledge.",
        "",
        *_metadata_lines(session),
        "",
        "## Summary",
        "",
        "| Rating | Count |",
        "|---|---:|",
        f"| Helpful | {len(buckets['helpful'])} |",
        f"| Needs review | {len(buckets['needs_review'])} |",
        f"| Unrated | {len(buckets['unrated'])} |",
    ]
    if buckets["needs_review"]:
        category_counts = Counter(_category_key(record) for _, record in buckets["needs_review"])
        lines.extend(["", "### Needs-review categories", "", "| Category | Count |", "|---|---:"])
        for category in sorted(category_counts, key=lambda value: (CATEGORY_ORDER.index(value) if value in CATEGORY_ORDER else len(CATEGORY_ORDER), value)):
            lines.append(f"| {_category_heading(category)} | {category_counts[category]} |")

    if buckets["helpful"]:
        lines.extend(["", "## Helpful"])
        for sequence, record in buckets["helpful"]:
            lines.extend(["", *_interaction_lines(sequence, record, needs_review=False), "", "---"])
    if buckets["needs_review"]:
        lines.extend(["", "## Needs Review"])
        grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for item in buckets["needs_review"]:
            grouped[_category_key(item[1])].append(item)
        ordered_categories = sorted(grouped, key=lambda value: (CATEGORY_ORDER.index(value) if value in CATEGORY_ORDER else len(CATEGORY_ORDER), value))
        for category in ordered_categories:
            lines.extend(["", f"### {_category_heading(category)}"])
            for sequence, record in grouped[category]:
                lines.extend(["", *_interaction_lines(sequence, record, needs_review=True), "", "---"])
    if buckets["unrated"]:
        lines.extend(["", "## Unrated"])
        for sequence, record in buckets["unrated"]:
            lines.extend(["", *_interaction_lines(sequence, record, needs_review=False), "", "---"])
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def default_output_path(session: SessionData, user_testing_root: Path = USER_TESTING_ROOT) -> Path:
    return user_testing_root / EXPORTS_DIRNAME / session.tester_id / f"{session.session_id}.md"


def export_session(
    path_value: str | Path,
    *,
    output_path: str | Path | None = None,
    user_testing_root: Path = USER_TESTING_ROOT,
) -> Path:
    session = load_session(path_value, user_testing_root=user_testing_root)
    output = Path(output_path).resolve() if output_path is not None else default_output_path(session, user_testing_root)
    if output == session.source_path:
        raise ExportError("Markdown output path must not replace the source JSONL session.")
    _atomic_write(output, render_session_markdown(session))
    return output


def discover_sessions_for_input_dir(path_value: str | Path, *, user_testing_root: Path = USER_TESTING_ROOT) -> list[Path]:
    directory = _resolve_under_user_testing(path_value, user_testing_root)
    try:
        relative = directory.relative_to(user_testing_root.resolve())
    except ValueError as error:
        raise ExportError("Input directory must be under data/user_testing/.") from error
    if not directory.is_dir() or len(relative.parts) != 1 or relative.parts[0] in EXCLUDED_BATCH_DIRECTORIES:
        raise ExportError("Input directory must be one tester directory under data/user_testing/.")
    return sorted(path for path in directory.glob("session_*.jsonl") if path.is_file() and not path.is_symlink())


def discover_all_sessions(*, user_testing_root: Path = USER_TESTING_ROOT) -> tuple[list[Path], list[str]]:
    sessions: list[Path] = []
    excluded: list[str] = []
    if not user_testing_root.is_dir():
        raise ExportError("data/user_testing/ does not exist.")
    for tester_directory in sorted(path for path in user_testing_root.iterdir() if path.is_dir()):
        tester_id = tester_directory.name
        if tester_id in EXCLUDED_BATCH_DIRECTORIES or tester_id.startswith("."):
            continue
        if tester_id in DEVELOPMENT_ONLY_TESTERS:
            excluded.append(tester_id)
            continue
        sessions.extend(sorted(path for path in tester_directory.glob("session_*.jsonl") if path.is_file() and not path.is_symlink()))
    return sessions, excluded


def export_batch(paths: Iterable[Path], *, user_testing_root: Path = USER_TESTING_ROOT) -> list[Path]:
    sessions = [load_session(path, user_testing_root=user_testing_root) for path in paths]
    outputs: list[Path] = []
    for session in sessions:
        output = default_output_path(session, user_testing_root)
        _atomic_write(output, render_session_markdown(session))
        outputs.append(output)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--session", help="One session JSONL under data/user_testing/<tester_id>/.")
    modes.add_argument("--input-dir", help="One tester directory under data/user_testing/.")
    modes.add_argument("--all-sessions", action="store_true", help="Export sessions from all non-development tester directories.")
    parser.add_argument("--output", help="Optional Markdown output path; valid only with --session.")
    args = parser.parse_args()
    if args.output and not args.session:
        parser.error("--output is valid only with --session.")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.session:
            output = export_session(args.session, output_path=args.output)
            print(f"Markdown report generated: {output}")
            return 0
        if args.input_dir:
            paths = discover_sessions_for_input_dir(args.input_dir)
            outputs = export_batch(paths)
            print(f"Sessions scanned: {len(paths)}")
            print(f"Markdown reports generated: {len(outputs)}")
            print(f"Output root: {USER_TESTING_ROOT / EXPORTS_DIRNAME}")
            return 0
        paths, excluded = discover_all_sessions()
        outputs = export_batch(paths)
        print(f"Sessions scanned: {len(paths)}")
        print(f"Markdown reports generated: {len(outputs)}")
        print(f"Output root: {USER_TESTING_ROOT / EXPORTS_DIRNAME}")
        if excluded:
            print(f"Development-only testers excluded: {', '.join(excluded)}")
        return 0
    except ExportError as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
