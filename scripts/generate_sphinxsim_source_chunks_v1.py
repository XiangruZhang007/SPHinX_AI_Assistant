#!/usr/bin/env python3
"""Generate SPHinXsim-specific V1 chunks and the combined Sys + Sim demo corpus."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.chunk_generation_v1 import generate_combined_corpus, generate_sphinxsim  # noqa: E402


def main() -> None:
    chunks = PROJECT_ROOT / "data/chunks"
    sim_jsonl = chunks / "sphinxsim_source_chunks_v1.jsonl"
    sim_summary = chunks / "sphinxsim_source_chunks_v1_summary.json"
    summary = generate_sphinxsim(PROJECT_ROOT, sim_jsonl, sim_summary)
    combined = generate_combined_corpus(
        chunks / "source_chunks_v1.jsonl",
        chunks / "source_chunks_v1_summary.json",
        sim_jsonl,
        sim_summary,
        chunks / "source_chunks_v1_combined.jsonl",
        chunks / "source_chunks_v1_combined_summary.json",
    )
    print(f"Generated {summary['total_chunk_count']} SPHinXsim chunks")
    print(f"Generated {combined['total_chunk_count']} combined chunks")


if __name__ == "__main__":
    main()
