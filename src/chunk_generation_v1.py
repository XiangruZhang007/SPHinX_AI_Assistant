"""Minimal SPHinXsys/SPHinXsim retrieval-chunk generators for Prototype V1.

This module only reads the standalone upstream repository.  It deliberately
does not perform embedding, retrieval, indexing, or any write within an
upstream repository.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha1
import json
from pathlib import Path
import re
from statistics import fmean, median
from typing import Any, Iterable

from chunker import chunk_file
from tree_sitter_language_pack import get_parser


SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
PRIMARY_NODE_TYPES = {"function_definition", "method_declaration"}
PYTHON_PRIMARY_NODE_TYPES = {"function_definition", "class_definition"}
SIM_SCOPE_ROOTS = (
    "sphinxsim/bindings",
    "sphinxsim/config",
    "sphinxsim/llm",
    "sphinxsim/sph_simulation",
    "sphinxsim/visualization",
    "examples",
    "tests",
)
SIM_SOURCE_LANGUAGES = {
    ".py": "python",
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}
OVERSIZED_BYTES = 5_000
SEPARATOR_RE = re.compile(r"^\s*//-{10,}\s*$")
COMMENT_RE = re.compile(r"^\s*//\s?(.*)$")


def _line_starts(source: bytes) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, value in enumerate(source) if value == ord("\n"))
    return starts


def _line_number(starts: list[int], byte_offset: int) -> int:
    """Return the one-based line containing a byte offset."""
    from bisect import bisect_right

    return bisect_right(starts, byte_offset)


def _end_line(source: bytes, starts: list[int], exclusive_end: int) -> int:
    """Return the final included one-based line for an exclusive byte end."""
    line = _line_number(starts, exclusive_end)
    return line - 1 if exclusive_end > 0 and source[exclusive_end - 1 : exclusive_end] == b"\n" else line


def _relative_path(project_root: Path, path: Path) -> str:
    return path.relative_to(project_root).as_posix()


def _source_role(repo_root: Path, source_path: Path) -> str:
    relative_parts = source_path.relative_to(repo_root).parts
    if relative_parts[0] == "src":
        return "core_library"
    test_root = relative_parts[:2]
    if test_root == ("tests", "unit_tests_src"):
        return "unit_test"
    if test_root in {("tests", "2d_examples"), ("tests", "3d_examples")}:
        return "simulation_case"
    if test_root == ("tests", "optimization"):
        return "optimization_case"
    if test_root == ("tests", "tests_sycl"):
        return "sycl_case"
    if test_root == ("tests", "extra_source_and_tests"):
        return "extra_source_or_test"
    return "simulation_case"


def _context(raw: dict[str, Any]) -> dict[str, Any]:
    route = raw.get("qualified_route") or raw.get("parent_route") or []
    return {
        "parent_context": raw.get("parent_context", ""),
        "parent_route": raw.get("parent_route", []),
        "qualified_route": route,
    }


def _symbol(raw: dict[str, Any]) -> str | None:
    signature = (raw.get("metadata") or {}).get("signature") or {}
    signature_name = signature.get("name")
    if signature_name and not signature_name.startswith("anon@"):
        return signature_name
    route = raw.get("qualified_route") or []
    if route:
        last = route[-1]
        route_name = last.split(":", 1)[-1]
        if not route_name.startswith("anon@"):
            return route_name
    return None


def _record_from_raw(
    raw: dict[str, Any], project_root: Path, source_role: str, repository: str = "SPHinXsys"
) -> dict[str, Any]:
    return {
        "chunk_id": raw["chunk_id"],
        "repository": repository,
        "source_role": source_role,
        "source_path": _relative_path(project_root, Path(raw["file_path"])),
        "chunk_type": raw["node_type"],
        "node_type": raw["node_type"],
        "symbol": _symbol(raw),
        "start_line": raw["start_line"],
        "end_line": raw["end_line"],
        "byte_start": raw["byte_start"],
        "byte_end": raw["byte_end"],
        "parent_context": _context(raw),
        "content": raw["content"],
        "structural_metadata": {
            "parent_chunk_id": raw.get("parent_chunk_id"),
            "symbol_id": raw.get("symbol_id"),
            "definition_id": raw.get("definition_id"),
            "metadata": raw.get("metadata", {}),
        },
    }


def _detect_section_headers(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Find complete separator-comment title blocks in a function body."""
    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line.encode("utf-8"))

    headers: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not SEPARATOR_RE.match(lines[index]):
            index += 1
            continue
        closing = index + 1
        while closing < len(lines) and not SEPARATOR_RE.match(lines[closing]):
            closing += 1
        if closing >= len(lines):
            issues.append({"relative_line": index + 1, "issue": "missing closing separator"})
            index += 1
            continue
        title_parts: list[str] = []
        valid = True
        for title_index in range(index + 1, closing):
            match = COMMENT_RE.match(lines[title_index])
            if not match:
                valid = False
                break
            title = match.group(1).strip()
            if title:
                title_parts.append(title)
        if not valid or not title_parts:
            issues.append({"relative_line": index + 1, "issue": "separator block has no usable comment title"})
        else:
            headers.append(
                {
                    "title": " ".join(title_parts),
                    "local_byte_start": offsets[index],
                    "relative_line": index + 1,
                }
            )
        index = closing + 1
    return headers, issues


def _function_body_nodes(content: bytes) -> tuple[list[Any], bool]:
    """Return top-level named nodes inside the first function definition body."""
    tree = get_parser("cpp").parse(content)
    function = next(
        (node for node in tree.root_node.named_children if node.type == "function_definition"),
        None,
    )
    if function is None:
        return [], tree.root_node.has_error
    body = next((node for node in function.named_children if node.type == "compound_statement"), None)
    return (list(body.named_children) if body is not None else []), tree.root_node.has_error


def _safe_headers(headers: list[dict[str, Any]], nodes: list[Any]) -> list[dict[str, Any]]:
    """Use only headers that do not split a top-level statement/block."""
    safe: list[dict[str, Any]] = []
    for header in headers:
        point = header["local_byte_start"]
        if not any(node.start_byte < point < node.end_byte for node in nodes if node.type != "comment"):
            safe.append(header)
    return safe


def _semantic_section_records(
    raw: dict[str, Any], project_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create E1 sections from one simulation function, or no records if no E1 headers exist."""
    content = raw["content"]
    content_bytes = content.encode("utf-8")
    headers, issues = _detect_section_headers(content)
    nodes, parse_error = _function_body_nodes(content_bytes)
    if parse_error:
        issues.append({"relative_line": 1, "issue": "Tree-sitter reported a parse error"})
    headers = _safe_headers(headers, nodes)
    if not headers:
        return [], issues

    starts = [0, *(header["local_byte_start"] for header in headers)]
    titles = ["Function preamble (before first semantic section)", *(header["title"] for header in headers)]
    line_starts = _line_starts(content_bytes)
    records: list[dict[str, Any]] = []
    for section_index, (start, title) in enumerate(zip(starts, titles)):
        end = starts[section_index + 1] if section_index + 1 < len(starts) else len(content_bytes)
        for subchunk_index, (chunk_start, chunk_end) in enumerate(
            _subdivide_semantic_section(start, end, nodes)
        ):
            identifier_input = f"{raw['chunk_id']}:e1:{section_index}:{subchunk_index}:{chunk_start}:{chunk_end}"
            node_types = sorted(
                {
                    node.type
                    for node in nodes
                    if node.start_byte < chunk_end and chunk_start < node.end_byte
                }
            )
            records.append(
                {
                    "chunk_id": sha1(identifier_input.encode("utf-8")).hexdigest(),
                    "repository": "SPHinXsys",
                    "source_role": "simulation_case",
                    "source_path": _relative_path(project_root, Path(raw["file_path"])),
                    "chunk_type": (
                        "semantic_section"
                        if chunk_start == start and chunk_end == end
                        else "semantic_section_tree_sitter_subchunk"
                    ),
                    "node_type": "semantic_section",
                    "symbol": title,
                    "section_title": title,
                    "start_line": raw["start_line"] + _line_number(line_starts, chunk_start) - 1,
                    "end_line": raw["start_line"] + _end_line(content_bytes, line_starts, chunk_end) - 1,
                    "byte_start": raw["byte_start"] + chunk_start,
                    "byte_end": raw["byte_start"] + chunk_end,
                    "parent_context": {
                        **_context(raw),
                        "original_function_chunk_id": raw["chunk_id"],
                        "original_function_symbol": _symbol(raw),
                        "original_function_start_line": raw["start_line"],
                        "original_function_end_line": raw["end_line"],
                        "original_function_byte_start": raw["byte_start"],
                        "original_function_byte_end": raw["byte_end"],
                        "semantic_section_index": section_index,
                        "subchunk_index_within_section": subchunk_index,
                        "tree_sitter_node_types_contained": node_types,
                    },
                    "content": content_bytes[chunk_start:chunk_end].decode("utf-8"),
                }
            )
    return records, issues


def _subdivide_semantic_section(start: int, end: int, nodes: list[Any]) -> list[tuple[int, int]]:
    """For an oversized section, split only before top-level statements/blocks."""
    if end - start <= OVERSIZED_BYTES:
        return [(start, end)]
    boundaries = sorted(
        {
            start,
            end,
            *(
                node.start_byte
                for node in nodes
                if start < node.start_byte < end and node.type != "comment"
            ),
        }
    )
    output: list[tuple[int, int]] = []
    current = start
    for position_index in range(1, len(boundaries)):
        boundary = boundaries[position_index]
        if boundary - current > OVERSIZED_BYTES:
            prior = boundaries[position_index - 1]
            if prior > current:
                output.append((current, prior))
                current = prior
    if current < end:
        output.append((current, end))
    return output or [(start, end)]


def _source_files(repo_root: Path) -> Iterable[Path]:
    for directory in (repo_root / "src", repo_root / "tests"):
        yield from sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS)


def generate(project_root: Path, output_jsonl: Path, output_summary: Path) -> dict[str, Any]:
    """Generate Prototype V1 chunks from the standalone SPHinXsys source tree."""
    repo_root = project_root / "repos" / "SPHinXsys"
    parser = get_parser("cpp")
    records: list[dict[str, Any]] = []
    skipped_files: list[dict[str, str]] = []
    parse_errors: list[str] = []
    semantic_issues: list[dict[str, Any]] = []

    for source_path in _source_files(repo_root):
        source_role = _source_role(repo_root, source_path)
        relative = _relative_path(project_root, source_path)
        try:
            source_bytes = source_path.read_bytes()
            if parser.parse(source_bytes).root_node.has_error:
                parse_errors.append(relative)
            raw_chunks = [asdict(chunk) for chunk in chunk_file(str(source_path), language="cpp")]
        except Exception as error:  # Keep the demo run complete if one source is unsupported.
            skipped_files.append({"source_path": relative, "reason": f"{type(error).__name__}: {error}"})
            continue

        primary = [raw for raw in raw_chunks if raw["node_type"] in PRIMARY_NODE_TYPES]
        if source_role != "simulation_case":
            records.extend(_record_from_raw(raw, project_root, source_role) for raw in primary)
            continue

        # E1 is primary for commented simulation functions. Functions without valid
        # separator-comment sections remain complete function-level retrieval units.
        for raw in primary:
            sections, issues = _semantic_section_records(raw, project_root)
            if issues:
                semantic_issues.append({"source_path": relative, "function_chunk_id": raw["chunk_id"], "issues": issues})
            if sections:
                records.extend(sections)
            else:
                records.append(_record_from_raw(raw, project_root, source_role))

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    byte_sizes = [record["byte_end"] - record["byte_start"] for record in records]
    summary = {
        "prototype": "source_chunks_v1",
        "repository_scope": ["SPHinXsys"],
        "input_root": _relative_path(project_root, repo_root),
        "total_chunk_count": len(records),
        "counts_by_source_role": dict(sorted(Counter(record["source_role"] for record in records).items())),
        "counts_by_repository": dict(sorted(Counter(record["repository"] for record in records).items())),
        "counts_by_chunk_type": dict(sorted(Counter(record["chunk_type"] for record in records).items())),
        "chunk_size_bytes": {
            "min": min(byte_sizes) if byte_sizes else 0,
            "max": max(byte_sizes) if byte_sizes else 0,
            "mean": fmean(byte_sizes) if byte_sizes else 0,
            "median": median(byte_sizes) if byte_sizes else 0,
        },
        "oversized_chunks_remaining_gt_5000_bytes": sum(size > OVERSIZED_BYTES for size in byte_sizes),
        "skipped_files": skipped_files,
        "parse_errors": parse_errors,
        "semantic_section_warnings": semantic_issues,
    }
    output_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _sphinxsim_source_role(repo_root: Path, source_path: Path) -> str:
    """Return the small, demo-only taxonomy selected for SPHinXsim-specific files."""
    relative = source_path.relative_to(repo_root)
    if source_path.suffix.lower() == ".json":
        return "config_or_schema"
    text = relative.as_posix()
    if text.startswith("sphinxsim/bindings/"):
        return "binding"
    if text.startswith("sphinxsim/config/"):
        return "config_or_schema"
    if text.startswith("sphinxsim/llm/"):
        return "llm_support"
    if text.startswith("sphinxsim/sph_simulation/"):
        return "simulator_library"
    if text.startswith("sphinxsim/visualization/"):
        return "visualization"
    if text.startswith("examples/"):
        return "simulation_example"
    return "test"


def _sphinxsim_files(repo_root: Path) -> Iterable[Path]:
    embedded_root = repo_root / "sphinxsim" / "sphinxsys"
    for scope_root in SIM_SCOPE_ROOTS:
        root = repo_root / scope_root
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            # Keep this explicit provenance guard even though the declared roots
            # currently do not recurse into the embedded SPHinXsys subtree.
            if path.is_relative_to(embedded_root):
                continue
            if path.suffix.lower() in SIM_SOURCE_LANGUAGES or path.suffix.lower() == ".json":
                yield path


def _config_document_record(project_root: Path, source_path: Path, content: str) -> tuple[dict[str, Any], str | None]:
    source_bytes = content.encode("utf-8")
    relative = _relative_path(project_root, source_path)
    warning = None
    try:
        json.loads(content)
    except json.JSONDecodeError as error:
        warning = f"JSONDecodeError: {error.msg} at line {error.lineno}, column {error.colno}"
    return (
        {
            "chunk_id": sha1(f"SPHinXsim:config_document:{relative}:{source_bytes!r}".encode("utf-8")).hexdigest(),
            "repository": "SPHinXsim",
            "source_role": "config_or_schema",
            "source_path": relative,
            "chunk_type": "config_document",
            "node_type": "json_document",
            "symbol": source_path.stem,
            "start_line": 1,
            "end_line": len(content.splitlines()) or 1,
            "byte_start": 0,
            "byte_end": len(source_bytes),
            "parent_context": {"configuration_role": "atomic_json_document"},
            "content": content,
            "structural_metadata": {"json_parse_success": warning is None},
        },
        warning,
    )


def generate_sphinxsim(project_root: Path, output_jsonl: Path, output_summary: Path) -> dict[str, Any]:
    """Generate SPHinXsim-specific V1 chunks while excluding its embedded subtree."""
    repo_root = project_root / "repos" / "SPHinXsim"
    parsers = {"python": get_parser("python"), "cpp": get_parser("cpp")}
    records: list[dict[str, Any]] = []
    skipped_files: list[dict[str, str]] = []
    parse_warnings: list[dict[str, str]] = []

    for source_path in _sphinxsim_files(repo_root):
        relative = _relative_path(project_root, source_path)
        suffix = source_path.suffix.lower()
        try:
            content = source_path.read_text(encoding="utf-8")
            if suffix == ".json":
                record, warning = _config_document_record(project_root, source_path, content)
                records.append(record)
                if warning:
                    parse_warnings.append({"source_path": relative, "warning": warning})
                continue

            language = SIM_SOURCE_LANGUAGES[suffix]
            if parsers[language].parse(content.encode("utf-8")).root_node.has_error:
                parse_warnings.append({"source_path": relative, "warning": "Tree-sitter reported a parse error"})
            raw_chunks = [asdict(chunk) for chunk in chunk_file(str(source_path), language=language)]
        except Exception as error:
            skipped_files.append({"source_path": relative, "reason": f"{type(error).__name__}: {error}"})
            continue

        node_types = PYTHON_PRIMARY_NODE_TYPES if language == "python" else PRIMARY_NODE_TYPES
        role = _sphinxsim_source_role(repo_root, source_path)
        records.extend(
            _record_from_raw(raw, project_root, role, repository="SPHinXsim")
            for raw in raw_chunks
            if raw["node_type"] in node_types
        )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    sizes = [record["byte_end"] - record["byte_start"] for record in records]
    summary = {
        "prototype": "sphinxsim_source_chunks_v1",
        "repository_scope": ["SPHinXsim"],
        "input_root": _relative_path(project_root, repo_root),
        "excluded_paths": ["repos/SPHinXsim/sphinxsim/sphinxsys/"],
        "total_chunk_count": len(records),
        "counts_by_source_role": dict(sorted(Counter(record["source_role"] for record in records).items())),
        "counts_by_repository": {"SPHinXsim": len(records)},
        "counts_by_chunk_type": dict(sorted(Counter(record["chunk_type"] for record in records).items())),
        "chunk_size_bytes": {
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
            "mean": fmean(sizes) if sizes else 0,
            "median": median(sizes) if sizes else 0,
        },
        "oversized_chunks_remaining_gt_5000_bytes": sum(size > OVERSIZED_BYTES for size in sizes),
        "skipped_files": skipped_files,
        "parse_warning_files": parse_warnings,
    }
    output_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def generate_combined_corpus(
    sys_jsonl: Path,
    sys_summary_path: Path,
    sim_jsonl: Path,
    sim_summary_path: Path,
    output_jsonl: Path,
    output_summary: Path,
) -> dict[str, Any]:
    """Concatenate compatible V1 records and validate their demo corpus invariants."""
    records = [*_read_jsonl(sys_jsonl), *_read_jsonl(sim_jsonl)]
    required = {"chunk_id", "repository", "source_role", "source_path", "content"}
    missing = [index for index, record in enumerate(records) if not required <= set(record)]
    identifiers = [record["chunk_id"] for record in records]
    unique = len(identifiers) == len(set(identifiers))
    if missing or not unique:
        raise ValueError(f"Combined corpus validation failed: missing={missing[:10]}, unique_chunk_ids={unique}")

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    sys_summary = json.loads(sys_summary_path.read_text(encoding="utf-8"))
    sim_summary = json.loads(sim_summary_path.read_text(encoding="utf-8"))
    sizes = [record["byte_end"] - record["byte_start"] for record in records]
    parse_warnings = [
        {"repository": "SPHinXsys", "source_path": path, "warning": "Tree-sitter reported a parse error"}
        for path in sys_summary.get("parse_errors", [])
    ] + [
        {"repository": "SPHinXsim", **warning}
        for warning in sim_summary.get("parse_warning_files", [])
    ]
    skipped_files = [
        {"repository": "SPHinXsys", **item}
        for item in sys_summary.get("skipped_files", [])
    ] + [
        {"repository": "SPHinXsim", **item}
        for item in sim_summary.get("skipped_files", [])
    ]
    summary = {
        "prototype": "source_chunks_v1_combined",
        "total_chunk_count": len(records),
        "counts_by_repository": dict(sorted(Counter(record["repository"] for record in records).items())),
        "counts_by_source_role": dict(sorted(Counter(record["source_role"] for record in records).items())),
        "counts_by_chunk_type": dict(sorted(Counter(record["chunk_type"] for record in records).items())),
        "chunk_size_bytes": {
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
            "mean": fmean(sizes) if sizes else 0,
            "median": median(sizes) if sizes else 0,
        },
        "oversized_chunks_remaining_gt_5000_bytes": sum(size > OVERSIZED_BYTES for size in sizes),
        "skipped_files": skipped_files,
        "parse_warning_files": parse_warnings,
        "validation": {
            "every_line_parses_as_json": True,
            "all_records_have_required_fields": True,
            "chunk_id_values_unique": True,
        },
    }
    output_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
