"""Focused, dependency-free tests for the SPHinXsim generation CLI paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "generate_sphinxsim_source_chunks_v1.py"


def load_cli_module() -> ModuleType:
    fake_src = ModuleType("src")
    fake_chunk_generation = ModuleType("src.chunk_generation_v1")
    fake_chunk_generation.generate_sphinxsim = lambda *_args: {"total_chunk_count": 0}
    fake_chunk_generation.generate_combined_corpus = lambda *_args: {"total_chunk_count": 0}
    spec = importlib.util.spec_from_file_location("generate_sphinxsim_source_chunks_v1_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"src": fake_src, "src.chunk_generation_v1": fake_chunk_generation},
    ):
        spec.loader.exec_module(module)
    return module


class GenerateSphinxsimCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = load_cli_module()
        self.chunks = self.cli.PROJECT_ROOT / "data/chunks"

    def test_default_paths_match_existing_production_paths(self) -> None:
        arguments = self.cli.build_parser().parse_args([])
        self.assertEqual(arguments.sys_input, self.chunks / "source_chunks_v1.jsonl")
        self.assertEqual(arguments.sys_summary_input, self.chunks / "source_chunks_v1_summary.json")
        self.assertEqual(arguments.sim_output, self.chunks / "sphinxsim_source_chunks_v1.jsonl")
        self.assertEqual(arguments.sim_summary_output, self.chunks / "sphinxsim_source_chunks_v1_summary.json")
        self.assertEqual(arguments.combined_output, self.chunks / "source_chunks_v1_combined.jsonl")
        self.assertEqual(
            arguments.combined_summary_output,
            self.chunks / "source_chunks_v1_combined_summary.json",
        )

    def test_custom_paths_are_passed_to_existing_generation_functions(self) -> None:
        candidate = Path("/tmp/candidate")
        sys_input = candidate / "sys.jsonl"
        sys_summary = candidate / "sys_summary.json"
        sim_output = candidate / "sim.jsonl"
        sim_summary = candidate / "sim_summary.json"
        combined_output = candidate / "combined.jsonl"
        combined_summary = candidate / "combined_summary.json"

        with (
            patch.object(
                self.cli,
                "generate_sphinxsim",
                return_value={"total_chunk_count": 12},
            ) as generate_sim,
            patch.object(
                self.cli,
                "generate_combined_corpus",
                return_value={"total_chunk_count": 34},
            ) as generate_combined,
            patch.object(
                sys,
                "argv",
                [
                    str(SCRIPT_PATH),
                    "--sys-input",
                    str(sys_input),
                    "--sys-summary-input",
                    str(sys_summary),
                    "--sim-output",
                    str(sim_output),
                    "--sim-summary-output",
                    str(sim_summary),
                    "--combined-output",
                    str(combined_output),
                    "--combined-summary-output",
                    str(combined_summary),
                ],
            ),
        ):
            self.cli.main()

        generate_sim.assert_called_once_with(self.cli.PROJECT_ROOT, sim_output, sim_summary)
        generate_combined.assert_called_once_with(
            sys_input,
            sys_summary,
            sim_output,
            sim_summary,
            combined_output,
            combined_summary,
        )


if __name__ == "__main__":
    unittest.main()
