"""Read-only Policy D+ simulation from existing raw evaluation JSON.

No chunker API is invoked and no upstream source file is read. Tree-sitter is
used only to parse the existing content string of an oversized raw function.
"""

from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import fmean, median

from tree_sitter_language_pack import get_parser


PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = PROJECT_ROOT / "data/evaluation/treesitter_chunker/raw"
OUTPUT_ROOT = Path(__file__).resolve().parent
OVERSIZED_BYTES = 5000
WINDOW_LINES = 50
PRIMARY_TYPES = {"function_definition", "method_declaration"}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def byte_size(chunk: dict) -> int:
    return chunk["byte_end"] - chunk["byte_start"]


def line_span(chunk: dict) -> int:
    return chunk["end_line"] - chunk["start_line"] + 1


def strictly_contains(parent: dict, child: dict) -> bool:
    return (
        parent["byte_start"] <= child["byte_start"]
        and child["byte_end"] <= parent["byte_end"]
        and byte_size(parent) > byte_size(child)
    )


def interval_union_size(chunks: list[dict]) -> int:
    intervals = sorted((chunk["byte_start"], chunk["byte_end"]) for chunk in chunks)
    if not intervals:
        return 0
    total = 0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def metrics(chunks: list[dict]) -> dict:
    values = [byte_size(chunk) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "size_bytes": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
            "mean": fmean(values) if values else 0,
            "median": median(values) if values else 0,
        },
        "line_span": {
            "min": min((line_span(chunk) for chunk in chunks), default=0),
            "max": max((line_span(chunk) for chunk in chunks), default=0),
            "mean": fmean([line_span(chunk) for chunk in chunks]) if chunks else 0,
            "median": median([line_span(chunk) for chunk in chunks]) if chunks else 0,
        },
        "node_types": dict(sorted(Counter(chunk["node_type"] for chunk in chunks).items())),
        "oversized_chunks_remaining_gt_5000_bytes": sum(
            byte_size(chunk) > OVERSIZED_BYTES for chunk in chunks
        ),
    }


def overlap(chunks: list[dict]) -> dict:
    grouped = defaultdict(list)
    for chunk in chunks:
        grouped[chunk["file"]].append(chunk)
    nested = sum(
        sum(
            any(strictly_contains(parent, child) for parent in file_chunks)
            for child in file_chunks
        )
        for file_chunks in grouped.values()
    )
    total = sum(byte_size(chunk) for chunk in chunks)
    unique = sum(interval_union_size(file_chunks) for file_chunks in grouped.values())
    return {
        "nested_chunk_count": nested,
        "total_chunk_bytes_sum": total,
        "unique_covered_bytes": unique,
        "redundancy_bytes": total - unique,
        "duplication_factor": total / unique if unique else 0,
    }


def parent_metadata(parent: dict) -> dict:
    return {
        "parent_chunk_id": parent["chunk_id"],
        "parent_node_type": parent["node_type"],
        "parent_start_line": parent["start_line"],
        "parent_end_line": parent["end_line"],
        "parent_byte_start": parent["byte_start"],
        "parent_byte_end": parent["byte_end"],
        "parent_route": parent.get("qualified_route") or parent.get("parent_route"),
    }


def copy_primary(chunk: dict) -> dict:
    return {
        "file": chunk["file_path"],
        "node_type": chunk["node_type"],
        "start_line": chunk["start_line"],
        "end_line": chunk["end_line"],
        "byte_start": chunk["byte_start"],
        "byte_end": chunk["byte_end"],
        "chunk_id": chunk["chunk_id"],
        "parent_route": chunk.get("qualified_route") or chunk.get("parent_route"),
        "content": chunk["content"],
        "selection_kind": "original_function_or_method",
    }


def line_window_subchunks(parent: dict) -> list[dict]:
    lines = parent["content"].splitlines(keepends=True)
    output = []
    local_byte_start = 0
    for index, start in enumerate(range(0, len(lines), WINDOW_LINES)):
        text = "".join(lines[start : start + WINDOW_LINES])
        local_byte_end = local_byte_start + len(text.encode("utf-8"))
        output.append(
            {
                **parent_metadata(parent),
                "file": parent["file_path"],
                "node_type": "line_window_subchunk",
                "subchunk_index": index,
                "start_line": parent["start_line"] + start,
                "end_line": parent["start_line"] + start + len(lines[start : start + WINDOW_LINES]) - 1,
                "byte_start": parent["byte_start"] + local_byte_start,
                "byte_end": parent["byte_start"] + local_byte_end,
                "content": text,
                "selection_kind": "fixed_50_line_window",
                "boundary_start": "fixed_line_window",
                "boundary_end": "fixed_line_window",
            }
        )
        local_byte_start = local_byte_end
    return output


def statement_boundary_subchunks(parent: dict) -> list[dict]:
    text = parent["content"]
    src = text.encode("utf-8")
    tree = get_parser("cpp").parse(src)
    function = next(
        node for node in tree.root_node.named_children if node.type == "function_definition"
    )
    body = next(node for node in function.named_children if node.type == "compound_statement")
    structural_starts = {
        src.rfind(b"\n", 0, node.start_byte) + 1: node.type
        for node in body.named_children
        if node.type != "comment"
    }
    boundaries = [0, *structural_starts, len(src)]
    boundaries = sorted(set(boundaries))
    units = list(zip(boundaries, boundaries[1:]))
    segments: list[tuple[int, int]] = []
    segment_start = units[0][0]
    segment_end = segment_start
    for unit_start, unit_end in units:
        if segment_end > segment_start and unit_end - segment_start > OVERSIZED_BYTES:
            segments.append((segment_start, unit_start))
            segment_start = unit_start
        segment_end = unit_end
    if segment_start < len(src):
        segments.append((segment_start, len(src)))

    def boundary_type(position: int, is_start: bool) -> str:
        if position == 0:
            return "function_signature_and_opening_brace"
        if position == len(src):
            return "function_closing_brace"
        if position in structural_starts:
            return structural_starts[position]
        return "tree_sitter_statement_boundary"

    output = []
    for index, (local_start, local_end) in enumerate(segments):
        content = src[local_start:local_end].decode("utf-8")
        start_line = parent["start_line"] + text[:local_start].count("\n")
        end_line = start_line + content.rstrip("\n").count("\n")
        output.append(
            {
                **parent_metadata(parent),
                "file": parent["file_path"],
                "node_type": "tree_sitter_statement_subchunk",
                "subchunk_index": index,
                "start_line": start_line,
                "end_line": end_line,
                "byte_start": parent["byte_start"] + local_start,
                "byte_end": parent["byte_start"] + local_end,
                "content": content,
                "selection_kind": "tree_sitter_top_level_statement_boundary",
                "boundary_start": boundary_type(local_start, True),
                "boundary_end": boundary_type(local_end, False),
            }
        )
    return output


def policy_chunks(primary: list[dict], policy: str) -> list[dict]:
    output = []
    for chunk in primary:
        if byte_size(chunk) <= OVERSIZED_BYTES or policy == "D1":
            output.append(copy_primary(chunk))
        elif policy == "D2":
            output.extend(line_window_subchunks(chunk))
        elif policy == "D3":
            output.extend(statement_boundary_subchunks(chunk))
        else:
            raise ValueError(policy)
    return output


def summarize_by_file(chunks: list[dict]) -> dict:
    grouped = defaultdict(list)
    for chunk in chunks:
        grouped[chunk["file"]].append(chunk)
    return {
        path: {**metrics(items), "overlap": overlap(items)}
        for path, items in sorted(grouped.items())
    }


def main() -> None:
    raw_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(RAW_ROOT.glob("*.json"))]
    primary = [
        chunk
        for payload in raw_payloads
        for chunk in payload["chunks"]
        if chunk["node_type"] in PRIMARY_TYPES
    ]
    policies = {policy: policy_chunks(primary, policy) for policy in ("D1", "D2", "D3")}
    for policy, chunks in policies.items():
        write_json(OUTPUT_ROOT / f"{policy.lower()}_chunks.json", chunks)

    comparison = {
        "parameters": {
            "primary_node_types": sorted(PRIMARY_TYPES),
            "oversized_threshold_bytes": OVERSIZED_BYTES,
            "d2_fixed_line_window_size": WINDOW_LINES,
            "d3_boundary_method": "top-level non-comment nodes in the Tree-sitter compound_statement parsed from existing raw function content",
        },
        "policies": {
            policy: {
                "aggregate": {**metrics(chunks), "overlap": overlap(chunks)},
                "per_file": summarize_by_file(chunks),
            }
            for policy, chunks in policies.items()
        },
    }
    write_json(OUTPUT_ROOT / "policy_comparison.json", comparison)

    dambreak_file = next(
        path for path in comparison["policies"]["D1"]["per_file"] if path.endswith("Dambreak.cpp")
    )
    dambreak = {
        "file": dambreak_file,
        "original_main": next(
            chunk
            for chunk in policies["D1"]
            if chunk["file"].endswith("Dambreak.cpp") and "main(" in chunk["content"]
        ),
        "D2_line_windows": [
            chunk for chunk in policies["D2"] if chunk["file"].endswith("Dambreak.cpp") and "parent_chunk_id" in chunk
        ],
        "D3_statement_boundary_subchunks": [
            chunk for chunk in policies["D3"] if chunk["file"].endswith("Dambreak.cpp") and "parent_chunk_id" in chunk
        ],
    }
    write_json(OUTPUT_ROOT / "dambreak_main_examples.json", dambreak)

    lines = [
        "# Policy D+ Retrieval Simulation",
        "",
        "This analysis consumes existing raw JSON only. It does not rerun treesitter-chunker or read upstream source files.",
        "",
        "- D1: function/method chunks only.",
        "- D2: replace functions over 5,000 bytes with fixed 50-line windows.",
        "- D3: replace functions over 5,000 bytes with contiguous segments whose internal boundaries align to top-level non-comment Tree-sitter statement/block nodes where possible.",
        "",
        "## Aggregate results",
        "",
        "| Policy | Chunks | Min B | Max B | Mean B | Median B | Overlap factor | Oversized >5000 B |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for policy, detail in comparison["policies"].items():
        aggregate = detail["aggregate"]
        sizes = aggregate["size_bytes"]
        lines.append(
            f"| {policy} | {aggregate['chunk_count']} | {sizes['min']} | {sizes['max']} | {sizes['mean']:.2f} | {sizes['median']} | {aggregate['overlap']['duplication_factor']:.2f} | {aggregate['oversized_chunks_remaining_gt_5000_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## Dambreak main()",
            "",
            "See `dambreak_main_examples.json` for exact spans, parent metadata, boundary labels, and unmodified substring content for D2 and D3.",
            "",
            "D2 boundaries are fixed line-count boundaries. D3 boundaries are Tree-sitter top-level statement/block boundaries; every D3 subchunk retains the original function's ID and full span as parent metadata.",
        ]
    )
    (OUTPUT_ROOT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
