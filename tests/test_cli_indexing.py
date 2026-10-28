"""Focused CLI-only production generation and retrieval checks; never execute upstream CLI.

Run with tools/treesitter-chunker/.venv/bin/python -m unittest discover -s tests
-p test_cli_indexing.py -v. Generated output stays outside the project.
"""
import ast
import json
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

# Expectations are fixed before retrieval, from source inspection. Relevance
# failures are diagnostic findings, not extraction/integrity test failures.
RETRIEVAL_CASES = [
    ("How does SPHinXsim run a simulation through its CLI?", ["cmd_run"]),
    ("How does the CLI validate simulation configuration?", ["cmd_validate"]),
    ("Which function handles CLI configuration generation?", ["cmd_generate"]),
    ("How does the CLI expose or initiate interactive simulation functionality?", ["cmd_shell"]),
    ("How are CLI arguments and subcommands constructed?", ["_build_parser"]),
]


def generate_cli_report(output):
    """Run real production transforms and validate every record against source."""
    from src import chunk_generation_v1 as generator
    root = PROJECT / "repos" / "SPHinXsim"
    cli = root / "sphinxsim" / "cli.py"
    source = cli.read_bytes()
    tree = ast.parse(source)
    expected = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    assert not generator.get_parser("python").parse(source).root_node.has_error
    corpus, summary_path = output / "cli.jsonl", output / "summary.json"
    with patch.object(generator, "_sphinxsim_files", return_value=iter([cli])) as discovery:
        summary = generator.generate_sphinxsim(PROJECT, corpus, summary_path)
        discovery.assert_called_once_with(root)
    assert not summary["skipped_files"], summary
    assert not summary["parse_warning_files"], summary
    records = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    assert records and len(records) == summary["total_chunk_count"]
    assert len({r["chunk_id"] for r in records}) == len(records)
    symbols = {r["symbol"] for r in records}
    assert expected <= symbols, expected - symbols
    # Tree-sitter points are zero-based; production stores inclusive line numbers.
    for record in records:
        assert record["repository"] == "SPHinXsim"
        assert record["source_role"] == "cli"
        assert record["source_path"] == "repos/SPHinXsim/sphinxsim/cli.py"
        start, end = record["byte_start"], record["byte_end"]
        assert 0 <= start < end <= len(source)
        assert record["content"].encode("utf-8") == source[start:end], record["symbol"]
        assert record["start_line"] == source[:start].count(b"\n") + 1
        assert record["end_line"] == source[:end].count(b"\n") + 1
    sizes = [len(r["content"].encode("utf-8")) for r in records]
    largest = sorted((dict(symbol=r["symbol"], bytes=size, start_line=r["start_line"], end_line=r["end_line"])
                      for r, size in zip(records, sizes)), key=lambda r: -r["bytes"])
    return {"chunk_count": len(records), "symbols": [r["symbol"] for r in records],
            "missing_top_level_symbols": sorted(expected - symbols), "duplicate_ids": 0,
            "parser_warnings": summary["parse_warning_files"], "extraction_errors": summary["skipped_files"],
            "metadata_and_source_integrity": "PASS", "min_bytes": min(sizes), "max_bytes": max(sizes),
            "mean_bytes": statistics.mean(sizes), "median_bytes": statistics.median(sizes),
            "oversized": [r for r in largest if r["bytes"] > 5000], "largest_five": largest[:5]}


def retrieval_report(corpus):
    from src.retrieval_v1 import RetrievalV1
    retrieval = RetrievalV1(corpus)
    available = {r["symbol"] for r in retrieval.records}
    results = []
    for query, expected in RETRIEVAL_CASES:
        assert set(expected) <= available, expected
        top = [r.record["symbol"] for r in retrieval.search(query, top_k=5)]
        assert top == [r.record["symbol"] for r in retrieval.search(query, top_k=5)]
        ranks = {name: top.index(name) + 1 if name in top else None for name in expected}
        results.append({"query": query, "expected": expected, "top_5": top, "ranks": ranks,
                        "relevance": "PASS" if all(ranks.values()) else "FAIL",
                        "blocking": False})
    return results


class CliSourceRoleTests(unittest.TestCase):
    def test_exact_cli_role_and_existing_role_boundaries(self):
        # Dependency-free extraction of real role code, not a reimplementation.
        tree = ast.parse((PROJECT / "src/chunk_generation_v1.py").read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sphinxsim_source_role"]
        scope = {"Path": Path}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "role-test", "exec"), scope)
        role = scope["_sphinxsim_source_role"]
        root = Path("/synthetic/SPHinXsim")
        for relative, expected in [("sphinxsim/cli.py", "cli"), ("tests/cli.py", "test"),
                                   ("sphinxsim/cli/helper.py", "test"), ("sphinxsim/other.py", "test"),
                                   ("sphinxsim/config/a.json", "config_or_schema"),
                                   ("sphinxsim/bindings/a.cpp", "binding"),
                                   ("sphinxsim/sph_simulation/a.py", "simulator_library")]:
            self.assertEqual(role(root, root / relative), expected)


class CliProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from src import chunk_generation_v1
        except ModuleNotFoundError as error:
            raise unittest.SkipTest(f"NOT VERIFIED: missing production dependency {error.name}")
        if not (PROJECT / "repos/SPHinXsim/sphinxsim/cli.py").is_file():
            raise unittest.SkipTest("NOT VERIFIED: upstream CLI source unavailable")

    def test_cli_only_production_extraction_integrity_and_retrieval(self):
        with tempfile.TemporaryDirectory(prefix="sphinx-cli-test-") as directory:
            output = Path(directory)
            report = generate_cli_report(output)
            self.assertEqual(report["metadata_and_source_integrity"], "PASS")
            results = retrieval_report(output / "cli.jsonl")
            self.assertEqual(len(results), len(RETRIEVAL_CASES))
            print(json.dumps({"chunking": report, "retrieval": results}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    unittest.main()
