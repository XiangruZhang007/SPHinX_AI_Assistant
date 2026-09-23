#!/usr/bin/env python3
"""Search the Prototype V1 combined corpus with deterministic BM25 retrieval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.retrieval_v1 import RetrievalV1, content_preview  # noqa: E402


INITIAL_QUERIES = [
    "How is the 2D dambreak case initialized?",
    "What does WeaklyCompressibleFluid do?",
    "Find a heat transfer simulation case.",
    "How is a 2D simulation configured in SPHinXsim?",
    "What information is defined by the SPHinXsim schema?",
    "Which builder constructs simulations in SPHinXsim?",
]


def print_results(query: str, results, debug: bool) -> None:
    print(f"Query: {query}")
    for rank, result in enumerate(results, start=1):
        item = result.as_dict(include_debug=debug)
        print(f"\n#{rank} score={item['score']:.6f}")
        print(f"  repository: {item['repository']}")
        print(f"  source_role: {item['source_role']}")
        print(f"  source_path: {item['source_path']}")
        print(f"  chunk_type: {item['chunk_type']}")
        print(f"  symbol/title: {item['symbol_or_section_title']}")
        print(f"  lines: {item['start_line']}-{item['end_line']}")
        print(f"  preview: {content_preview(result.record['content'])}")
        if debug:
            print(f"  debug: {json.dumps(item['score_components'], sort_keys=True)}")


def write_initial_validation(retriever: RetrievalV1, path: Path, top_k: int) -> None:
    payload = {
        "prototype": "retrieval_v1_initial",
        "corpus": str(retriever.corpus_path.relative_to(PROJECT_ROOT)),
        "top_k": top_k,
        "queries": [
            {"query": query, "top_k_results": [result.as_dict() for result in retriever.search(query, top_k)]}
            for query in INITIAL_QUERIES
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validation report: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Natural-language repository question")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--debug", action="store_true", help="Print score components")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=PROJECT_ROOT / "data/chunks/source_chunks_v1_combined.jsonl",
    )
    parser.add_argument(
        "--write-initial-validation",
        action="store_true",
        help="Run the six initial checks and write data/evaluation/retrieval_v1_initial.json",
    )
    arguments = parser.parse_args()
    if not arguments.query and not arguments.write_initial_validation:
        parser.error("provide --query or --write-initial-validation")
    retriever = RetrievalV1(arguments.corpus)
    if arguments.query:
        print_results(arguments.query, retriever.search(arguments.query, arguments.top_k), arguments.debug)
    if arguments.write_initial_validation:
        write_initial_validation(
            retriever,
            PROJECT_ROOT / "data/evaluation/retrieval_v1_initial.json",
            arguments.top_k,
        )


if __name__ == "__main__":
    main()
