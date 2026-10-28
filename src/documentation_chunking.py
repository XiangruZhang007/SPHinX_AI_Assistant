"""Lossless, bounded-syntax Markdown/RST scanning, not a rendering parser.

No directives, references, includes or source code are executed. Unsupported
constructs remain verbatim. Block boundaries are conservative: a nested list,
table, directive or code block can exceed the size target. Empty input emits no
records. Every nonempty input is partitioned completely, including whitespace.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .indexing_scope import discover_files, is_in_scope, load_config, normalize_relative_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs/documentation_scope.json"
CHUNKER_VERSION = "documentation-v1.2"
TARGET_BYTES = 3000
OVERSIZED_BYTES = 4000
SUPPORTED = {".md", ".rst"}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)]|\([0-9]+\)|#\.)\s+\S")
DIRECTIVE = re.compile(r"^(\s*)\.\.\s+([\w-]+)::")
DISPLAY_MATH = re.compile(r"^ {0,3}\$\$(.*)$")
MATH_CLOSE = re.compile(r"(?<!\\)\$\$")
ENVIRONMENT = re.compile(r"\\(begin|end)\{([^}]+)\}")


@dataclass(frozen=True)
class Block:
    start: int  # line index, inclusive
    end: int    # line index, exclusive
    kind: str
    title: str = ""
    level: int = 0


def _indent(line: str) -> int:
    return len(line.expandtabs(8)) - len(line.expandtabs(8).lstrip())


def _adornment(line: str) -> str | None:
    value = line.strip()
    if value and not line[:1].isspace() and len(value) >= 3 and len(set(value)) == 1 and value[0] in '=~-^"`:+*#_':
        return value[0]
    return None


def _heading(lines: list[str], index: int, suffix: str, styles: dict) -> Block | None:
    line = lines[index].rstrip("\r\n")
    following = lines[index + 1].rstrip("\r\n") if index + 1 < len(lines) else ""
    if suffix == ".md":
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line)
        if match:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2])
            return Block(index, index + 1, "heading", title, len(match[1]))
        if line.strip() and not line[:1].isspace() and re.fullmatch(r" {0,3}(?:={3,}|-{3,})\s*", following):
            return Block(index, index + 2, "heading", line.strip(), 1 if following.strip()[0] == "=" else 2)
        return None
    over = _adornment(line)
    if over and index + 2 < len(lines) and following.strip() and not following[:1].isspace():
        if _adornment(lines[index + 2]) == over:
            style = (over, True)
            level = styles.setdefault(style, len(styles) + 1)
            return Block(index, index + 3, "heading", following.strip(), level)
    under = _adornment(following)
    if line.strip() and not line[:1].isspace() and not over and under:
        style = (under, False)
        level = styles.setdefault(style, len(styles) + 1)
        return Block(index, index + 2, "heading", line.strip(), level)
    return None


def _literal_closer(line: str) -> tuple[str, str] | None:
    """Bounded literal blocks only, not HTML/Liquid parsing or evaluation.

    Stop at the first matching close marker. Arbitrarily nested HTML is outside
    this scanner's scope. Markdown fenced/indented code takes precedence.
    """
    if re.match(r"^ {0,3}<!--", line):
        return r"-->", "html_comment"
    html = re.match(r"^ {0,3}<(script|style|pre|head|p|div|center|a|table)(?=[\s>])", line, re.I)
    if html:
        return r"</" + html[1] + r"\s*>", "html_" + html[1].lower()
    liquid = re.match(r"^ {0,3}\{%-?\s*(raw|comment)\s*-?%\}", line)
    if liquid:
        return r"\{%-?\s*end" + liquid[1] + r"\s*-?%\}", "liquid_" + liquid[1]
    return None


def scan_blocks(lines: list[str], suffix: str) -> tuple[list[Block], list[dict]]:
    """Scan the tested syntax subset; preserve all text even on malformed input."""
    blocks, warnings, styles = [], [], {}
    i = 0

    def warn(line, message):
        warnings.append({"line": line + 1, "message": message})

    def special(index):
        line = lines[index]
        return (not line.strip() or FENCE.match(line) or DIRECTIVE.match(line)
                or LIST.match(line) or _indent(line) >= 4 or ENVIRONMENT.search(line)
                or (suffix == ".md" and (DISPLAY_MATH.match(line) or _literal_closer(line)))
                or _heading(lines, index, suffix, dict(styles)) is not None
                or line.lstrip().startswith(("|", ".. ")))

    while i < len(lines):
        start = i
        line = lines[i]
        fence = FENCE.match(line) if suffix == ".md" else None
        directive = DIRECTIVE.match(line) if suffix == ".rst" else None
        literal = _literal_closer(line) if suffix == ".md" else None
        if suffix == ".md" and i == 0 and line.strip() == "---":
            end = next((j + 1 for j in range(1, len(lines)) if lines[j].strip() in {"---", "..."}), None)
            if end is None:
                warn(start, "Unclosed YAML front matter; preserved conservatively")
                i += 1
                while i < len(lines) and lines[i].strip() and not special(i):
                    i += 1
            else:
                i = end
            kind = "front_matter"
        elif fence:
            marker = fence[1]
            i += 1
            while i < len(lines) and not re.fullmatch(r" {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*[\r\n]*", lines[i]):
                i += 1
            if i < len(lines):
                i += 1
            else:
                warn(start, "Unclosed Markdown fence; preserved through end of file")
            kind = "fenced_code"
        elif literal:
            closer, kind = literal
            i += 1
            if not re.search(closer, line, re.I):
                while i < len(lines) and not re.search(closer, lines[i], re.I):
                    i += 1
                if i < len(lines):
                    i += 1
                else:
                    warn(start, "Unclosed literal markup; preserved through end of file")
            if kind in {"html_script", "html_style", "html_head", "liquid_raw", "liquid_comment"}:
                warn(start, f"Literal {kind} preserved without rendering, expansion or execution")
        elif directive:
            base, name = _indent(line), directive[2]
            i += 1
            while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > base):
                i += 1
            kind = "rst_directive"
            if name not in {"code-block", "code", "sourcecode", "figure", "image", "math", "note", "warning", "tip", "admonition"}:
                warn(start, f"Unsupported directive {name!r}; preserved without execution or expansion")
        elif suffix == ".md" and (display := DISPLAY_MATH.match(line)):
            # A display equation is atomic even when it contains blank lines.
            # Inline prose math is outside this deliberately bounded scanner.
            end = i + 1 if MATH_CLOSE.search(display[1]) else None
            if end is None:
                for j in range(i + 1, len(lines)):
                    if MATH_CLOSE.search(lines[j]):
                        end = j + 1
                        break
                    if FENCE.match(lines[j]) or _heading(lines, j, suffix, dict(styles)):
                        break
            if end is None:
                warn(start, "Unclosed Markdown display math; preserved conservatively")
                i += 1
                while i < len(lines) and lines[i].strip() and not special(i):
                    i += 1
            else:
                i = end
            kind = "display_math"
        elif ENVIRONMENT.search(line):
            # Only pair balanced environments before the next section. An orphan
            # end marker is its own preserved block, never a scan-to-EOF trigger.
            stack, end, malformed = [], None, None
            for j in range(i, len(lines)):
                if j > i and _heading(lines, j, suffix, dict(styles)):
                    break
                for token in ENVIRONMENT.finditer(lines[j]):
                    action, name = token.groups()
                    if action == "begin":
                        stack.append(name)
                    elif not stack or stack[-1] != name:
                        malformed = f"Unmatched equation/environment end marker: {name}"
                        break
                    else:
                        stack.pop()
                if malformed:
                    break
                if not stack:
                    end = j + 1
                    break
            if end is None:
                warn(start, malformed or "Unclosed equation/environment; preserved conservatively")
                # Bound malformed material to its paragraph, not the rest of file.
                i += 1
                while i < len(lines) and lines[i].strip() and not special(i):
                    i += 1
            else:
                i = end
            kind = "equation"
        elif LIST.match(line):
            base = _indent(line)
            i += 1
            while i < len(lines):
                if not lines[i].strip():
                    i += 1
                elif _indent(lines[i]) > base or (LIST.match(lines[i]) and _indent(lines[i]) == base):
                    i += 1
                else:
                    break
            kind = "list"
        elif _indent(line) >= 4:
            i += 1
            while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) >= 4):
                i += 1
            kind = "indented_code"
        else:
            heading = _heading(lines, i, suffix, styles)
            if heading:
                blocks.append(heading)
                i = heading.end
                continue
            if not line.strip():
                i += 1
                while i < len(lines) and not lines[i].strip():
                    i += 1
                kind = "whitespace"
            elif line.lstrip().startswith("|") or (suffix == ".rst" and re.match(r"^\+[=+-]+\+\s*$", line)):
                i += 1
                while i < len(lines) and lines[i].strip():
                    i += 1
                kind = "table"
            elif suffix == ".rst" and line.startswith(".. "):
                i += 1
                while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > 0):
                    i += 1
                kind = "rst_comment_or_target"
            else:
                i += 1
                while i < len(lines) and not special(i):
                    i += 1
                kind = "paragraph"
        blocks.append(Block(start, i, kind))
    return blocks, warnings


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def chunk_document(source: bytes, repository: str, relative_path: str,
                   *, target_bytes: int = TARGET_BYTES, oversized_bytes: int = OVERSIZED_BYTES) -> tuple[list[dict], dict]:
    """Partition one file into lossless records plus diagnostics. No filesystem IO."""
    relative_path = normalize_relative_path(relative_path)
    # Reuse the existing reader's repository validation, rather than new rules.
    config = {"schema_version": 1, "repositories": {repository: {
        "include_directories": [], "extensions": [], "include_files": [relative_path],
        "exclude_directories": [], "exclude_files": []}}}
    is_in_scope(config, repository, relative_path)
    suffix = Path(relative_path).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported document: {relative_path}")
    if type(target_bytes) is not int or type(oversized_bytes) is not int or not 0 < target_bytes <= oversized_bytes:
        raise ValueError("Require 0 < target_bytes <= oversized_bytes (integer UTF-8 bytes)")
    source.decode("utf-8")  # strict validation; no newline conversion
    raw_lines = source.splitlines(keepends=True)
    lines = [line.decode("utf-8") for line in raw_lines]
    offsets = [0]
    for line in raw_lines:
        offsets.append(offsets[-1] + len(line))
    blocks, warnings = scan_blocks(lines, suffix)
    records, sections, stack = [], [], []
    section_id, part, pending = 0, 0, []
    file_digest = sha256(source).hexdigest()

    def flush():
        nonlocal part, pending
        if not pending:
            return
        part += 1
        start, end = offsets[pending[0].start], offsets[pending[-1].end]
        content = source[start:end].decode("utf-8")
        heading_path = [item[1] for item in stack]
        title = heading_path[-1] if heading_path else "Document preamble"
        label = " / ".join(heading_path) or title
        identity = ["documentation", CHUNKER_VERSION, repository, relative_path, file_digest,
                    {"target_bytes": target_bytes, "oversized_bytes": oversized_bytes}, start, end, section_id, part]
        oversized = [{"kind": b.kind, "start_line": b.start + 1,
                      "bytes": offsets[b.end] - offsets[b.start]} for b in pending
                     if offsets[b.end] - offsets[b.start] > oversized_bytes]
        records.append({
            "chunk_id": "doc-" + _digest(identity), "repository": repository,
            "source_path": f"repos/{repository}/{relative_path}", "source_role": "documentation",
            "chunk_type": "documentation_section", "node_type": "documentation_section",
            "symbol": f"{label}::section_{section_id}::part_{part}", "section_title": title,
            "parent_context": {"heading_path": heading_path}, "content": content,
            "start_line": pending[0].start + 1,
            "end_line": bisect_right(offsets, end - 1),
            "byte_start": start, "byte_end": end,
            "structural_metadata": {"format": suffix[1:], "chunker_version": CHUNKER_VERSION,
                                    "section_index": section_id, "part_index": part,
                                    "oversized": end - start > oversized_bytes,
                                    "oversized_reason": "Unsplittable structural block" if oversized else None,
                                    "oversized_blocks": oversized},
        })
        pending = []

    for block in blocks:
        if block.kind == "heading":
            flush()
            section_id += 1
            part = 0
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.title))
            sections.append({"title": block.title, "level": block.level,
                             "line": block.start + 1, "heading_path": [item[1] for item in stack]})
        size = offsets[block.end] - offsets[block.start]
        if pending and offsets[block.end] - offsets[pending[0].start] > target_bytes:
            # Avoid emitting whitespace-only fragments or separating a bare
            # heading from its first block. A whole oversized atom stays intact.
            if any(b.kind not in {"heading", "whitespace"} for b in pending):
                flush()
        pending.append(block)
        if size > oversized_bytes:
            warnings.append({"line": block.start + 1,
                             "message": f"Oversized atomic {block.kind}: {size} UTF-8 bytes; preserved intact"})
    flush()
    if not source:
        warnings.append({"line": 0, "message": "Empty document: zero chunks, zero source bytes"})
    validate_records(records, {(repository, relative_path): source})
    sizes = [len(r["content"].encode("utf-8")) for r in records]
    report = {"repository": repository, "path": relative_path, "sha256": file_digest,
              "source_bytes": len(source), "line_count": len(lines), "chunk_count": len(records),
              "chunk_sizes": sizes, "min_bytes": min(sizes, default=0), "max_bytes": max(sizes, default=0),
              "sections": sections, "warnings": warnings,
              "oversized_chunks": [{"symbol": r["symbol"], "bytes": len(r["content"].encode("utf-8")),
                                    "reason": r["structural_metadata"]["oversized_reason"]}
                                   for r in records if r["structural_metadata"]["oversized"]],
              "integrity": "PASS", "covered_bytes": len(source), "missing_byte_ranges": [],
              "overlapping_byte_ranges": [], "duplicate_ids": 0}
    return records, report


def validate_records(records: list[dict], sources: dict[tuple[str, str], bytes]) -> None:
    """Reject malformed metadata, duplicate identities and non-partitioning ranges."""
    required = {"chunk_id", "repository", "source_role", "source_path", "content", "symbol",
                "section_title", "chunk_type", "node_type", "start_line", "end_line", "byte_start", "byte_end",
                "parent_context", "structural_metadata"}
    ids, evidence, grouped = set(), set(), {key: [] for key in sources}
    for record in records:
        if not required <= record.keys():
            raise ValueError("Documentation record missing required fields")
        identifier = record["chunk_id"]
        if not isinstance(identifier, str) or not identifier.startswith("doc-") or identifier in ids:
            raise ValueError("Invalid or duplicate chunk ID")
        ids.add(identifier)
        repository, path = record["repository"], record["source_path"]
        prefix = f"repos/{repository}/"
        if not isinstance(path, str) or not path.startswith(prefix):
            raise ValueError("Invalid source path")
        relative = normalize_relative_path(path[len(prefix):])
        key = (repository, relative)
        if key not in sources or record["source_role"] != "documentation" or any(
            record[k] != "documentation_section" for k in ("node_type", "chunk_type")
        ):
            raise ValueError("Invalid documentation metadata")
        if not isinstance(record["symbol"], str) or not record["symbol"]:
            raise ValueError("Missing unique documentation symbol")
        evidence_key = (repository, path, record["symbol"])
        if evidence_key in evidence:
            raise ValueError("Duplicate retrieval evidence key")
        evidence.add(evidence_key)
        grouped[key].append(record)
    for key, source in sources.items():
        cursor = 0
        offsets = [0]
        for line in source.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        for record in grouped[key]:
            start, end = record["byte_start"], record["byte_end"]
            if type(start) is not int or type(end) is not int or start != cursor or not start < end <= len(source):
                raise ValueError("Source byte coverage gap, overlap, or invalid range")
            if record["content"].encode("utf-8") != source[start:end]:
                raise ValueError("Source content integrity mismatch")
            if any(type(record[k]) is not int for k in ("start_line", "end_line")) or (record["start_line"], record["end_line"]) != (bisect_right(offsets, start), bisect_right(offsets, end - 1)):
                raise ValueError("Invalid source line range")
            cursor = end
        if cursor != len(source):
            raise ValueError("Missing source coverage")


def generate_documentation(project_root: Path, output_jsonl: Path, output_summary: Path,
                           config_path: Path = DEFAULT_CONFIG, *, target_bytes: int = TARGET_BYTES,
                           oversized_bytes: int = OVERSIZED_BYTES, file_observer=None) -> dict:
    """Validate in memory and staging, then publish via exclusive hard links.

    Each publication is atomic/no-clobber; the pair is NOT transactionally atomic.
    On failure, remove our published links if still owned, and all staging files.
    Destination parents must already exist; no output directories are created.
    """
    project_root = Path(project_root).resolve()
    destinations = [Path(output_jsonl).absolute(), Path(output_summary).absolute()]
    if destinations[0].resolve() == destinations[1].resolve():
        raise ValueError("JSONL and summary destinations must be distinct")
    for path in destinations:
        if os.path.lexists(path):
            raise FileExistsError(f"Refusing existing output destination: {path}")
        if not path.parent.is_dir():
            raise FileNotFoundError(f"Output parent directory unavailable: {path.parent}")
    config = load_config(config_path)
    sources, records, reports = {}, [], []
    for repository, rules in config["repositories"].items():
        if rules["include_directories"]:
            raise ValueError("Documentation scope must use an explicit file whitelist")
        root = project_root / "repos" / repository
        for relative in rules["include_files"]:
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(f"Configured document missing: {repository}/{relative}")
            if path.suffix.lower() not in SUPPORTED:
                raise ValueError(f"Unsupported configured document: {relative}")
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Document resolves outside repository: {relative}")
            if not is_in_scope(config, repository, relative):
                raise ValueError(f"Configured document excluded: {relative}")
        for path in discover_files(config, repository, root):
            relative = path.relative_to(root).as_posix()
            source = path.read_bytes()
            sources[(repository, relative)] = source
    for (repository, relative), source in sources.items():
        chunks, report = chunk_document(source, repository, relative, target_bytes=target_bytes,
                                        oversized_bytes=oversized_bytes)
        records.extend(chunks)
        reports.append(report)
        if file_observer is not None:
            file_observer(project_root / "repos" / repository / relative, chunks, [], [], None)
    validate_records(records, sources)
    summary = {"chunker_version": CHUNKER_VERSION, "document_count": len(sources),
               "total_chunk_count": len(records), "target_bytes": target_bytes,
               "oversized_bytes": oversized_bytes, "integrity": "PASS", "duplicate_ids": 0,
               "warning_count": sum(len(r["warnings"]) for r in reports), "documents": reports}
    staged, published = [], []
    try:
        payloads = ["".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
                    json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"]
        for path, payload in zip(destinations, payloads):
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".documentation-stage-", dir=path.parent, delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(payload.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        validate_records([json.loads(line) for line in staged[0].read_text(encoding="utf-8").splitlines()], sources)
        if json.loads(staged[1].read_text(encoding="utf-8")) != summary:
            raise ValueError("Staged summary validation failed")
        for stage, destination in zip(staged, destinations):
            os.link(stage, destination)  # fails if destination exists, including races
            published.append((stage, destination))
    except Exception as error:
        cleanup_errors = []
        for stage, destination in reversed(published):
            try:
                if destination.exists() and os.path.samefile(stage, destination):
                    destination.unlink()
            except OSError as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        raise RuntimeError(f"Documentation output staging/publication failed: {error}; rollback errors: {cleanup_errors}") from error
    finally:
        for stage in staged:
            stage.unlink(missing_ok=True)
    return summary
