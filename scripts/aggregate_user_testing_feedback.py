#!/usr/bin/env python3
"""Create deterministic reviewer batches from user-testing session JSONL files."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_TESTING_ROOT = PROJECT_ROOT / "data" / "user_testing"
REVIEW_BATCHES_DIRNAME = "review_batches"
REQUIRED_FIELDS = (
    "interaction_id",
    "tester_id",
    "query",
    "raw_answer",
    "review_status",
)
OUTPUT_FIELDS = (
    "interaction_id",
    "session_id",
    "tester_id",
    "timestamp",
    "query",
    "raw_answer",
    "sources",
    "retrieved_evidence_metadata",
    "model",
    "base_url",
    "top_k",
    "prompt_version",
    "retrieval_version",
    "chunking_version",
    "rating",
    "feedback_category",
    "feedback_comment",
    "suggested_correction",
    "review_status",
    "sphinxsys_commit",
    "sphinxsim_commit",
)
TIMESTAMP_SUFFIX_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")


class AggregationError(ValueError):
    """A controlled aggregation failure that must not create a review batch."""


def _session_files(user_testing_root: Path) -> list[Path]:
    review_batches = user_testing_root / REVIEW_BATCHES_DIRNAME
    return sorted(
        path
        for path in user_testing_root.rglob("session_*.jsonl")
        if path.is_file() and review_batches not in path.parents
    )


def _validate_record(record: Any, session_file: Path, line_number: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise AggregationError(f"{session_file}:{line_number}: JSONL value must be an object.")
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise AggregationError(f"{session_file}:{line_number}: missing required field(s): {', '.join(missing)}.")
    for field in ("interaction_id", "tester_id"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise AggregationError(f"{session_file}:{line_number}: {field} must be a non-empty string.")
    return record


def load_interactions(user_testing_root: Path) -> tuple[list[tuple[dict[str, Any], Path]], int]:
    """Read every session completely before any review-batch output is created."""
    records: list[tuple[dict[str, Any], Path]] = []
    interaction_sources: dict[str, Path] = {}
    session_files = _session_files(user_testing_root)
    for session_file in session_files:
        try:
            lines = session_file.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise AggregationError(f"Could not read session file: {session_file}.") from error
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as error:
                raise AggregationError(f"{session_file}:{line_number}: invalid JSON.") from error
            record = _validate_record(decoded, session_file, line_number)
            interaction_id = record["interaction_id"]
            if interaction_id in interaction_sources:
                raise AggregationError(
                    f"Duplicate interaction_id {interaction_id!r} in "
                    f"{interaction_sources[interaction_id]} and {session_file}."
                )
            interaction_sources[interaction_id] = session_file
            records.append((record, session_file))
    return records, len(session_files)


def _requires_review(record: dict[str, Any], only_unreviewed: bool) -> bool:
    correction = record.get("suggested_correction")
    selected = record.get("rating") == "needs_review" or (
        isinstance(correction, str) and bool(correction.strip())
    )
    return selected and (not only_unreviewed or record.get("review_status") == "unreviewed")


def select_records(
    records: list[tuple[dict[str, Any], Path]], only_unreviewed: bool
) -> list[tuple[dict[str, Any], Path]]:
    selected = [(record, path) for record, path in records if _requires_review(record, only_unreviewed)]
    return sorted(selected, key=lambda item: (str(item[0].get("timestamp", "")), item[0]["tester_id"], item[0]["interaction_id"]))


def _source_session_path(session_file: Path, user_testing_root: Path) -> str:
    try:
        return str(session_file.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(session_file.relative_to(user_testing_root))


def _output_record(record: dict[str, Any], session_file: Path, user_testing_root: Path) -> dict[str, Any]:
    output = {field: record.get(field) for field in OUTPUT_FIELDS}
    output["source_session_file"] = _source_session_path(session_file, user_testing_root)
    return output


def _fenced_text(text: Any) -> str:
    value = "" if text is None else str(text)
    longest_backtick_run = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest_backtick_run + 1)
    return f"{fence}text\n{value}\n{fence}"


def _source_summary(sources: Any) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return ["- No compact source metadata available."]
    lines: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        label = source.get("symbol_or_section_title") or source.get("symbol") or source.get("section_title") or "(unnamed)"
        line_start = source.get("start_line")
        line_end = source.get("end_line")
        line_range = f"{line_start}-{line_end}" if line_start is not None or line_end is not None else "(line range unavailable)"
        lines.append(
            "- "
            f"{source.get('repository') or '(repository unavailable)'} | "
            f"{source.get('source_role') or '(role unavailable)'} | "
            f"`{source.get('source_path') or '(path unavailable)'}` | {label} | lines {line_range}"
        )
    return lines or ["- No compact source metadata available."]


def render_markdown(
    *,
    selected: list[tuple[dict[str, Any], Path]],
    session_files_scanned: int,
    interactions_scanned: int,
    generated_timestamp: str,
    only_unreviewed: bool,
    user_testing_root: Path,
) -> str:
    category_counts = Counter(
        str(record.get("feedback_category") or "(none)") for record, _ in selected
    )
    tester_counts = Counter(record["tester_id"] for record, _ in selected)
    lines = [
        "# User-Testing Review Batch",
        "",
        f"- **Generated (UTC):** {generated_timestamp}",
        f"- **Session files scanned:** {session_files_scanned}",
        f"- **Interactions scanned:** {interactions_scanned}",
        f"- **Selected for review:** {len(selected)}",
        f"- **Only unreviewed:** {'yes' if only_unreviewed else 'no'}",
        "",
        "## Counts by feedback category",
        "",
    ]
    lines.extend([f"- `{category}`: {count}" for category, count in sorted(category_counts.items())] or ["- None"])
    lines.extend(["", "## Counts by tester", ""])
    lines.extend([f"- `{tester}`: {count}" for tester, count in sorted(tester_counts.items())] or ["- None"])

    for index, (record, session_file) in enumerate(selected, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {record['interaction_id']}",
                "",
                f"- **Tester:** {record['tester_id']}",
                f"- **Timestamp:** {record.get('timestamp')}",
                f"- **Rating:** {record.get('rating')}",
                f"- **Feedback category:** {record.get('feedback_category')}",
                f"- **Feedback comment:** {record.get('feedback_comment')}",
                f"- **Suggested correction:** {record.get('suggested_correction')}",
                f"- **Review status:** {record.get('review_status')}",
                f"- **Model / Top-K:** {record.get('model')} / {record.get('top_k')}",
                f"- **SPHinXsys commit:** {record.get('sphinxsys_commit')}",
                f"- **SPHinXsim commit:** {record.get('sphinxsim_commit')}",
                f"- **Source session file:** `{_source_session_path(session_file, user_testing_root)}`",
                "",
                "### Query",
                _fenced_text(record.get("query")),
                "",
                "### Compact sources",
                *_source_summary(record.get("sources")),
                "",
                "### Raw answer",
                _fenced_text(record.get("raw_answer")),
            ]
        )
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def aggregate(
    *,
    user_testing_root: Path = USER_TESTING_ROOT,
    only_unreviewed: bool = False,
    timestamp_suffix: str | None = None,
) -> tuple[Path, Path, int]:
    records, session_files_scanned = load_interactions(user_testing_root)
    selected = select_records(records, only_unreviewed)
    generated = datetime.now(timezone.utc)
    suffix = timestamp_suffix or generated.strftime("%Y%m%dT%H%M%SZ")
    if not TIMESTAMP_SUFFIX_PATTERN.fullmatch(suffix):
        raise AggregationError("Timestamp suffix must use UTC format YYYYMMDDTHHMMSSZ.")

    review_batches = user_testing_root / REVIEW_BATCHES_DIRNAME
    jsonl_path = review_batches / f"review_batch_{suffix}.jsonl"
    markdown_path = review_batches / f"review_batch_{suffix}.md"
    if jsonl_path.exists() or markdown_path.exists():
        raise AggregationError(f"Review batch already exists for timestamp suffix {suffix}.")

    output_records = [_output_record(record, session_file, user_testing_root) for record, session_file in selected]
    jsonl_content = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in output_records)
    markdown_content = render_markdown(
        selected=selected,
        session_files_scanned=session_files_scanned,
        interactions_scanned=len(records),
        generated_timestamp=generated.isoformat(timespec="seconds").replace("+00:00", "Z"),
        only_unreviewed=only_unreviewed,
        user_testing_root=user_testing_root,
    )
    review_batches.mkdir(parents=True, exist_ok=True)
    _atomic_write(jsonl_path, jsonl_content)
    _atomic_write(markdown_path, markdown_content)
    return jsonl_path, markdown_path, len(selected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-unreviewed", action="store_true", help="Select only records with review_status=unreviewed.")
    parser.add_argument("--timestamp-suffix", help="Optional UTC suffix for controlled runs: YYYYMMDDTHHMMSSZ.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        jsonl_path, markdown_path, selected_count = aggregate(
            only_unreviewed=args.only_unreviewed,
            timestamp_suffix=args.timestamp_suffix,
        )
    except AggregationError as error:
        print(f"Aggregation failed: {error}", file=sys.stderr)
        return 1
    print(f"Selected for review: {selected_count}")
    print(f"JSONL batch: {jsonl_path}")
    print(f"Markdown batch: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
