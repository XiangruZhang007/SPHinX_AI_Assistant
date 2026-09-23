"""Read-only, focused treesitter-chunker validation for SPHinXsim-specific code."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from statistics import fmean, median
from typing import Any

from chunker import chunk_file
from tree_sitter_language_pack import get_parser


PROJECT_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = PROJECT_ROOT / "repos" / "SPHinXsim"
OUTPUT_ROOT = Path(__file__).resolve().parent
TARGETS = {
    "sphinxsim/bindings": "simulator_library",
    "sphinxsim/config": "config/schema",
    "sphinxsim/llm": "llm_support",
    "sphinxsim/sph_simulation": "simulator_library",
    "sphinxsim/visualization": "visualization",
    "examples": "simulation_example",
    "tests": "test",
}
REPRESENTATIVES = [
    ("sphinxsim/bindings/loader.py", "Python binding loader", "python", "simulator_library"),
    ("sphinxsim/config/schemas.py", "Python schema library", "python", "config/schema"),
    ("sphinxsim/sph_simulation/simulation_builder/base_simulation_builder.cpp", "C++ simulation builder", "cpp", "simulator_library"),
    ("sphinxsim/bindings/sphinxsys_python.cpp", "C++ Python binding", "cpp", "simulator_library"),
    ("examples/test_simulation_2d.py", "Python simulation orchestration example", "python", "simulation_example"),
    ("examples/input/test_simulation_2d/config.json", "Example simulation input/config", "json", "config/schema"),
    ("tests/test_schemas.py", "Python schema test", "python", "test"),
    ("tests/test_simulation/test_2d_simulation/simulation.cpp", "C++ simulation test", "cpp", "test"),
]
SOURCE_LANGUAGES = {"python", "cpp"}
OVERSIZED_BYTES = 5_000
TINY_BYTES = 100


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def role_for(relative: Path) -> str:
    text = relative.as_posix()
    for prefix, role in TARGETS.items():
        if text == prefix or text.startswith(prefix + "/"):
            return role
    raise ValueError(f"Outside declared validation scope: {relative}")


def inventory() -> dict[str, Any]:
    files: list[Path] = []
    directory_counts: dict[str, int] = {}
    extension_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for root, role in TARGETS.items():
        matches = sorted(path for path in (REPO_ROOT / root).rglob("*") if path.is_file())
        directory_counts[root] = len(matches)
        files.extend(matches)
        extension_counts.update(path.suffix.lower() or "[no extension]" for path in matches)
        role_counts[role] += len(matches)
    return {
        "repository": "SPHinXsim",
        "scope_roots": list(TARGETS),
        "excluded_paths": ["sphinxsim/sphinxsys/"],
        "total_files": len(files),
        "counts_by_directory": directory_counts,
        "counts_by_extension": dict(sorted(extension_counts.items())),
        "counts_by_likely_source_role": dict(sorted(role_counts.items())),
    }


def walk(node: Any):
    yield node
    for child in node.children:
        yield from walk(child)


def integrity(tree: Any, raw_chunks: list[dict[str, Any]], language: str) -> dict[str, Any]:
    class_type = "class_definition" if language == "python" else "class_specifier"
    expected_types = {"function_definition", class_type}
    expected = [node for node in walk(tree.root_node) if node.type in expected_types]
    report: dict[str, Any] = {}
    for node_type, label in (("function_definition", "functions"), (class_type, "classes")):
        nodes = [node for node in expected if node.type == node_type]
        raw_types = {node_type}
        if language == "cpp" and node_type == "function_definition":
            raw_types.add("method_declaration")
        raw_spans = {
            (chunk["byte_start"], chunk["byte_end"])
            for chunk in raw_chunks
            if chunk["node_type"] in raw_types
        }
        intact = sum((node.start_byte, node.end_byte) in raw_spans for node in nodes)
        report[label] = {"ast_count": len(nodes), "raw_chunk_exact_span_count": intact, "all_intact": intact == len(nodes)}
    return report


def check_file(relative: str, purpose: str, language: str, role: str) -> dict[str, Any]:
    source_path = REPO_ROOT / relative
    source = source_path.read_bytes()
    common = {
        "source_path": f"repos/SPHinXsim/{relative}",
        "purpose": purpose,
        "source_role": role,
        "language": language,
        "line_count": source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0),
    }
    if language not in SOURCE_LANGUAGES:
        return {**common, "chunking_checked": False, "reason": "configuration/input file; no source-code check requested"}
    tree = get_parser(language).parse(source)
    raw_chunks = [asdict(chunk) for chunk in chunk_file(str(source_path), language=language)]
    sizes = [chunk["byte_end"] - chunk["byte_start"] for chunk in raw_chunks]
    return {
        **common,
        "chunking_checked": True,
        "parse_success": not tree.root_node.has_error,
        "raw_chunk_count": len(raw_chunks),
        "main_node_types": dict(Counter(chunk["node_type"] for chunk in raw_chunks).most_common()),
        "functions_classes_intact": integrity(tree, raw_chunks, language),
        "chunk_size_bytes": {
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
            "mean": fmean(sizes) if sizes else 0,
            "median": median(sizes) if sizes else 0,
            "tiny_under_100_bytes": sum(size < TINY_BYTES for size in sizes),
            "oversized_over_5000_bytes": sum(size > OVERSIZED_BYTES for size in sizes),
        },
    }


def summary_markdown(inventory_data: dict[str, Any], results: list[dict[str, Any]]) -> str:
    checked = [result for result in results if result["chunking_checked"]]
    failed = [result["source_path"] for result in checked if not result["parse_success"]]
    lines = [
        "# SPHinXsim-specific Tree-sitter Chunking Validation",
        "",
        "Scope is limited to the declared SPHinXsim-specific directories. `sphinxsim/sphinxsys/` was excluded and not read by this validation.",
        "",
        f"Inventory: {inventory_data['total_files']} files across {len(inventory_data['scope_roots'])} requested directories.",
        "",
        "## Representative compatibility checks",
        "",
        "| File | Language | Parse | Raw chunks | Functions intact | Classes intact | Tiny <100 B | >5 KB |",
        "| --- | --- | --- | ---: | --- | --- | ---: | ---: |",
    ]
    for item in results:
        if not item["chunking_checked"]:
            lines.append(f"| `{item['source_path']}` | {item['language']} | not checked (input/config) | — | — | — | — | — |")
            continue
        intact = item["functions_classes_intact"]
        sizes = item["chunk_size_bytes"]
        lines.append(
            f"| `{item['source_path']}` | {item['language']} | {'success' if item['parse_success'] else 'failure'} | "
            f"{item['raw_chunk_count']} | {intact['functions']['all_intact']} | {intact['classes']['all_intact']} | "
            f"{sizes['tiny_under_100_bytes']} | {sizes['oversized_over_5000_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## Tentative Prototype V1 policy",
            "",
            "- **simulator_library**: use language-specific Tree-sitter structural parsing; retrieve function/method units, retaining enclosing class/struct context as metadata.",
            "- **config/schema**: treat JSON input/config files as atomic configuration documents for this prototype; apply structural Python chunks to schema code.",
            "- **simulation_example**: use Python function/class structural chunks; preserve module and class context.",
            "- **test**: use Python/C++ function or test-case-level structural chunks.",
            "- **llm_support** and **visualization**: use Python function/class structural chunks with class/module context metadata.",
            "",
            "This is a compatibility-based demo policy only; no full SPHinXsim production chunks were generated.",
            "",
            f"Parse failures in this representative set: {len(failed)}" + (f" (`{', '.join(failed)}`)" if failed else "."),
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    inventory_data = inventory()
    representatives = [
        {"source_path": f"repos/SPHinXsim/{path}", "purpose": purpose, "language": language, "source_role": role}
        for path, purpose, language, role in REPRESENTATIVES
    ]
    results = [check_file(*representative) for representative in REPRESENTATIVES]
    write_json(OUTPUT_ROOT / "inventory_summary.json", inventory_data)
    write_json(OUTPUT_ROOT / "representative_files.json", representatives)
    write_json(OUTPUT_ROOT / "chunking_results.json", results)
    (OUTPUT_ROOT / "summary.md").write_text(summary_markdown(inventory_data, results), encoding="utf-8")
    print(f"Validated {len(results)} representative files; {sum(item['chunking_checked'] for item in results)} source-code files checked.")


if __name__ == "__main__":
    main()
