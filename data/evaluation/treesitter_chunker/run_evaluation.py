"""One-off, read-only treesitter-chunker evaluation serializer.

This is evaluation-only material. It does not alter the input repositories or
perform any production indexing, retrieval, or pipeline integration.
"""

from dataclasses import asdict
import json
from pathlib import Path

from chunker import chunk_file
from tree_sitter_language_pack import get_parser


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = Path(__file__).resolve().parent
INPUTS = [
    PROJECT_ROOT / "repos/SPHinXsys/src/shared/common/scalar_functions.cpp",
    PROJECT_ROOT / "repos/SPHinXsys/src/shared/bodies/base_body.h",
    PROJECT_ROOT / "repos/SPHinXsys/src/shared/common/sphinxsys_variable.h",
    PROJECT_ROOT / "repos/SPHinXsys/tests/2d_examples/test_2d_dambreak/Dambreak.cpp",
    PROJECT_ROOT
    / "repos/SPHinXsys/tests/unit_tests_src/shared/common/test_scalar_functions/test_scalar_functions.cpp",
]


def write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    parser = get_parser("cpp")
    aggregate: list[dict[str, object]] = []

    for source_path in INPUTS:
        relative_path = source_path.relative_to(PROJECT_ROOT).as_posix()
        source_text = source_path.read_text(encoding="utf-8")
        source_bytes = source_text.encode("utf-8")
        tree = parser.parse(source_bytes)
        chunks = chunk_file(str(source_path), language="cpp")

        raw_chunks = []
        for index, chunk in enumerate(chunks):
            record = asdict(chunk)
            record["chunk_index"] = index
            raw_chunks.append(record)

        byte_sizes = [chunk.byte_end - chunk.byte_start for chunk in chunks]
        summary = {
            "file": relative_path,
            "total_lines": len(source_text.splitlines()),
            "total_chunks": len(chunks),
            "chunk_node_types": sorted({chunk.node_type for chunk in chunks}),
            "minimum_chunk_size_bytes": min(byte_sizes) if byte_sizes else 0,
            "maximum_chunk_size_bytes": max(byte_sizes) if byte_sizes else 0,
            "average_chunk_size_bytes": (
                sum(byte_sizes) / len(byte_sizes) if byte_sizes else 0
            ),
            "parsing_errors_reported": tree.root_node.has_error,
        }
        output_name = source_path.name + ".json"
        write_json(
            OUTPUT_ROOT / "raw" / output_name,
            {"file": relative_path, "language": "cpp", "chunks": raw_chunks},
        )
        write_json(OUTPUT_ROOT / "summaries" / output_name, summary)
        aggregate.append(summary)

    write_json(OUTPUT_ROOT / "summary.json", aggregate)


if __name__ == "__main__":
    main()
