"""Focused CLI-path tests for the deterministic retrieval validation report."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts/retrieve_v1.py"


def load_retrieve_cli():
    spec = importlib.util.spec_from_file_location("retrieve_v1_cli_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRetriever:
    def __init__(self, corpus_path: Path):
        self.corpus_path = corpus_path

    def search(self, query: str, top_k: int) -> list[object]:
        return []


class RetrievalValidationPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_retrieve_cli()

    def test_default_validation_report_path_is_unchanged(self):
        arguments = self.cli.build_parser().parse_args(["--write-initial-validation"])

        self.assertEqual(
            arguments.validation_report,
            PROJECT_ROOT / "data/evaluation/retrieval_v1_initial.json",
        )

    def test_custom_validation_report_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            report_path = Path(temporary_directory) / "retrieval_validation.json"
            arguments = self.cli.build_parser().parse_args(
                ["--validation-report", str(report_path)]
            )

            self.assertEqual(arguments.validation_report, report_path)

    def test_in_project_corpus_retains_relative_report_metadata(self):
        corpus_path = PROJECT_ROOT / "data/chunks/source_chunks_v1_combined.jsonl"
        with tempfile.TemporaryDirectory() as temporary_directory:
            report_path = Path(temporary_directory) / "retrieval_validation.json"

            self.cli.write_initial_validation(FakeRetriever(corpus_path), report_path, 5)

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["corpus"], "data/chunks/source_chunks_v1_combined.jsonl")

    def test_external_corpus_uses_absolute_report_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "source_chunks_candidate.jsonl"
            report_path = temporary_path / "retrieval_validation.json"

            self.cli.write_initial_validation(FakeRetriever(corpus_path), report_path, 5)

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["corpus"], str(corpus_path))

    def test_custom_corpus_and_report_paths_are_passed_to_validation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            corpus_path = temporary_path / "source_chunks_candidate.jsonl"
            report_path = temporary_path / "retrieval_validation.json"

            with (
                patch.object(self.cli, "RetrievalV1") as retriever_class,
                patch.object(self.cli, "write_initial_validation") as write_report,
                patch.object(
                    sys,
                    "argv",
                    [
                        str(SCRIPT_PATH),
                        "--corpus",
                        str(corpus_path),
                        "--validation-report",
                        str(report_path),
                        "--write-initial-validation",
                    ],
                ),
            ):
                self.cli.main()

            retriever_class.assert_called_once_with(corpus_path)
            write_report.assert_called_once_with(retriever_class.return_value, report_path, 5)
            self.assertFalse(report_path.exists())


if __name__ == "__main__":
    unittest.main()
