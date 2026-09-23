"""Read-only analysis of existing treesitter-chunker evaluation JSON.

This evaluation artifact does not call the chunker or read upstream source files.
It consumes only data/evaluation/treesitter_chunker/raw/*.json and summaries.
"""

from collections import Counter
import json
from pathlib import Path
import re
from statistics import fmean, median


PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVALUATION_ROOT = PROJECT_ROOT / "data/evaluation/treesitter_chunker"
RAW_ROOT = EVALUATION_ROOT / "raw"
SUMMARY_ROOT = EVALUATION_ROOT / "summaries"
OUTPUT_ROOT = Path(__file__).resolve().parent

RETAINED_TYPES = {
    "class_specifier",
    "struct_specifier",
    "function_definition",
    "method_declaration",
    "template_declaration",
}
PRIMARY_TYPES = {"function_definition", "method_declaration"}
CONTEXT_TYPES = {"class_specifier", "struct_specifier", "template_declaration"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def span_size(chunk: dict) -> int:
    return chunk["byte_end"] - chunk["byte_start"]


def line_span(chunk: dict) -> int:
    return chunk["end_line"] - chunk["start_line"] + 1


def strictly_contains(parent: dict, child: dict) -> bool:
    return (
        parent["byte_start"] <= child["byte_start"]
        and child["byte_end"] <= parent["byte_end"]
        and span_size(parent) > span_size(child)
    )


def interval_union_size(chunks: list[dict]) -> int:
    intervals = sorted((c["byte_start"], c["byte_end"]) for c in chunks)
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


def metrics(values: list[int]) -> dict[str, float | int]:
    return {
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "median": median(values) if values else 0,
        "mean": fmean(values) if values else 0,
    }


def overlap_analysis(chunks: list[dict]) -> tuple[dict, list[dict]]:
    relations = []
    for child in chunks:
        parents = [parent for parent in chunks if strictly_contains(parent, child)]
        if not parents:
            continue
        direct_parent = min(parents, key=span_size)
        relations.append(
            {
                "child_chunk_index": child["chunk_index"],
                "child_chunk_id": child["chunk_id"],
                "child_node_type": child["node_type"],
                "child_start_line": child["start_line"],
                "child_end_line": child["end_line"],
                "child_byte_size": span_size(child),
                "child_route": child.get("qualified_route") or child.get("parent_route"),
                "containing_chunk_count": len(parents),
                "direct_parent_chunk_index": direct_parent["chunk_index"],
                "direct_parent_chunk_id": direct_parent["chunk_id"],
                "direct_parent_node_type": direct_parent["node_type"],
                "direct_parent_start_line": direct_parent["start_line"],
                "direct_parent_end_line": direct_parent["end_line"],
                "direct_parent_byte_size": span_size(direct_parent),
                "direct_parent_route": direct_parent.get("qualified_route")
                or direct_parent.get("parent_route"),
            }
        )
    total_bytes = sum(span_size(chunk) for chunk in chunks)
    union_bytes = interval_union_size(chunks)
    return (
        {
            "nested_chunk_count": len(relations),
            "top_level_chunk_count": len(chunks) - len(relations),
            "nested_chunk_bytes_sum": sum(item["child_byte_size"] for item in relations),
            "total_chunk_bytes_sum": total_bytes,
            "unique_covered_bytes": union_bytes,
            "hierarchical_redundancy_bytes": total_bytes - union_bytes,
            "duplication_factor": total_bytes / union_bytes if union_bytes else 0,
        },
        relations,
    )


def policy_record(chunks: list[dict], policy: str) -> dict:
    if policy == "A":
        selected = chunks
        return {
            "retrieval_chunk_count": len(selected),
            "retrieval_node_types": dict(sorted(Counter(c["node_type"] for c in selected).items())),
        }
    if policy == "B":
        selected = [c for c in chunks if c["node_type"] in RETAINED_TYPES]
        return {
            "retrieval_chunk_count": len(selected),
            "retrieval_node_types": dict(sorted(Counter(c["node_type"] for c in selected).items())),
        }
    if policy == "C":
        candidates = [c for c in chunks if c["node_type"] in RETAINED_TYPES]
        selected = []
        removed = []
        for child in sorted(candidates, key=span_size, reverse=True):
            containers = [parent for parent in selected if strictly_contains(parent, child)]
            # Strict containment means the byte intersection covers 100% of child.
            if containers:
                removed.append(child)
            else:
                selected.append(child)
        return {
            "overlap_rule": "process larger candidates first; remove a candidate when a strictly larger retained candidate covers at least 90% of its bytes",
            "retrieval_chunk_count": len(selected),
            "retrieval_node_types": dict(sorted(Counter(c["node_type"] for c in selected).items())),
            "removed_fully_contained_candidate_count": len(removed),
            "removed_node_types": dict(sorted(Counter(c["node_type"] for c in removed).items())),
        }
    if policy == "D":
        primary = [c for c in chunks if c["node_type"] in PRIMARY_TYPES]
        context = [c for c in chunks if c["node_type"] in CONTEXT_TYPES]
        return {
            "retrieval_chunk_count": len(primary),
            "retrieval_node_types": dict(sorted(Counter(c["node_type"] for c in primary).items())),
            "context_only_chunk_count": len(context),
            "context_only_node_types": dict(sorted(Counter(c["node_type"] for c in context).items())),
        }
    raise ValueError(policy)


def find_dambreak_main(chunks: list[dict]) -> dict | None:
    for chunk in chunks:
        if chunk["node_type"] == "function_definition" and re.search(
            r"\bmain\s*\(", chunk["content"]
        ):
            children = [c for c in chunks if strictly_contains(chunk, c)]
            return {
                "chunk_index": chunk["chunk_index"],
                "byte_size": span_size(chunk),
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "line_span": line_span(chunk),
                "internal_raw_structural_child_count": len(children),
                "internal_raw_structural_children": [
                    {"chunk_index": c["chunk_index"], "node_type": c["node_type"]}
                    for c in children
                ],
            }
    return None


def representative_relations(relations: list[dict]) -> list[dict]:
    """Prefer meaningful nested members over top-level forward declarations."""
    preferred = [
        relation
        for relation in relations
        if relation["direct_parent_node_type"]
        in {"class_specifier", "struct_specifier", "template_declaration"}
    ]
    selected = []
    for child_type in (
        "function_definition",
        "method_declaration",
        "template_declaration",
        "field_declaration",
        "class_specifier",
        "struct_specifier",
    ):
        candidates = sorted(
            (item for item in preferred if item["child_node_type"] == child_type),
            key=lambda item: item["child_byte_size"],
            reverse=True,
        )
        selected.extend(candidates[:3])
    return selected[:12] if selected else relations[:12]


def main() -> None:
    per_file = []
    policy_comparison = {
        "policy_notes": {
            "A": {
                "advantage": "Retains every structural signal and all parent context.",
                "disadvantage": "Retains hierarchy-driven duplicates, including small declarations and overlapping parent chunks.",
            },
            "B": {
                "advantage": "Excludes fields, namespaces, and type definitions while retaining major structural nodes.",
                "disadvantage": "Still retains overlapping class/template/function hierarchies and can retain tiny forward declarations.",
            },
            "C": {
                "advantage": "Substantially reduces byte-overlap among retained structural candidates.",
                "disadvantage": "Can discard function/method chunks when a class or template parent is retained, reducing granular retrieval.",
            },
            "D": {
                "advantage": "Uses function/method chunks as granular retrieval units while preserving parent structures as context metadata.",
                "disadvantage": "Leaves long functions unsplit and requires parent-context association outside the retrieval set.",
            },
        },
        "per_file": {},
    }
    examples = {}
    markdown_rows = []

    for raw_path in sorted(RAW_ROOT.glob("*.json")):
        payload = read_json(raw_path)
        chunks = payload["chunks"]
        summary = read_json(SUMMARY_ROOT / raw_path.name)
        byte_sizes = [span_size(chunk) for chunk in chunks]
        line_spans = [line_span(chunk) for chunk in chunks]
        overlap, relations = overlap_analysis(chunks)
        file_analysis = {
            "file": payload["file"],
            "chunk_count_by_node_type": dict(sorted(Counter(c["node_type"] for c in chunks).items())),
            "chunk_size_distribution": {
                "bytes": metrics(byte_sizes),
                "line_span": metrics(line_spans),
            },
            "tiny_chunks": {
                "lt_50_bytes": sum(size < 50 for size in byte_sizes),
                "lt_100_bytes": sum(size < 100 for size in byte_sizes),
                "lt_200_bytes": sum(size < 200 for size in byte_sizes),
            },
            "large_chunks": {
                "gt_2000_bytes": sum(size > 2000 for size in byte_sizes),
                "gt_5000_bytes": sum(size > 5000 for size in byte_sizes),
                "gt_10000_bytes": sum(size > 10000 for size in byte_sizes),
            },
            "parent_child_structural_overlap": overlap,
            "parsing_errors_reported": summary["parsing_errors_reported"],
        }
        if payload["file"].endswith("Dambreak.cpp"):
            file_analysis["main_function_case"] = find_dambreak_main(chunks)
        per_file.append(file_analysis)
        policy_comparison["per_file"][payload["file"]] = {
            "Policy_A_keep_all_chunks": policy_record(chunks, "A"),
            "Policy_B_structural_types_only": policy_record(chunks, "B"),
            "Policy_C_structural_types_deduplicated_by_strict_containment": policy_record(chunks, "C"),
            "Policy_D_function_method_primary_with_context_only_parents": policy_record(chunks, "D"),
        }
        if raw_path.name in {"base_body.h.json", "sphinxsys_variable.h.json"}:
            examples[payload["file"]] = representative_relations(relations)
        markdown_rows.append(
            "| {file} | {count} | {nested} | {factor:.2f} | {tiny} | {large} |".format(
                file=Path(payload["file"]).name,
                count=len(chunks),
                nested=overlap["nested_chunk_count"],
                factor=overlap["duplication_factor"],
                tiny=file_analysis["tiny_chunks"]["lt_100_bytes"],
                large=file_analysis["large_chunks"]["gt_2000_bytes"],
            )
        )

    write_json(OUTPUT_ROOT / "per_file_analysis.json", per_file)
    write_json(OUTPUT_ROOT / "policy_comparison.json", policy_comparison)
    write_json(OUTPUT_ROOT / "overlap_examples.json", examples)

    lines = [
        "# Tree-sitter Chunker Retrieval-Selection Analysis",
        "",
        "This analysis reads existing raw evaluation JSON only; it does not rerun chunking or access upstream source files.",
        "",
        "## Per-file overview",
        "",
        "| File | Raw chunks | Nested chunks | Duplication factor | Chunks <100 B | Chunks >2000 B |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *markdown_rows,
        "",
        "## Policy definitions",
        "",
        "- **A:** retain all raw chunks.",
        "- **B:** retain class, struct, function, method, and template node types.",
        "- **C:** start from B; remove a candidate strictly contained in a larger retained candidate. Strict containment gives 100% byte coverage of the removed candidate, satisfying the ≥90% high-overlap criterion.",
        "- **D:** retain function and method chunks for retrieval; retain class, struct, and template chunks as context-only metadata.",
        "",
        "## Policy advantages and disadvantages",
        "",
        "- **A:** preserves every structural signal, but retains all hierarchy-driven overlap and tiny declarations.",
        "- **B:** filters fields, namespaces, and type definitions, but retains overlap among class/template/function levels.",
        "- **C:** reduces overlap substantially, but can remove granular functions or methods if their enclosing class/template is retained.",
        "- **D:** retains granular functions/methods while keeping parents as context, but leaves long functions intact and needs external parent-context association.",
        "",
        "See `per_file_analysis.json`, `policy_comparison.json`, and `overlap_examples.json` for the complete computed data.",
    ]
    (OUTPUT_ROOT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
