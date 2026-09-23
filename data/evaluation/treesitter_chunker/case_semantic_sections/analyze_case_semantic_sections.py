"""E1 case-aware semantic-section prototype using existing evaluation JSON only."""

import json
from pathlib import Path
import re
from statistics import fmean, median

from tree_sitter_language_pack import get_parser


PROJECT_ROOT = Path(__file__).resolve().parents[4]
RAW_DAMBREAK = PROJECT_ROOT / "data/evaluation/treesitter_chunker/raw/Dambreak.cpp.json"
D3_CHUNKS = PROJECT_ROOT / "data/evaluation/treesitter_chunker/policy_d_plus/d3_chunks.json"
OUTPUT_ROOT = Path(__file__).resolve().parent
OVERSIZED_BYTES = 5000
SEPARATOR_RE = re.compile(r"^\s*//-{10,}\s*$")
COMMENT_RE = re.compile(r"^\s*//\s?(.*)$")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def size(chunk: dict) -> int:
    return chunk["byte_end"] - chunk["byte_start"]


def metrics(chunks: list[dict]) -> dict:
    sizes = [size(chunk) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "size_bytes": {
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
            "mean": fmean(sizes) if sizes else 0,
            "median": median(sizes) if sizes else 0,
        },
        "oversized_chunks_remaining_gt_5000_bytes": sum(
            value > OVERSIZED_BYTES for value in sizes
        ),
    }


def local_line_start(text: str, offset: int) -> int:
    return len(text[:offset].encode("utf-8"))


def find_main(raw_payload: dict) -> dict:
    return next(
        chunk
        for chunk in raw_payload["chunks"]
        if chunk["node_type"] == "function_definition" and re.search(r"\bmain\s*\(", chunk["content"])
    )


def detect_headers(text: str, function_start_line: int) -> tuple[list[dict], list[dict]]:
    lines = text.splitlines(keepends=True)
    offsets = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line.encode("utf-8"))
    headers = []
    issues = []
    index = 0
    while index < len(lines):
        if not SEPARATOR_RE.match(lines[index]):
            index += 1
            continue
        closing = index + 1
        while closing < len(lines) and not SEPARATOR_RE.match(lines[closing]):
            closing += 1
        if closing >= len(lines):
            issues.append(
                {
                    "line": function_start_line + index,
                    "issue": "separator block has no closing separator",
                }
            )
            index += 1
            continue
        title_parts = []
        valid_comment_block = True
        for content_index in range(index + 1, closing):
            match = COMMENT_RE.match(lines[content_index])
            if not match:
                valid_comment_block = False
                break
            value = match.group(1).strip()
            if value:
                title_parts.append(value)
        if not valid_comment_block or not title_parts:
            issues.append(
                {
                    "line": function_start_line + index,
                    "issue": "separator block has no usable comment title",
                }
            )
        else:
            headers.append(
                {
                    "title": " ".join(title_parts),
                    "header_start_line": function_start_line + index,
                    "header_end_line": function_start_line + closing,
                    "byte_start": offsets[index],
                    "raw_header_content": "".join(lines[index : closing + 1]),
                }
            )
        index = closing + 1
    return headers, issues


def local_line_number(text: str, position: int, function_start_line: int) -> int:
    return function_start_line + text[:position].count("\n")


def exclusive_end_line(text: str, position: int, function_start_line: int) -> int:
    """Return the last included line for an exclusive byte boundary."""
    line = local_line_number(text, position, function_start_line)
    src = text.encode("utf-8")
    return line - 1 if position > 0 and src[position - 1 : position] == b"\n" else line


def section_node_types(nodes: list, local_start: int, local_end: int) -> list[str]:
    return sorted(
        {
            node.type
            for node in nodes
            if node.start_byte < local_end and local_start < node.end_byte
        }
    )


def parent_metadata(parent: dict) -> dict:
    return {
        "original_function_chunk_id": parent["chunk_id"],
        "original_function_start_line": parent["start_line"],
        "original_function_end_line": parent["end_line"],
        "original_function_byte_start": parent["byte_start"],
        "original_function_byte_end": parent["byte_end"],
        "source_file": parent["file_path"],
        "source_role": "simulation_case",
    }


def subdivide_section(
    section: dict, text: str, parent: dict, structural_nodes: list
) -> list[dict]:
    """Split an oversized semantic section only at top-level statement/block lines."""
    starts_to_types = {
        text.rfind("\n", 0, node.start_byte) + 1: node.type
        for node in structural_nodes
        if section["local_start"] < node.start_byte < section["local_end"]
    }
    boundaries = sorted({section["local_start"], *starts_to_types, section["local_end"]})
    segments = []
    segment_start = boundaries[0]
    for boundary in boundaries[1:]:
        if boundary > segment_start and boundary - segment_start > OVERSIZED_BYTES:
            prior = max(
                value for value in boundaries if segment_start < value < boundary
            )
            segments.append((segment_start, prior))
            segment_start = prior
        if boundary == section["local_end"]:
            segments.append((segment_start, boundary))
    output = []
    for index, (start, end) in enumerate(segments):
        output.append(
            make_chunk(
                section,
                text,
                parent,
                structural_nodes,
                start,
                end,
                index,
                True,
                starts_to_types.get(start, "semantic_section_start"),
                starts_to_types.get(end, "semantic_section_end"),
            )
        )
    return output


def make_chunk(
    section: dict,
    text: str,
    parent: dict,
    nodes: list,
    local_start: int,
    local_end: int,
    subchunk_index: int,
    subdivided: bool,
    boundary_start: str,
    boundary_end: str,
) -> dict:
    content = text.encode("utf-8")[local_start:local_end].decode("utf-8")
    return {
        **parent_metadata(parent),
        "semantic_section_index": section["index"],
        "semantic_section_title": section["title"],
        "semantic_section_start_line": section["start_line"],
        "semantic_section_end_line": section["end_line"],
        "subchunk_index_within_section": subchunk_index,
        "was_subdivided_for_size": subdivided,
        "start_line": local_line_number(text, local_start, parent["start_line"]),
        "end_line": exclusive_end_line(text, local_end, parent["start_line"]),
        "byte_start": parent["byte_start"] + local_start,
        "byte_end": parent["byte_start"] + local_end,
        "byte_size": local_end - local_start,
        "tree_sitter_node_types_contained": section_node_types(nodes, local_start, local_end),
        "boundary_start": boundary_start,
        "boundary_end": boundary_end,
        "content": content,
    }


def main() -> None:
    parent = find_main(read_json(RAW_DAMBREAK))
    text = parent["content"]
    src = text.encode("utf-8")
    tree = get_parser("cpp").parse(src)
    function = next(node for node in tree.root_node.named_children if node.type == "function_definition")
    body = next(node for node in function.named_children if node.type == "compound_statement")
    all_nodes = list(body.named_children)
    structural_nodes = [node for node in all_nodes if node.type != "comment"]

    headers, issues = detect_headers(text, parent["start_line"])
    boundary_conflicts = []
    for header in headers:
        for node in structural_nodes:
            if node.start_byte < header["byte_start"] < node.end_byte:
                boundary_conflicts.append(
                    {
                        "title": header["title"],
                        "header_start_line": header["header_start_line"],
                        "containing_node_type": node.type,
                    }
                )

    section_starts = [0, *(header["byte_start"] for header in headers)]
    section_titles = ["Function preamble (before first semantic section)", *(header["title"] for header in headers)]
    sections = []
    for index, (start, title) in enumerate(zip(section_starts, section_titles)):
        end = section_starts[index + 1] if index + 1 < len(section_starts) else len(src)
        sections.append(
            {
                "index": index,
                "title": title,
                "local_start": start,
                "local_end": end,
                "start_line": local_line_number(text, start, parent["start_line"]),
                "end_line": exclusive_end_line(text, end, parent["start_line"]),
            }
        )

    e1_chunks = []
    subdivisions = []
    for section in sections:
        if section["local_end"] - section["local_start"] > OVERSIZED_BYTES:
            generated = subdivide_section(section, text, parent, structural_nodes)
            subdivisions.append(
                {
                    "title": section["title"],
                    "original_section_byte_size": section["local_end"] - section["local_start"],
                    "subchunk_count": len(generated),
                }
            )
            e1_chunks.extend(generated)
        else:
            e1_chunks.append(
                make_chunk(
                    section,
                    text,
                    parent,
                    structural_nodes,
                    section["local_start"],
                    section["local_end"],
                    0,
                    False,
                    "semantic_section_start",
                    "semantic_section_end",
                )
            )

    d3_chunks = [
        chunk
        for chunk in read_json(D3_CHUNKS)
        if chunk.get("parent_chunk_id") == parent["chunk_id"]
    ]
    comparison = {
        "parameters": {
            "source_role": "simulation_case",
            "oversized_threshold_bytes": OVERSIZED_BYTES,
            "section_header_pattern": "separator comment line + one or more comment title lines + separator comment line",
            "tree_sitter_safety_rule": "semantic boundaries must not fall inside a top-level non-comment function-body node",
        },
        "D3_tree_sitter_statement_boundary": metrics(d3_chunks),
        "E1_semantic_comment_sections": metrics(e1_chunks),
        "detected_semantic_section_titles": [header["title"] for header in headers],
        "sections_subdivided_for_size": subdivisions,
        "ambiguous_or_malformed_section_comments": issues,
        "section_boundary_conflicts_with_tree_sitter_nodes": boundary_conflicts,
        "tree_sitter_parse_has_error": tree.root_node.has_error,
    }
    write_json(OUTPUT_ROOT / "e1_chunks.json", e1_chunks)
    write_json(OUTPUT_ROOT / "d3_vs_e1.json", comparison)

    lines = [
        "# E1 Case-Aware Semantic Section Prototype",
        "",
        "This prototype uses only existing Dambreak raw JSON content. It does not rerun treesitter-chunker or access upstream source files.",
        "",
        "## Comparison for Dambreak.cpp::main()",
        "",
        "| Policy | Chunks | Min B | Max B | Mean B | Median B | Oversized >5000 B |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, value in (
        ("D3", comparison["D3_tree_sitter_statement_boundary"]),
        ("E1", comparison["E1_semantic_comment_sections"]),
    ):
        sizes = value["size_bytes"]
        lines.append(
            f"| {label} | {value['chunk_count']} | {sizes['min']} | {sizes['max']} | {sizes['mean']:.2f} | {sizes['median']} | {value['oversized_chunks_remaining_gt_5000_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## Detected semantic section titles",
            "",
            *(f"- {title}" for title in comparison["detected_semantic_section_titles"]),
            "",
            f"- Sections subdivided for size: {len(subdivisions)}",
            f"- Malformed/ambiguous headers: {len(issues)}",
            f"- Tree-sitter boundary conflicts: {len(boundary_conflicts)}",
            "",
            "E1 chunks are contiguous original substrings. Their boundaries start at human-authored separator-comment sections; oversized sections, if any, are subdivided only at top-level non-comment Tree-sitter statement/block boundaries.",
        ]
    )
    (OUTPUT_ROOT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
