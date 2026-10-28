"""Focused preservation, preflight, scoring and exact-comparison checks."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.retrieval_evaluation_criteria import matches, outcome, union_covers
from src.retrieval_evaluation_io import (canonical_sha256, check_checksum, compare_results,
                                          expected_inventory, validate_inputs, validate_structure)
from src.retrieval_evaluation_reports import aggregate_metrics
from src.retrieval_evaluation_v2 import evaluate

PROJECT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT / "tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json"
MANIFEST = BENCHMARK.with_name("step_1_6_frozen_v2_manifest.json")


class FrozenBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(BENCHMARK.read_text())

    def test_complete_identity_and_inventory(self):
        validate_structure(self.spec)
        self.assertEqual(len(self.spec["questions"]), 88)
        self.assertEqual(len(expected_inventory(self.spec)), 128)

    def test_duplicate_id_rejected(self):
        self.spec["questions"][1]["id"] = self.spec["questions"][0]["id"]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_structure(self.spec)

    def test_schema_and_benchmark_versions_are_distinct(self):
        for key, value in [("schema_version", True), ("schema_version", 2),
                           ("benchmark_id", "step_1_6_frozen_evaluation_v1")]:
            spec = deepcopy(self.spec)
            spec[key] = value
            with self.assertRaises(ValueError):
                validate_structure(spec)

    def test_diagnostic_cannot_enter_primary_denominator(self):
        next(q for q in self.spec["questions"] if q["group"] == "D")["primary_score_eligible"] = True
        with self.assertRaisesRegex(ValueError, "diagnostic"):
            validate_structure(self.spec)

    def test_strict_historical_range_cannot_be_relaxed(self):
        next(q for q in self.spec["questions"] if q["id"] == "DOC-Q38")["requirements"][0]["range"][1] = 83
        with self.assertRaisesRegex(ValueError, "Historical interval"):
            validate_structure(self.spec)

    def test_invalid_repository_reference_rejected(self):
        self.spec["questions"][0]["requirements"][0]["path"] = "repos/SPHinXsys/../private"
        with self.assertRaisesRegex(ValueError, "evidence path"):
            validate_structure(self.spec)

    def test_config_change_rejected(self):
        self.spec["config"]["b"] = .8
        with self.assertRaisesRegex(ValueError, "configuration"):
            validate_structure(self.spec)

    def test_historical_runtime_hints_are_not_mandatory_locations(self):
        self.spec["candidate"] = "/not/a/runtime/location"
        self.spec["documentation_component"] = "/also/not/a/runtime/location"
        validate_structure(self.spec)


class EvaluationPreflightTests(unittest.TestCase):
    def test_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(FileNotFoundError, "Required input"):
                validate_inputs(BENCHMARK, root / "missing.jsonl", root / "docs.jsonl",
                                root / "output", MANIFEST, PROJECT)
            self.assertFalse((root / "output").exists())

    def test_invalid_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                check_checksum(path, "0" * 64)

    def test_existing_output_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                validate_inputs(root / "no", root / "no", root / "no", root, root / "no", PROJECT)
            self.assertEqual(list(root.iterdir()), [])

    def test_cli_independent_arguments(self):
        script = PROJECT / "scripts/evaluate_frozen_step_1_6.py"
        spec = importlib.util.spec_from_file_location("frozen_evaluation_cli", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = module.build_parser().parse_args([
            "--benchmark", "a.json", "--corpus", "b.jsonl",
            "--documentation-corpus", "c.jsonl", "--output-dir", "results"])
        self.assertEqual((args.benchmark, args.corpus, args.documentation_corpus, args.output_dir),
                         tuple(map(Path, ["a.json", "b.jsonl", "c.jsonl", "results"])))

    def test_worker_uses_runtime_paths_not_historical_hints(self):
        frozen = json.loads(BENCHMARK.read_text())
        frozen["questions"] = [frozen["questions"][1]]
        frozen["candidate"] = "/nonexistent/old-corpus"
        frozen["documentation_component"] = "/nonexistent/old-docs"

        class FakeRetriever:
            def __init__(self, path, **kwargs):
                self.records = [None] * (354 if path == Path("portable-docs") else 11338)

            def search(self, query, top_k):
                return []

        with patch("src.retrieval_evaluation_v2.RetrievalV1", side_effect=FakeRetriever) as mock:
            result = evaluate(frozen, Path("portable-combined"), Path("portable-docs"))
        self.assertEqual([c.args[0] for c in mock.call_args_list],
                         [Path("portable-docs"), Path("portable-combined")])
        self.assertEqual(len(result), 2)


class ScoringPreservationTests(unittest.TestCase):
    def setUp(self):
        self.record = {"source_path": "repos/SPHinXsys/a.md", "source_role": "documentation",
                       "symbol": "Generate", "section_title": "Generate", "content": "generate",
                       "start_line": 1, "end_line": 2}
        self.generate = {"path": self.record["source_path"], "kind": "section_title", "value": "Generate"}
        self.validate = {**self.generate, "value": "Validate"}

    def test_single_evidence_and_mrr(self):
        result = outcome([self.record], [self.generate])
        self.assertTrue(result["hit_at_5"])
        self.assertEqual(result["reciprocal_rank_at_5_single_only"], 1)

    def test_multi_all_of_and_partial_coverage(self):
        result = outcome([self.record], [self.generate, self.validate])
        self.assertFalse(result["hit_at_5"])
        self.assertEqual(result["partial_coverage_at_5"], .5)
        self.assertIsNone(result["reciprocal_rank_at_5_single_only"])

    def test_complete_at_ten_does_not_become_complete_at_five(self):
        records = [self.record] + [{**self.record, "section_title": "Other"}] * 4
        records.append({**self.record, "section_title": "Validate"})
        result = outcome(records, [self.generate, self.validate])
        self.assertFalse(result["hit_at_5"])
        self.assertTrue(result["hit_at_10"])
        self.assertEqual(result["evidence_ranks"], [1, 6])

    def test_union_never_changes_single_chunk_requirement(self):
        records = [self.record, {**self.record, "start_line": 3, "end_line": 4}]
        criterion = {"path": self.record["source_path"], "range": [1, 4]}
        self.assertTrue(union_covers(records, self.record["source_path"], 1, 4))
        self.assertFalse(any(matches(r, criterion) for r in records))

    def test_diagnostics_excluded_from_aggregate_denominator(self):
        row = {"mode": "combined", "group": "A", "category": "documentation",
               "multi_evidence": False, "evidence_available": True,
               "strict": outcome([self.record], [self.generate]), "failure_type": "NONE"}
        gap = {**row, "group": "D", "category": "p1", "evidence_available": False,
               "strict": outcome([], [self.generate]), "failure_type": "COVERAGE_GAP"}
        metrics = aggregate_metrics([row, gap])
        self.assertEqual(metrics["combined_primary_all"]["n"], 1)
        self.assertEqual(metrics["combined_primary_all"]["strict_hits_at_5"], 1)
        self.assertNotIn("combined:D", metrics["group"])


class ExactComparisonTests(unittest.TestCase):
    def fixture(self):
        spec = json.loads(BENCHMARK.read_text())
        rows = []
        refs = []
        for mode, identifier in sorted(expected_inventory(spec)):
            row = {"mode": mode, "id": identifier, "group": identifier.split("-")[0],
                   "strict": {}, "semantic_anchor": {}, "top_5_ids": ["id"], "top_10_ids": ["id"],
                   "top_10": [{"score": 1.0, "score_hex": float(1).hex()}]}
            rows.append(row)
            refs.append({**deepcopy(row), "full_row_sha256": canonical_sha256(row)})
        return spec, rows, refs

    def test_all_128_combinations_exact(self):
        spec, rows, refs = self.fixture()
        self.assertTrue(all(x["exact_match"] for x in compare_results(rows, refs, spec)))

    def test_tiny_score_difference_is_not_hidden_by_tolerance(self):
        spec, rows, refs = self.fixture()
        rows[0]["top_10"][0]["score"] = 1.0000000000000002
        rows[0]["top_10"][0]["score_hex"] = float(1.0000000000000002).hex()
        result = compare_results(rows, refs, spec)
        self.assertFalse(result[0]["exact_match"])
        self.assertTrue(result[0]["score_differences"])
        self.assertFalse(result[0]["tolerance_used"])

    def test_missing_combination_rejected(self):
        spec, rows, refs = self.fixture()
        with self.assertRaisesRegex(ValueError, "128"):
            compare_results(rows[:-1], refs, spec)
