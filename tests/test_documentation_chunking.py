"""Dependency-free structural, integrity, selection and safe publication tests.

All output files and synthetic repositories live in tempfile directories.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.documentation_chunking import (
    chunk_document, generate_documentation, validate_records,
)
from src.retrieval_v1 import RetrievalV1


class StructureTests(unittest.TestCase):
    def chunks(self, text, suffix="md", target=160, threshold=240):
        source = text.encode("utf-8") if isinstance(text, str) else text
        records, report = chunk_document(source, "SPHinXsim", "docs/test." + suffix,
                                         target_bytes=target, oversized_bytes=threshold)
        self.assertEqual(b"".join(r["content"].encode("utf-8") for r in records), source)
        self.assertEqual(report["covered_bytes"], len(source))
        self.assertFalse(report["missing_byte_ranges"])
        self.assertFalse(report["overlapping_byte_ranges"])
        return records, report

    def atomic(self, records, content):
        self.assertEqual(sum(content in r["content"] for r in records), 1)

    def test_markdown_atx_hierarchy_and_repeated_titles(self):
        records, report = self.chunks("# Root\nintro\n\n## Same\na\n\n### Leaf\nb\n\n## Same\nc\n")
        self.assertEqual([s["level"] for s in report["sections"]], [1, 2, 3, 2])
        self.assertEqual(records[2]["parent_context"]["heading_path"], ["Root", "Same", "Leaf"])
        self.assertEqual(len({r["symbol"] for r in records}), len(records))

    def test_setext_and_atx_closing_marks(self):
        _, report = self.chunks("Root\n====\n\nChild\n-----\ntext\n\n### Third ###\nrest\n")
        self.assertEqual([(s["title"], s["level"]) for s in report["sections"]],
                         [("Root", 1), ("Child", 2), ("Third", 3)])

    def test_fenced_and_indented_false_headings(self):
        text = "# Real\n\n````bash\n# shell comment\n```\n## still code\n````\n\n    # indented\n    code\n\n## Next\nbody\n"
        records, report = self.chunks(text, target=35, threshold=100)
        self.assertEqual([s["title"] for s in report["sections"]], ["Real", "Next"])
        self.atomic(records, "````bash\n# shell comment\n```\n## still code\n````")
        self.atomic(records, "    # indented\n    code")

    def test_tilde_fence(self):
        records, report = self.chunks("# Root\n\n~~~python\n# fake\n~~~\n", target=15, threshold=80)
        self.assertEqual(len(report["sections"]), 1)
        self.atomic(records, "~~~python\n# fake\n~~~")

    def test_rst_overline_underlined_headings_and_preamble(self):
        text = "Intro before heading.\n\n=======\nRoot\n=======\n\nChild\n-----\n\nThird\n~~~~~\nbody\n"
        records, report = self.chunks(text, "rst")
        self.assertTrue(records[0]["content"].startswith("Intro before"))
        self.assertEqual([(s["title"], s["level"]) for s in report["sections"]],
                         [("Root", 1), ("Child", 2), ("Third", 3)])
        self.assertEqual(records[-1]["parent_context"]["heading_path"], ["Root", "Child", "Third"])

    def test_rst_code_options_and_fake_heading(self):
        code = ".. code-block:: cpp\n   :linenos:\n\n\tTitle\n\t=====\n\tint main() {}\n"
        records, report = self.chunks("Root\n====\n\n" + code + "\nNext paragraph.\n", "rst", 30, 100)
        self.assertEqual(len(report["sections"]), 1)
        self.atomic(records, code)

    def test_rst_figure_math_and_literal_blocks(self):
        figure = ".. figure:: figure.png\n   :width: 500 px\n\n   Figure caption\n"
        math = ".. math::\n\n   x = y + z\n"
        literal = "    # fake heading\n    ======\n    example\n"
        records, _ = self.chunks("Root\n====\n\n" + figure + "\n" + math + "\nExample::\n\n" + literal, "rst", 35, 120)
        for atom in [figure, math, literal]:
            self.atomic(records, atom)

    def test_unknown_directive_and_include_preserved_not_executed(self):
        text = ".. include:: /nonexistent/never-read\n\n.. mystery:: value\n\n   body\n\nTail\n"
        records, report = self.chunks(text, "rst", 30, 100)
        self.assertEqual(len(report["warnings"]), 2)
        self.assertTrue(records[-1]["content"].endswith("Tail\n"))

    def test_nested_list_and_markdown_table_preservation(self):
        nested = "- parent\n  - nested\n\n    continuation\n- sibling\n"
        table = "| Name | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n"
        records, _ = self.chunks("# Root\n\n" + nested + "\n" + table + "\nEnd\n", target=35, threshold=90)
        self.atomic(records, nested)
        self.atomic(records, table)

    def test_rst_grid_table(self):
        table = "+---+---+\n| A | B |\n+===+===+\n| 1 | 2 |\n+---+---+\n"
        records, _ = self.chunks("Root\n====\n\n" + table + "\nTail\n", "rst", 30, 100)
        self.atomic(records, table)

    def test_long_section_splits_only_at_paragraph_boundaries(self):
        paragraphs = [f"Paragraph {i}: " + "x" * 70 + "\n\n" for i in range(8)]
        records, _ = self.chunks("# Root\n\n" + "".join(paragraphs), target=200, threshold=250)
        self.assertGreater(len(records), 2)
        for paragraph in paragraphs:
            self.atomic(records, paragraph.rstrip("\n"))

    def test_oversized_code_and_directive_and_list_are_not_truncated(self):
        for suffix, atom in [("md", "```\n" + "x" * 400 + "\n```\n"),
                             ("rst", ".. code-block:: cpp\n\n    " + "x" * 400 + "\n"),
                             ("md", "- parent\n  - " + "x" * 400 + "\n")]:
            with self.subTest(suffix=suffix, atom=atom[:20]):
                records, report = self.chunks(atom, suffix, 80, 100)
                self.atomic(records, atom)
                self.assertTrue(report["oversized_chunks"])
                self.assertIn("Oversized atomic", report["warnings"][0]["message"])

    def test_display_math_with_blank_lines_is_atomic(self):
        expression = "$$\n" + "x = " + "a" * 60 + "\n\n" + "y = " + "b" * 60 + "\n$$\n"
        records, _ = self.chunks("# Theory\n\n" + expression + "\nTail explanation.\n", target=70, threshold=200)
        self.atomic(records, expression)

    def test_display_math_single_line_and_code_literals(self):
        text = "# Theory\n\n$$x = y$$\n\n```text\n$$not math\n```\n\n## End\nbody\n"
        records, report = self.chunks(text, target=20, threshold=100)
        self.atomic(records, "$$x = y$$")
        self.assertFalse(report["warnings"])
        self.assertEqual([s["title"] for s in report["sections"]], ["Theory", "End"])

    def test_unclosed_display_math_preserved_without_swallowing_sections(self):
        text = "# Theory\n\n$$\nx = y\n\n## Later\n" + "tail text\n\n" * 20
        records, report = self.chunks(text, target=50, threshold=100)
        self.assertTrue(any("Unclosed Markdown display math" in w["message"] for w in report["warnings"]))
        self.assertEqual(report["sections"][-1]["title"], "Later")
        self.assertGreater(len(records), 2)

    def test_nested_latex_and_rst_math_directives(self):
        latex = "\\begin{equation}\n\\begin{cases}\na=b\n\n c=d\n\\end{cases}\n\\end{equation}\n"
        records, _ = self.chunks(latex, target=20, threshold=200)
        self.atomic(records, latex)
        directive = ".. math::\n   :label: example\n\n   a=b\n\n   c=d\n"
        records, _ = self.chunks("Root\n====\n\n" + directive + "\nTail\n", "rst", 20, 200)
        self.atomic(records, directive)

    def test_crlf_display_math_with_nested_environment_and_blank_lines(self):
        equation = "$$\r\n\\begin{aligned}\r\na &= b\r\n\r\nc &= d\r\n\\end{aligned}\r\n$$\r\n"
        records, _ = self.chunks("# Math\r\n\r\n" + equation + "\r\nExplanation.\r\n", target=25, threshold=150)
        self.atomic(records, equation)

    def test_large_rst_code_remains_one_atom(self):
        code = ".. code-block:: cpp\n\n" + "    int sample = 0;\n" * 1300
        records, report = self.chunks("Example\n=======\n\n" + code + "\nExplanatory text.\n",
                                      "rst", target=3000, threshold=4000)
        self.atomic(records, code)
        self.assertEqual(len(report["oversized_chunks"]), 1)
        self.assertTrue(any("Oversized atomic rst_directive" in w["message"] for w in report["warnings"]))

    def test_powershell_fences_preserve_backticks_quotes_and_crlf(self):
        code = '```powershell\r\ncmake --preset windows `\r\n  -D Python3_EXECUTABLE="$(python -c \"import sys; print(sys.executable)\")"\r\n```\r\n'
        records, report = self.chunks("# Windows\r\n\r\n" + code + "\r\n## Next\r\ntext\r\n", target=35, threshold=200)
        self.atomic(records, code)
        self.assertFalse(report["warnings"])

    def test_yaml_front_matter_is_atomic_not_setext_heading(self):
        front = "---\nlayout: post\ntitle: Sample\ncategories: sph method\n---\n"
        records, report = self.chunks(front + "\nAuthor\n\n## Real section\nBody\n", target=30, threshold=160)
        self.assertEqual([h["title"] for h in report["sections"]], ["Real section"])
        self.atomic(records, front)

    def test_front_matter_only_at_document_start(self):
        _, report = self.chunks("Real title\n---\nbody\n")
        self.assertEqual([h["title"] for h in report["sections"]], ["Real title"])

    def test_unclosed_front_matter_warns_without_hiding_body(self):
        text = "---\nlayout: post\ncategories: sph\n\n## Real section\nBody\n"
        _, report = self.chunks(text)
        self.assertEqual([h["title"] for h in report["sections"]], ["Real section"])
        self.assertTrue(any("Unclosed YAML front matter" in w["message"] for w in report["warnings"]))

    def test_script_html_comment_and_liquid_do_not_create_headings(self):
        atoms = ["<script>\n# fake script heading\n\nnot a section\n---\n</script>\n",
                 "<!--\n# fake comment heading\n-->\n",
                 "{% raw %}\n# fake liquid heading\n\n{% endraw %}\n",
                 "<p>\nnot a heading\n---\n</p>\n"]
        text = "# Real\n\n" + "\n".join(atoms) + "\n## Next\nContent\n"
        records, report = self.chunks(text, target=35, threshold=180)
        self.assertEqual([h["title"] for h in report["sections"]], ["Real", "Next"])
        for atom in atoms:
            self.atomic(records, atom)

    def test_unclosed_script_is_preserved_and_reported(self):
        records, report = self.chunks("# Title\n<script>\n# still script\n")
        self.assertEqual([h["title"] for h in report["sections"]], ["Title"])
        self.assertTrue(any("Unclosed literal markup" in w["message"] for w in report["warnings"]))

    def test_literal_markup_inside_fence_stays_code(self):
        text = "# Real\n\n```text\n---\ncategories: test\n---\n<script>\n# code\n{% raw %}\n```\n\n## Next\nBody\n"
        records, report = self.chunks(text, target=30, threshold=150)
        self.assertEqual([h["title"] for h in report["sections"]], ["Real", "Next"])
        self.assertFalse(report["warnings"])

    def test_balanced_and_malformed_equations(self):
        equation = "\\begin{equation}\n\n  x = y\n\n\\end{equation}\n"
        records, report = self.chunks("# Theory\n\n" + equation + "\nTail\n", target=20, threshold=100)
        self.atomic(records, equation)
        self.assertFalse(report["warnings"])
        records, report = self.chunks("# Theory\n\\end{equation}\n\n" + "Tail paragraph.\n\n" * 10,
                                      target=50, threshold=100)
        self.assertTrue(report["warnings"])
        self.assertGreater(len(records), 2)

    def test_unclosed_environment_does_not_swallow_rest(self):
        records, report = self.chunks("# A\n\\begin{equation}\nx=y\n\n## B\n" + "tail\n\n" * 20,
                                      target=40, threshold=100)
        self.assertTrue(report["warnings"])
        self.assertEqual(report["sections"][-1]["title"], "B")
        self.assertGreater(len(records), 2)

    def test_unclosed_fence_warns_and_preserves_source(self):
        _, report = self.chunks("# A\n```\n# protected\n")
        self.assertEqual(len(report["sections"]), 1)
        self.assertIn("Unclosed", report["warnings"][0]["message"])

    def test_utf8_crlf_and_no_final_newline(self):
        source = "# 标题\r\n\r\n第一段：ä🙂\r\n\r\n## 子标题\r\n末尾".encode()
        records, _ = self.chunks(source, target=35, threshold=80)
        for record in records:
            self.assertEqual(record["content"].encode(), source[record["byte_start"]:record["byte_end"]])
            self.assertEqual(record["start_line"], source[:record["byte_start"]].count(b"\n") + 1)
            self.assertEqual(record["end_line"], source[:record["byte_end"] - 1].count(b"\n") + 1)

    def test_empty_whitespace_and_tiny_files(self):
        records, report = self.chunks(b"")
        self.assertEqual(records, [])
        self.assertIn("Empty", report["warnings"][0]["message"])
        for source in [b" ", b"\r\n\r\n", b"a"]:
            self.chunks(source)

    def test_reproducibility_and_identity_inputs(self):
        source = b"# A\n\ntext\n"
        first, _ = self.chunks(source)
        second, _ = self.chunks(source)
        self.assertEqual(first, second)
        original = first[0]["chunk_id"]
        for kwargs in [dict(repository="SPHinXsys"), dict(relative_path="docs/other.md"),
                       dict(source=source + b"\n"), dict(target_bytes=159)]:
            params = dict(source=source, repository="SPHinXsim", relative_path="docs/test.md", target_bytes=160, oversized_bytes=240)
            params.update(kwargs)
            changed, _ = chunk_document(**params)
            self.assertNotEqual(original, changed[0]["chunk_id"])

    def test_validation_rejects_integrity_and_duplicate_failures(self):
        source = b"# A\n\ntext\n"
        records, _ = self.chunks(source)
        sources = {("SPHinXsim", "docs/test.md"): source}
        for field, value in [("content", "wrong"), ("byte_start", 1), ("byte_end", 2),
                             ("start_line", 999), ("end_line", 999), ("source_role", "test"),
                             ("source_path", "repos/SPHinXsim/../bad.md"), ("repository", "other")]:
            bad = deepcopy(records)
            bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_records(bad, sources)
        with self.assertRaises(ValueError):
            validate_records(records + records, sources)
        with self.assertRaises(ValueError):
            validate_records([], sources)

    def test_invalid_path_type_and_utf8_rejected(self):
        for path in ["../a.md", "/a.md", "C:/a.md", "a.py"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                chunk_document(b"text", "SPHinXsim", path)
        with self.assertRaises(UnicodeDecodeError):
            chunk_document(b"\xff", "SPHinXsim", "a.md")

    def test_retriever_does_not_collapse_section_parts(self):
        records, _ = self.chunks("# Gradient\n\n" + ("Gradient explanation " + "x" * 60 + "\n\n") * 5,
                                 target=100, threshold=150)
        with tempfile.TemporaryDirectory(prefix="sphinx-doc-retrieval-") as directory:
            path = Path(directory) / "docs.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            results = RetrievalV1(path).search("gradient", top_k=len(records))
            self.assertEqual({r.record["chunk_id"] for r in results}, {r["chunk_id"] for r in records})


class DocumentationScopeTests(unittest.TestCase):
    def test_exact_final_selection_and_original30_order(self):
        from src.indexing_scope import discover_files, load_config
        baseline = {
            "SPHinXsys": ["tutorials/sphinx/examples/example1_2D_dambreak.rst",
                          "tutorials/sphinx/examples/example8_2D_hydrostatic_fsi.rst"],
            "SPHinXsim": ["docs/cli-usage.md", "docs/index.md", "docs/theoretical-background-sph-methods.md"],
        }
        additions = {
            "SPHinXsys": ["tutorials/sphinx/theory.rst", "tutorials/sphinx/introduction.rst",
                          "tutorials/sphinx/SPHinXsysArch.rst", "tutorials/sphinx/examples/example2_elasticGate.rst",
                          "tutorials/sphinx/examples/example3_3D_multiResolutionParticleDistribution.rst",
                          "tutorials/sphinx/examples/example6_2D_heat_transfer.rst",
                          "tutorials/sphinx/examples/example7_2D_diffusion.rst",
                          "tutorials/sphinx/examples/example13_2D_oscillating_beam.rst"],
            "SPHinXsim": ["docs/theoretical-background-physical-models.md", "docs/visualization.md",
                          "docs/installation-linux.md"],
        }
        p2 = {
            "SPHinXsim": [
                "docs/installation-windows.md",
                "docs/installation-macos.md"
            ],
            "SPHinXsys": [
                "tutorials/sphinx/examples/example5_2D_tayloygreen.rst",
                "tutorials/sphinx/examples/example9_2D_staticConfinement.rst",
                "tutorials/sphinx/examples/example10_2D_thinPlate.rst",
                "tutorials/sphinx/examples/example12_2D_flowaroundcylinder.rst",
                "tutorials/sphinx/examples/example15_2D_aortic_valve.rst",
                "tutorials/sphinx/examples/example17_2D_throat.rst",
                "tutorials/sphinx/examples/example18_2D_wetting.rst",
                "tutorials/markdown/smoothing_kernel/first_order_consistancy.md",
                "tutorials/markdown/multi_resolution_solid/multi_resolution_solid.md",
                "tutorials/markdown/multiphase_mixture_model.md",
                "tests/3d_examples/test_3d_solid_to_shell_coupling/doc/README.md",
                "tests/3d_examples/test_3d_taylor_bar_hjc/README.md"
            ]
        }
        final_additions = {
            "SPHinXsys": [
                "README.md",
                "shell_model.md",
                "tutorials/markdown/fluid_shell_interaction/one-side-fluid-shell-interaction.md",
                "tutorials/markdown/smoothing_kernel/aisotropic_kernel.md",
                "tutorials/sphinx/examples/example4_2D_multiResolutionParticleDistribution.rst",
                "tutorials/sphinx/examples/example11_3D_arch.rst",
                "docs/_posts/2024-09-20-tensile-instability-p1.md",
                "docs/_posts/2024-09-20-tensile-instability-p2.md",
                "docs/_posts/2025-01-31-heterogeneous-parallelism-p2.md",
                "docs/_posts/2025-12-15-sparse-grid-heterogeneous-p2.md",
                "docs/_posts/2026-01-08-sparse-grid-heterogeneous-p3.md"
            ],
            "SPHinXsim": [
                "docs/llm-testing.md"
            ]
        }
        config = load_config(PROJECT / "configs/documentation_scope.json")
        self.assertEqual(set(config["repositories"]), set(baseline))
        self.assertEqual(sum(len(paths) for paths in baseline.values()), 5)
        self.assertEqual(sum(len(paths) for paths in additions.values()), 11)
        self.assertEqual(sum(len(paths) for paths in p2.values()), 14)
        self.assertEqual(sum(len(paths) for paths in final_additions.values()), 12)
        for repository in baseline:
            rules = config["repositories"][repository]
            original16 = baseline[repository] + additions[repository]
            original30 = original16 + p2[repository]
            expected = original30 + final_additions[repository]
            self.assertEqual(len(expected), 33 if repository == "SPHinXsys" else 9)
            self.assertEqual(rules["include_files"][:len(original30)], original30)
            self.assertEqual(rules["include_files"][:len(original16)], original16)
            self.assertEqual(rules["include_directories"], [])
            self.assertEqual(rules["include_files"], expected)  # excludes every unapproved document
            self.assertEqual(len(set(expected)), len(expected))
            root = PROJECT / "repos" / repository
            if root.is_dir():
                for _ in range(2):
                    self.assertEqual([p.relative_to(root).as_posix() for p in discover_files(config, repository, root)],
                                     expected)
        self.assertIn("sphinxsim/sphinxsys", config["repositories"]["SPHinXsim"]["exclude_directories"])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sphinx-doc-generation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repos/SPHinXsim"
        self.repo.mkdir(parents=True)
        (self.repo / "a.md").write_bytes(b"# Root\r\n\r\nText\r\n")
        self.config = self.root / "scope.json"
        self.rules = {"include_directories": [], "extensions": [".md", ".rst"],
                      "include_files": ["a.md"], "exclude_directories": [], "exclude_files": []}
        self.write_config()
        self.output, self.summary = self.root / "docs.jsonl", self.root / "summary.json"

    def write_config(self):
        self.config.write_text(json.dumps({"schema_version": 1, "repositories": {"SPHinXsim": self.rules}}))

    def generate(self):
        return generate_documentation(self.root, self.output, self.summary, self.config)

    def assert_no_outputs_or_stages(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.summary.exists())
        self.assertFalse(list(self.root.glob(".documentation-stage-*")))

    def test_publish_and_repeated_generation_identical(self):
        first = self.generate()
        output = self.output.read_bytes()
        second_path, second_summary = self.root / "second.jsonl", self.root / "second-summary.json"
        second = generate_documentation(self.root, second_path, second_summary, self.config)
        self.assertEqual(first, second)
        self.assertEqual(output, second_path.read_bytes())
        self.assertEqual(json.loads(self.summary.read_text()), first)
        self.assertEqual(first["integrity"], "PASS")

    def test_missing_input_is_explicit_error_with_no_output(self):
        self.rules["include_files"].append("missing.md")
        self.write_config()
        with self.assertRaises(FileNotFoundError):
            self.generate()
        self.assert_no_outputs_or_stages()

    def test_unsupported_input_and_excluded_input_rejected(self):
        (self.repo / "a.py").write_text("not documentation")
        self.rules["include_files"] = ["a.py"]
        self.write_config()
        with self.assertRaises(ValueError):
            self.generate()
        self.assert_no_outputs_or_stages()
        self.rules["include_files"] = ["a.md"]
        self.rules["exclude_files"] = ["a.md"]
        self.write_config()
        with self.assertRaises(ValueError):
            self.generate()

    def test_symlink_escape_rejected(self):
        outside = self.root / "outside.md"
        outside.write_text("outside")
        (self.repo / "escape.md").symlink_to(outside)
        self.rules["include_files"] = ["escape.md"]
        self.write_config()
        with self.assertRaises(ValueError):
            self.generate()
        self.assert_no_outputs_or_stages()

    def test_existing_destinations_not_overwritten(self):
        for path in [self.output, self.summary]:
            path.write_bytes(b"existing user data")
            with self.assertRaises(FileExistsError):
                self.generate()
            self.assertEqual(path.read_bytes(), b"existing user data")
            path.unlink()

    def test_identical_destination_rejected(self):
        with self.assertRaises(ValueError):
            generate_documentation(self.root, self.output, self.output, self.config)
        self.assert_no_outputs_or_stages()

    def test_invalid_utf8_does_not_publish(self):
        (self.repo / "a.md").write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            self.generate()
        self.assert_no_outputs_or_stages()

    def test_publication_failure_rolls_back_only_owned_outputs(self):
        import os
        real_link = os.link

        def fail_second(source, destination):
            if Path(destination) == self.summary:
                raise OSError("injected second publication failure")
            real_link(source, destination)

        with patch("src.documentation_chunking.os.link", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "publication failed"):
                self.generate()
        self.assert_no_outputs_or_stages()

    def test_publication_race_never_overwrites_competing_file(self):
        import os
        real_link = os.link

        def race_second(source, destination):
            if Path(destination) == self.summary:
                self.summary.write_bytes(b"competing user file")
            real_link(source, destination)

        with patch("src.documentation_chunking.os.link", side_effect=race_second):
            with self.assertRaises(RuntimeError):
                self.generate()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.summary.read_bytes(), b"competing user file")
        self.assertFalse(list(self.root.glob(".documentation-stage-*")))

    def test_staging_integrity_failure_does_not_publish(self):
        from src.documentation_chunking import validate_records as real_validate
        calls = 0

        def fail_staged(records, sources):
            nonlocal calls
            calls += 1
            if calls == 3:  # chunk validation, aggregate validation, staged validation
                raise ValueError("injected staged integrity failure")
            return real_validate(records, sources)

        with patch("src.documentation_chunking.validate_records", side_effect=fail_staged):
            with self.assertRaisesRegex(RuntimeError, "staged integrity failure"):
                self.generate()
        self.assertEqual(calls, 3)
        self.assert_no_outputs_or_stages()

    def test_cli_requires_explicit_outputs_and_works_from_other_cwd(self):
        script = PROJECT / "scripts/generate_documentation_chunks_v1.py"
        result = subprocess.run([sys.executable, "-B", str(script)], cwd=self.root, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, "-B", str(script), "--help"], cwd=self.root, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"--output", result.stdout)


if __name__ == "__main__":
    unittest.main()
