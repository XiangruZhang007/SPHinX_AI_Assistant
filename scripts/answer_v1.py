#!/usr/bin/env python3
"""Answer repository questions from Retrieval V1 evidence through an LLM endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.grounded_answer_v1 import DEFAULT_TOP_K, GroundedAnswerV1, source_preview  # noqa: E402


SMOKE_QUERIES = [
    "What does WeaklyCompressibleFluid do?",
    "How is the 2D dambreak case initialized?",
    "How is a 2D simulation configured in SPHinXsim?",
]


def print_result(result: dict, show_evidence: bool, grounded: GroundedAnswerV1, top_k: int) -> None:
    if result["success"]:
        print("Answer:")
        print(result["answer"])
    else:
        print(f"Error: {result['error']}")
    print("\nSources:")
    for rank, source in enumerate(result["sources"], start=1):
        print(
            f"{rank}. {source['repository']} | {source['source_role']} | {source['source_path']} | "
            f"lines {source['start_line']}-{source['end_line']} | {source['symbol_or_section_title']}"
        )
    if show_evidence:
        print("\nEvidence previews:")
        for rank, evidence in enumerate(grounded.retriever.search(result["query"], top_k), start=1):
            print(f"{rank}. {source_preview(evidence)}")


def write_smoke(grounded: GroundedAnswerV1, path: Path, top_k: int) -> list[dict]:
    results = [grounded.answer(query, top_k) for query in SMOKE_QUERIES]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"prototype": "grounded_answer_v1_smoke", "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Smoke report: {path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Natural-language repository question")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--show-evidence", action="store_true")
    parser.add_argument("--write-smoke", action="store_true", help="Run the three grounded-answer smoke queries.")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=PROJECT_ROOT / "data/chunks/source_chunks_v1_combined.jsonl",
    )
    arguments = parser.parse_args()
    if not arguments.query and not arguments.write_smoke:
        parser.error("provide --query or --write-smoke")
    grounded = GroundedAnswerV1(arguments.corpus)
    if arguments.query:
        print_result(grounded.answer(arguments.query, arguments.top_k), arguments.show_evidence, grounded, arguments.top_k)
    if arguments.write_smoke:
        for result in write_smoke(grounded, PROJECT_ROOT / "data/evaluation/grounded_answer_v1_smoke.json", arguments.top_k):
            print(f"{result['query']}: {'success' if result['success'] else result['error']}")


if __name__ == "__main__":
    main()
