import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.combine_candidate_corpus import combine, read_records


class CandidateCombinationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.record = dict(chunk_id="one", repository="SPHinXsys", source_role="core_library",
                           source_path="repos/SPHinXsys/src/a.cpp", chunk_type="function_definition",
                           node_type="function_definition", symbol="a", start_line=1, end_line=1,
                           byte_start=0, byte_end=2, parent_context={}, content="é", structural_metadata={})
        self.first = self.write("a.jsonl", [self.record])
        self.second = self.write("b.jsonl", [dict(self.record, chunk_id="two")])
        self.output = self.root / "combined.jsonl"

    def write(self, name, records):
        path = self.root / name
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def test_preserves_values_order_and_legitimate_overlap(self):
        summary = combine([self.first, self.second], self.output)
        self.assertEqual(summary["total_chunk_count"], 2)
        self.assertEqual(read_records(self.output), [self.record, dict(self.record, chunk_id="two")])

    def test_existing_semantic_section_schema_has_no_structural_metadata(self):
        section = dict(self.record, chunk_type="semantic_section", node_type="semantic_section")
        del section["structural_metadata"]
        source = self.write("section", [section])
        combine([source], self.output)
        self.assertEqual(read_records(self.output), [section])

    def test_duplicate_ids_within_and_across_inputs(self):
        for inputs in ([self.first, self.first], [self.write("dupe", [self.record, self.record])]):
            with self.assertRaises(ValueError):
                combine(inputs, self.output)
            self.assertFalse(self.output.exists())

    def test_malformed_missing_fields_paths_and_ranges(self):
        invalid = [dict(self.record, repository="Other"), dict(self.record, byte_end=3),
                   dict(self.record, source_path="repos/SPHinXsys/../x"),
                   dict(self.record, start_line=0), {}, []]
        for record in invalid:
            with self.assertRaises(ValueError):
                combine([self.write("invalid", [record])], self.output)
        bad = self.root / "bad"; bad.write_text("{bad\n")
        with self.assertRaises(ValueError):
            combine([bad], self.output)
        self.assertFalse(self.output.exists())

    def test_existing_destination_and_symlink_rejected(self):
        self.output.write_text("existing")
        with self.assertRaises(FileExistsError):
            combine([self.first], self.output)
        self.assertEqual(self.output.read_text(), "existing")
        link = self.root / "link"; link.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            combine([self.first], link)

    def test_empty_input_rejected(self):
        for inputs in ([], [self.write("empty", [])]):
            with self.assertRaises(ValueError):
                combine(inputs, self.output)

    def test_publication_race_preserves_competing_output_and_cleans_stage(self):
        def competing_link(stage, destination):
            destination.write_text("competitor")
            raise FileExistsError(destination)
        with patch("scripts.combine_candidate_corpus.os.link", side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                combine([self.first], self.output)
        self.assertEqual(self.output.read_text(), "competitor")
        self.assertFalse(list(self.root.glob(".candidate-stage-*")))

    def test_staged_validation_failure_does_not_publish(self):
        original = read_records
        def corrupt(path):
            if path.name.startswith(".candidate-stage-"):
                return []
            return original(path)
        with patch("scripts.combine_candidate_corpus.read_records", side_effect=corrupt):
            with self.assertRaises(ValueError):
                combine([self.first], self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".candidate-stage-*")))
