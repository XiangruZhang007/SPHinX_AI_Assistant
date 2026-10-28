#!/usr/bin/env python3
"""Generate SPHinXsim-specific V1 chunks and the combined Sys + Sim demo corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.chunk_generation_v1 import generate_combined_corpus, generate_sphinxsim  # noqa: E402
from src.chunk_identity import IDENTITY_SCHEMES, validate_identity_scheme  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    chunks = PROJECT_ROOT / "data/chunks"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-scheme", choices=IDENTITY_SCHEMES, default="legacy",
                        help="Source identity mode; must match the supplied SPHinXsys component")
    parser.add_argument(
        "--sys-input",
        type=Path,
        default=chunks / "source_chunks_v1.jsonl",
        help="Existing SPHinXsys JSONL input path.",
    )
    parser.add_argument(
        "--sys-summary-input",
        type=Path,
        default=chunks / "source_chunks_v1_summary.json",
        help="Existing SPHinXsys summary input path.",
    )
    parser.add_argument(
        "--sim-output",
        type=Path,
        default=chunks / "sphinxsim_source_chunks_v1.jsonl",
        help="SPHinXsim JSONL output path.",
    )
    parser.add_argument(
        "--sim-summary-output",
        type=Path,
        default=chunks / "sphinxsim_source_chunks_v1_summary.json",
        help="SPHinXsim summary output path.",
    )
    parser.add_argument(
        "--combined-output",
        type=Path,
        default=chunks / "source_chunks_v1_combined.jsonl",
        help="Combined JSONL output path.",
    )
    parser.add_argument(
        "--combined-summary-output",
        type=Path,
        default=chunks / "source_chunks_v1_combined_summary.json",
        help="Combined summary output path.",
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.identity_scheme != "legacy":
        sys_summary = json.loads(arguments.sys_summary_input.read_text(encoding="utf-8"))
        if validate_identity_scheme(sys_summary.get("identity_scheme", "legacy")) != arguments.identity_scheme:
            raise ValueError("SPHinXsys input must use the selected source identity scheme")
    options = {} if arguments.identity_scheme == "legacy" else {"identity_scheme": arguments.identity_scheme}
    summary = generate_sphinxsim(PROJECT_ROOT, arguments.sim_output, arguments.sim_summary_output, **options)
    combined = generate_combined_corpus(
        arguments.sys_input,
        arguments.sys_summary_input,
        arguments.sim_output,
        arguments.sim_summary_output,
        arguments.combined_output,
        arguments.combined_summary_output,
    )
    print(f"Generated {summary['total_chunk_count']} SPHinXsim chunks")
    print(f"Generated {combined['total_chunk_count']} combined chunks")


if __name__ == "__main__":
    main()
