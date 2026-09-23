#!/usr/bin/env python3
"""Generate the minimal Prototype V1 retrieval-ready SPHinXsys JSONL output."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.chunk_generation_v1 import generate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/chunks/source_chunks_v1.jsonl",
        help="JSONL output path (default: data/chunks/source_chunks_v1.jsonl)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=PROJECT_ROOT / "data/chunks/source_chunks_v1_summary.json",
        help="Summary JSON path (default: data/chunks/source_chunks_v1_summary.json)",
    )
    arguments = parser.parse_args()
    summary = generate(PROJECT_ROOT, arguments.output, arguments.summary)
    print(f"Generated {summary['total_chunk_count']} chunks")
    print(f"JSONL: {arguments.output}")
    print(f"Summary: {arguments.summary}")


if __name__ == "__main__":
    main()
