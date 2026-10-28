"""R2's actual index is isolated; historical R1 scoring and records survive."""
from collections import Counter
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import retrieval_v1 as r1
from src.retrieval_v2 import RetrievalV2, searchable_text, create_retriever
from src.retrieval_identity_diagnostics import (record_scores, rank_trace, classify_identity_order,
                                               run_diagnostics, retrieval_fingerprint)

P = Path(__file__).resolve().parents[1]


def record(identifier="a", original="aa12bb34", **updates):
    value = {"chunk_id": identifier, "repository": "SPHinXsys", "source_role": "simulation_case",
             "source_path": "repos/SPHinXsys/tests/example.cpp", "chunk_type": "semantic_section",
             "node_type": "semantic_section", "symbol": "Dynamics", "start_line": 2, "end_line": 3,
             "byte_start": 10, "byte_end": 40, "content": "pressure relaxation density dynamics",
             "parent_context": {"original_function_chunk_id": original, "original_function_symbol": "main",
                                "original_function_start_line": 1, "original_function_byte_start": 0,
                                "description": "prepare the fluid dynamics", "semantic_section_index": 0}}
    value.update(updates)
    return value


class IdentityNeutralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def corpus(self, records, name="corpus.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in records))
        return path

    def test_exact_direct_field_removed_from_view(self):
        value = record(); expected = copy.deepcopy(value)
        del expected["parent_context"]["original_function_chunk_id"]
        self.assertEqual(searchable_text(value), r1._searchable_text(expected))
        self.assertNotIn("original_function_chunk_id", searchable_text(value))
        self.assertNotIn("aa12bb34", searchable_text(value))

    def test_semantic_context_and_numeric_ranges_preserved(self):
        text = searchable_text(record())
        for value in ["pressure relaxation", "Dynamics", "main", "prepare the fluid dynamics",
                      "original_function_start_line", "original_function_byte_start", "semantic_section_index"]:
            self.assertIn(value, text)

    def test_source_literals_equal_to_excluded_label_or_hash_remain(self):
        value = record(content='const char* original_function_chunk_id = "aa12bb34";')
        self.assertIn(value["content"], searchable_text(value))
        context = json.loads(searchable_text(value).rsplit("\n", 1)[-1])
        self.assertNotIn("original_function_chunk_id", context)

    def test_only_exact_context_path_excluded(self):
        value = record()
        value["parent_context"]["nested"] = {"original_function_chunk_id": "legitimate_nested_label"}
        value["parent_context"]["other_id"] = "cc56dd78"
        text = searchable_text(value)
        self.assertIn("legitimate_nested_label", text); self.assertIn("cc56dd78", text)

    def test_missing_identity_field_preserves_r1_text_exactly(self):
        value = record(); del value["parent_context"]["original_function_chunk_id"]
        self.assertEqual(searchable_text(value), r1._searchable_text(value))

    def test_missing_or_nonmapping_context_preserves_r1_serialization(self):
        for context in [None, "description", ["parent"]]:
            value = record(parent_context=context)
            self.assertEqual(searchable_text(value), r1._searchable_text(value))
        value = record(); del value["parent_context"]
        self.assertEqual(searchable_text(value), r1._searchable_text(value))

    def test_structural_metadata_and_top_id_not_added_to_text(self):
        value = record(); value["structural_metadata"] = {"symbol_id": "marker_not_searchable"}
        value["chunk_id"] = "another_not_searchable_marker"
        self.assertNotIn("marker_not_searchable", searchable_text(value))
        self.assertNotIn("another_not_searchable_marker", searchable_text(value))

    def test_actual_index_uses_r2_text_instead_of_module_level_r1(self):
        path = self.corpus([record()]); engine = RetrievalV2(path)
        self.assertEqual(engine.indexed_texts[0], searchable_text(engine.records[0]))
        self.assertEqual(engine.term_frequencies[0], Counter(r1.tokenize(engine.indexed_texts[0])))
        self.assertNotEqual(engine.term_frequencies[0], r1.RetrievalV1(path).term_frequencies[0])
        self.assertEqual(engine.document_lengths[0], sum(engine.term_frequencies[0].values()))

    def test_original_record_and_persisted_bytes_are_not_mutated(self):
        rows = [record()]; before = copy.deepcopy(rows); path = self.corpus(rows); raw = path.read_bytes()
        searchable_text(rows[0]); engine = RetrievalV2(path); found = engine.search("dynamics", 1)
        self.assertEqual(rows, before); self.assertEqual(engine.records, before)
        self.assertEqual(path.read_bytes(), raw)
        self.assertIs(found[0].record, engine.records[0])
        self.assertEqual(found[0].record["parent_context"]["original_function_chunk_id"], "aa12bb34")
        citation = found[0].as_dict(True)
        self.assertEqual(citation["source_path"], before[0]["source_path"])
        self.assertEqual(citation["start_line"], before[0]["start_line"])

    def test_scoring_tokenizer_ranking_and_dedup_methods_reused(self):
        for name in ["_bm25", "_symbol_boost", "_path_boost", "_role_boost",
                     "_repository_boost", "search", "_load_records"]:
            self.assertIs(getattr(RetrievalV2, name), getattr(r1.RetrievalV1, name))

    def test_r1_unchanged_in_same_process_after_r2_construction_and_search(self):
        path = self.corpus([record()]); engine = r1.RetrievalV1(path)
        text_function = r1._searchable_text; before = copy.deepcopy(vars(engine))
        scores = [(x.record["chunk_id"], x.score.hex()) for x in engine.search("dynamics")]
        RetrievalV2(path).search("dynamics")
        self.assertIs(r1._searchable_text, text_function); self.assertEqual(vars(engine), before)
        fresh = r1.RetrievalV1(path)
        self.assertEqual(vars(fresh), before)
        self.assertEqual(scores, [(x.record["chunk_id"], x.score.hex()) for x in fresh.search("dynamics")])

    def test_new_factory_defaults_to_historical_r1(self):
        path = self.corpus([record()]); self.assertIs(type(create_retriever(path)), r1.RetrievalV1)
        self.assertIs(type(create_retriever(path, version="r2")), RetrievalV2)

    def test_unknown_version_rejected_before_loading(self):
        with self.assertRaisesRegex(ValueError, "Unknown Retriever"):
            create_retriever(self.root / "does-not-exist", version="R99")

    def test_cross_identity_text_counter_statistics_and_exact_scores_equal(self):
        left = [record(), record("c", "cc33dd44", symbol="Other", content="fluid configuration")]
        right = copy.deepcopy(left)
        for i, value in enumerate(right):
            value["chunk_id"] = "portable-" + str(i)
            value["parent_context"]["original_function_chunk_id"] = "ff99ee88" + str(i)
        a = RetrievalV2(self.corpus(left)); b = RetrievalV2(self.corpus(right, "other.jsonl"))
        for name in ["indexed_texts", "term_frequencies", "document_lengths", "average_document_length", "idf"]:
            self.assertEqual(getattr(a, name), getattr(b, name))
        for query in ["pressure dynamics", "configuration fluid", "2D main"]:
            self.assertEqual([x.score.hex() for x in record_scores(a, query)],
                             [x.score.hex() for x in record_scores(b, query)])

    def test_records_without_exclusion_have_exact_r1_index_and_results(self):
        rows = [record(), record("b", symbol="Other", content="fluid configuration")]
        for row in rows:del row["parent_context"]["original_function_chunk_id"]
        path = self.corpus(rows); a = r1.RetrievalV1(path); b = RetrievalV2(path)
        for name in ["term_frequencies", "document_lengths", "average_document_length", "idf"]:
            self.assertEqual(getattr(a, name), getattr(b, name))
        self.assertEqual(a.search("dynamics fluid"), b.search("dynamics fluid"))

    def test_tie_order_can_change_dedup_winner_without_score_mismatch(self):
        left = [record("a"), record("z")]; right = copy.deepcopy(left)
        right[0]["chunk_id"] = "z"; right[1]["chunk_id"] = "a"
        a = RetrievalV2(self.corpus(left)); b = RetrievalV2(self.corpus(right, "other.jsonl"))
        sa, sb = record_scores(a, "dynamics"), record_scores(b, "dynamics")
        ta, tb = rank_trace(a, "dynamics", sa), rank_trace(b, "dynamics", sb)
        report = classify_identity_order(ta, tb, sa, sb)
        self.assertEqual(len(report["reordered_tie_groups"]), 1)
        self.assertEqual(len(report["dedup_winner_changes"]), 1)
        self.assertEqual(report["unexplained_differences"], 0)
        self.assertEqual(ta["retained_indices"], [0]); self.assertEqual(tb["retained_indices"], [1])

    def test_real_score_mismatch_is_rejected_by_classifier(self):
        a = RetrievalV2(self.corpus([record()])); scores = record_scores(a, "dynamics")
        trace = rank_trace(a, "dynamics", scores)
        other = [r1.SearchResult(scores[0].record, scores[0].lexical_score + 1, 0, 0, 0, 0)]
        with self.assertRaisesRegex(ValueError, "score mismatch"):
            classify_identity_order(trace, trace, scores, other)

    def test_diagnostic_requires_seed_before_loading_inputs(self):
        with patch.dict(os.environ, {"PYTHONHASHSEED": "wrong"}):
            with self.assertRaisesRegex(ValueError, "PYTHONHASHSEED"):
                run_diagnostics(P, self.root / "missing", self.root / "output")

    def test_fingerprint_locks_r1_r2_runtime_contract_and_parameters(self):
        contract = {"exclude": ["parent_context.original_function_chunk_id"]}
        first = retrieval_fingerprint(P, contract, 1.5, .75)
        self.assertIn("src/retrieval_v1.py", first["inputs"]["implementation_files"])
        self.assertIn("src/retrieval_v2.py", first["inputs"]["implementation_files"])
        self.assertNotEqual(first["fingerprint"], retrieval_fingerprint(P, contract, 1.6, .75)["fingerprint"])
        self.assertNotEqual(first["fingerprint"], retrieval_fingerprint(P, {}, 1.5, .75)["fingerprint"])

    def test_independent_process_reproducibility_and_new_cwd(self):
        path = self.corpus([record()]); code = (
            "import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
            "from src.retrieval_v2 import RetrievalV2;"
            "r=RetrievalV2(Path(sys.argv[2]));"
            "print(json.dumps({'lengths':r.document_lengths,'tokens':[dict(sorted(c.items())) for c in r.term_frequencies],"
            "'results':[(x.record['chunk_id'],x.score.hex()) for x in r.search('pressure dynamics')]} ,sort_keys=True))")
        cmd = [sys.executable, "-B", "-c", code, str(P), str(path)]
        env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        a = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True, text=True, check=True)
        b = subprocess.run(cmd, cwd=P, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(a.stdout, b.stdout)


if __name__ == "__main__":
    unittest.main()
