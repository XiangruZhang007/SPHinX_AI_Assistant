#!/usr/bin/env python3
"""Reproduce the frozen Step 1.6 evaluation without changing the Retriever."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from src.retrieval_evaluation_io import (canonical_sha256, compare_results, file_sha256,
                                          validate_inputs)
from src.retrieval_evaluation_v2 import evaluate
from src.retrieval_evaluation_reports import aggregate_and_diagnose


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--documentation-corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path,
                        help="Defaults to step_1_6_frozen_v2_manifest.json beside the benchmark")
    return parser


def write_json(path, value, *, sort_keys=False):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=sort_keys)
        handle.write("\n")


def main(argv=None):
    args = build_parser().parse_args(argv)
    manifest_path = args.manifest or args.benchmark.with_name("step_1_6_frozen_v2_manifest.json")
    try:
        validated = validate_inputs(args.benchmark, args.corpus, args.documentation_corpus,
                                    args.output_dir, manifest_path, PROJECT_ROOT)
        if os.environ.get("PYTHONHASHSEED") != "0":
            raise ValueError("Exact reproduction requires PYTHONHASHSEED=0 before starting Python")
        spec = validated["specification"]
        # Reject races as well as pre-existing output; all checks precede publication.
        args.output_dir.mkdir(parents=True, exist_ok=False)
        write_json(args.output_dir / "effective-inputs.json", {
            "benchmark": str(args.benchmark.resolve()), "benchmark_sha256": file_sha256(args.benchmark),
            "corpus": str(args.corpus.resolve()), "corpus_sha256": file_sha256(args.corpus),
            "documentation_corpus": str(args.documentation_corpus.resolve()),
            "documentation_sha256": file_sha256(args.documentation_corpus),
            "manifest": str(manifest_path.resolve()), "manifest_sha256": file_sha256(manifest_path),
            "configuration_sha256": canonical_sha256(spec["config"]),
            "runtime_paths_override_historical_hints": True,
            "python": sys.executable, "python_version": sys.version,
            "python_hash_seed": os.environ["PYTHONHASHSEED"], "config": spec["config"],
            "argv": sys.argv if argv is None else argv,
            "installed_tree_sitter_packages_not_used_for_ranking": {
                d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
                if "tree" in d.metadata["Name"].lower() or "chunk" in d.metadata["Name"].lower()},
        })
        rows = evaluate(spec, args.corpus, args.documentation_corpus,
                        progress=lambda row: print(json.dumps(row, sort_keys=True), flush=True))
        write_json(args.output_dir / "results.json", rows, sort_keys=True)
        comparisons = compare_results(rows, validated["reference_results"], spec)
        write_json(args.output_dir / "per-combination-comparison.json", comparisons)
        # Historical finish_evaluation.py reads the sorted-key raw result file.
        # Preserve that JSON key order in nested diagnostic records as well.
        archived_order_rows = json.loads((args.output_dir / "results.json").read_text(encoding="utf-8"))
        artifacts = aggregate_and_diagnose(spec, archived_order_rows, validated["combined_records"],
                                          validated["historical_documentation_results"])
        artifact_checks = {}
        for name, value in artifacts.items():
            write_json(args.output_dir / name, value)
            expected = validated["reference_artifact_checksums"][name]
            artifact_checks[name] = {
                "canonical_equal": canonical_sha256(value) == expected["canonical_sha256"],
                "serialized_bytes_equal": file_sha256(args.output_dir / name) == expected["file_sha256"],
                "historical_sha256": expected["file_sha256"],
                "reproduced_sha256": file_sha256(args.output_dir / name),
            }
        write_json(args.output_dir / "aggregate-and-diagnostic-comparison.json", artifact_checks)
        exact = all(c["exact_match"] for c in comparisons) and all(
            c["canonical_equal"] and c["serialized_bytes_equal"] for c in artifact_checks.values())
        result_hash = file_sha256(args.output_dir / "results.json")
        exact_bytes = result_hash == validated["manifest"]["historical_results_sha256"]
        summary = {"status": "EXACT_REPRODUCTION" if exact and exact_bytes else "NONEXACT_REPRODUCTION",
                   "combinations": len(rows), "exact_combination_matches": sum(c["exact_match"] for c in comparisons),
                   "results_sha256": result_hash, "historical_results_bytes_equal": exact_bytes,
                   "aggregate_and_diagnostics_exact": exact, "score_tolerance_used": False,
                   "candidate_unchanged": file_sha256(args.corpus) == spec["candidate_sha256"]}
        write_json(args.output_dir / "reproduction-summary.json", summary)
        report = ["# Frozen Step 1.6 reproduction", "", json.dumps(summary, indent=2), "",
                  "Strict historical criteria and source availability were not changed.",
                  "Semantic anchors, historical limitations and post-run alternative interpretations remain separate.",
                  "P1 diagnostics are excluded from ordinary scored denominators.", "",
                  "| Mode/group | Hit@5 | Hit@10 |", "|---|---|---|"]
        for group, metric in artifacts["metrics.json"]["group"].items():
            report.append(f"| {group} | {metric['strict_hits_at_5']}/{metric['n']} | {metric['strict_hits_at_10']}/{metric['n']} |")
        report.extend(["", "Detailed comparison: per-combination-comparison.json.",
                       "All original metric, failure, alternative, documentation, P1 and oracle-limitation reports are reproduced as separate JSON artifacts."])
        with (args.output_dir / "report.md").open("x", encoding="utf-8") as handle:
            handle.write("\n".join(report) + "\n")
        checksums = [{"path": p.name, "sha256": file_sha256(p)} for p in sorted(args.output_dir.iterdir()) if p.is_file()]
        write_json(args.output_dir / "checksums.json", checksums)
        print(json.dumps(summary, sort_keys=True), flush=True)
        return 0 if exact and exact_bytes else 1
    except (OSError, ValueError, KeyError, AssertionError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
