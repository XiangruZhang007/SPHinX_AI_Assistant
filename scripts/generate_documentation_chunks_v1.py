#!/usr/bin/env python3
"""Generate a separate documentation-only corpus; never overwrite existing files."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.documentation_chunking import DEFAULT_CONFIG, generate_documentation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New JSONL path; parent must exist")
    parser.add_argument("--summary", required=True, type=Path, help="Distinct new summary path; parent must exist")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        summary = generate_documentation(PROJECT_ROOT, args.output, args.summary, args.config)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Documentation generation failed: {error}\n")
    print(f"Generated {summary['total_chunk_count']} chunks from {summary['document_count']} documents; "
          f"warnings={summary['warning_count']}; integrity={summary['integrity']}")


if __name__ == "__main__":
    main()
