"""Synthetic scope tests and full current-checkout V1 file-set parity.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_indexing_scope.py -v
Parity uses frozen test-only V1 logic and AST-isolated migrated wrappers.
AST isolation does not verify real generator imports or CLI dependencies.
"""
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess
import os
import tempfile
from typing import Iterable
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from indexing_scope import discover_files, is_in_scope, load_config, normalize_relative_path, validate_config


# Frozen pre-migration V1 reference; never used by production code.
LEGACY_SOURCE_EXTENSIONS = {'.c', '.cc', '.cpp', '.cxx', '.h', '.hh', '.hpp', '.hxx'}
LEGACY_SIM_SCOPE_ROOTS = ('sphinxsim/bindings', 'sphinxsim/config', 'sphinxsim/llm', 'sphinxsim/sph_simulation', 'sphinxsim/visualization', 'examples', 'tests')
LEGACY_SIM_SOURCE_LANGUAGES = {'.py': 'python', '.c': 'cpp', '.cc': 'cpp', '.cpp': 'cpp', '.cxx': 'cpp', '.h': 'cpp', '.hh': 'cpp', '.hpp': 'cpp', '.hxx': 'cpp'}

def legacy_source_files(repo_root: Path) -> Iterable[Path]:
    for directory in (repo_root / 'src', repo_root / 'tests'):
        yield from sorted((path for path in directory.rglob('*') if path.is_file() and path.suffix.lower() in LEGACY_SOURCE_EXTENSIONS))

def legacy_sphinxsim_files(repo_root: Path) -> Iterable[Path]:
    embedded_root = repo_root / 'sphinxsim' / 'sphinxsys'
    for scope_root in LEGACY_SIM_SCOPE_ROOTS:
        root = repo_root / scope_root
        for path in sorted((item for item in root.rglob('*') if item.is_file())):
            if path.is_relative_to(embedded_root):
                continue
            if path.suffix.lower() in LEGACY_SIM_SOURCE_LANGUAGES or path.suffix.lower() == '.json':
                yield path


class SyntheticScopeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_all_included_directories_and_extensions(self):
        for repository, rules in self.config["repositories"].items():
            for directory in rules["include_directories"]:
                for extension in rules["extensions"]:
                    with self.subTest(repository=repository, directory=directory, extension=extension):
                        self.assertTrue(is_in_scope(self.config, repository, directory + "/nested/file" + extension.upper()))

    def test_outside_scope_and_boundaries(self):
        for repository, path in [
            ("SPHinXsys", "src_extra/a.cpp"), ("SPHinXsys", "tests2/a.h"),
            ("SPHinXsys", "src/a.py"), ("SPHinXsys", "docs/a.md"),
            ("SPHinXsim", "sphinxsim/cli/main.py"), ("SPHinXsim", "docs/a.md"),
            ("SPHinXsim", "sphinxsim/bindings_extra/a.py"),
            ("SPHinXsim", "sphinxsim/sphinxsys/src/a.cpp"),
        ]:
            with self.subTest(path=path):
                self.assertFalse(is_in_scope(self.config, repository, path))

    def test_only_explicit_cli_file_added(self):
        self.assertTrue(is_in_scope(self.config, "SPHinXsim", "sphinxsim/cli.py"))
        for path in ["cli.py", "setup.py", "sphinxsim/__init__.py", "sphinxsim/other.py",
                     "sphinxsim/cli/main.py", "sphinxsim/CLI.py"]:
            self.assertFalse(is_in_scope(self.config, "SPHinXsim", path), path)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["examples/a.py", "sphinxsim/cli.py", "setup.py", "sphinxsim/other.py"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            self.assertEqual([p.relative_to(root).as_posix() for p in
                              discover_files(self.config, "SPHinXsim", root)],
                             ["examples/a.py", "sphinxsim/cli.py"])

    def test_invalid_repositories(self):
        for name in ["", "../SPHinXsys", "/SPHinXsys", "a/b", "a\\b", "a b", None, "unknown"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                is_in_scope(self.config, name, "src/a.cpp")

    def test_invalid_paths(self):
        for path in ["", "/src/a.cpp", "../src/a.cpp", "src/../a.cpp", "src/../../a.cpp", "C:/src/a.cpp", "src\\a.cpp", "src/\x00.cpp", ".", None]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_relative_path(path)
        self.assertEqual(normalize_relative_path("./src//nested/a.cpp"), "src/nested/a.cpp")

    def test_explicit_inclusions_and_exclusion_priority(self):
        rules = self.config["repositories"]["SPHinXsim"]
        rules["include_files"] = ["docs/guide.md", "sphinxsim/sphinxsys/a.py", "tests/private.py"]
        rules["exclude_files"] = ["tests/private.py"]
        self.assertTrue(is_in_scope(self.config, "SPHinXsim", "docs/guide.md"))
        self.assertFalse(is_in_scope(self.config, "SPHinXsim", "tests/private.py"))
        self.assertFalse(is_in_scope(self.config, "SPHinXsim", "sphinxsim/sphinxsys/a.py"))
        rules["include_directories"].append("sphinxsim")
        self.assertFalse(is_in_scope(self.config, "SPHinXsim", "sphinxsim/sphinxsys/nested/a.py"))
        self.assertTrue(is_in_scope(self.config, "SPHinXsim", "sphinxsim/sphinxsys_extra/a.py"))

    def test_config_validation(self):
        mutations = [
            lambda c: c.update(schema_version=True),
            lambda c: c.update(schema_version=2),
            lambda c: c.update(extra=[]),
            lambda c: c.update(repositories=[]),
            lambda c: c["repositories"].update({"../bad": {}}),
            lambda c: c["repositories"]["SPHinXsys"].update(extensions=["CPP"]),
            lambda c: c["repositories"]["SPHinXsys"].update(include_files=["../a"]),
            lambda c: c["repositories"]["SPHinXsys"].update(include_directories=["./src"]),
            lambda c: c["repositories"]["SPHinXsys"].update(exclude_files="file"),
            lambda c: c["repositories"]["SPHinXsys"].update(include_files=["a", "a"]),
        ]
        for mutate in mutations:
            config = deepcopy(self.config)
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_discovery_order_duplicates_and_explicit_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["src/z.CPP", "src/nested/a.h", "tests/b.cc", "src/no.py", "src_extra/no.cpp", "docs/guide.md"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            rules = self.config["repositories"]["SPHinXsys"]
            rules["include_directories"].append("src/nested")
            rules["include_files"] = ["docs/guide.md", "src/z.CPP", "missing.cpp"]
            expected = ["src/nested/a.h", "src/z.CPP", "tests/b.cc", "src/nested/a.h", "docs/guide.md"]
            for _ in range(2):
                actual = discover_files(self.config, "SPHinXsys", root)
                self.assertEqual([p.relative_to(root).as_posix() for p in actual], expected)

    def test_config_loading_from_other_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            code = ("import sys; sys.path.insert(0, " + repr(str(PROJECT / "src")) + "); "
                    "from indexing_scope import load_config; "
                    "assert load_config()['schema_version'] == 1")
            subprocess.run([sys.executable, "-B", "-c", code], cwd=directory, check=True,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_missing_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                discover_files(self.config, "SPHinXsys", Path(directory) / "missing")


class CompleteFileSetParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Extract only migrated wrappers, not a purported legacy baseline.
        source = PROJECT / "src" / "chunk_generation_v1.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"_source_files", "_sphinxsim_files"}]
        cls.updated = {"Path": Path, "Iterable": Iterable,
                       "discover_files": discover_files, "load_config": load_config}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), cls.updated)
        cls.config = load_config()

    def check_parity(self, repository, function):
        root = PROJECT / "repos" / repository
        if not root.is_dir() or not any(root.iterdir()):
            self.skipTest(f"{repository}: NOT VERIFIED — local checkout unavailable or empty")
        legacy = {"_source_files": legacy_source_files, "_sphinxsim_files": legacy_sphinxsim_files}
        old = [p.relative_to(root).as_posix() for p in legacy[function](root)]
        new = [p.relative_to(root).as_posix() for p in self.updated[function](root)]
        configured = [p.relative_to(root).as_posix() for p in discover_files(self.config, repository, root)]
        expected = old + (["sphinxsim/cli.py", "sphinxsim/json_format.py"] if repository == "SPHinXsim" else [])
        first_difference = next((i for i, pair in enumerate(zip(expected, new)) if pair[0] != pair[1]),
                                min(len(expected), len(new)) if len(expected) != len(new) else None)
        report = {"repository": repository, "V1_count": len(old), "configured_count": len(new),
                  "only_V1": sorted(set(old) - set(new)), "only_configuration": sorted(set(new) - set(old)),
                  "expected_set_equality": set(expected) == set(new), "expected_ordered_equality": expected == new,
                  "first_ordering_difference": first_difference}
        print(json.dumps(report, sort_keys=True), flush=True)
        self.assertEqual(expected, new, json.dumps(report))
        self.assertEqual(new, configured)

    def test_sphinxsys_parity(self):
        self.check_parity("SPHinXsys", "_source_files")

    def test_sphinxsim_parity(self):
        self.check_parity("SPHinXsim", "_sphinxsim_files")


if __name__ == "__main__":
    unittest.main()
