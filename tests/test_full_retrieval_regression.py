"""P2.2 adapter, exact composite keys, frozen grading and rank edge cases."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src import full_retrieval_regression as review
from src import remediation_evaluation as frozen
from src.corpus_snapshot import file_digest, write_json
from src.retrieval_evaluation_criteria import outcome
from src.retrieval_evaluation_io import expected_inventory
from src.retrieval_identity_diagnostics import retrieval_fingerprint
from src.retrieval_v1 import RetrievalV1, SearchResult
from src.retrieval_v2 import RetrievalV2

P = Path(__file__).resolve().parents[1]
BENCHMARK = P / 'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json'


def record(identifier='target', **updates):
    row = dict(chunk_id=identifier, repository='SPHinXsys', source_role='simulation_case',
               source_path='repos/SPHinXsys/tests/2d_examples/test_2d_dambreak/Dambreak.cpp',
               symbol='main', node_type='semantic_section', start_line=88, end_line=106,
               content='pressure_relaxation density_relaxation', parent_context={'original_function_chunk_id': 'ab12'})
    row.update(updates); return row


def scored(row, value=1.0):
    return SearchResult(row, value, 0.0, 0.0, 0.0, 0.0)


class FullRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = json.loads(BENCHMARK.read_text())

    def corpus(self):
        path = self.root / 'corpus.jsonl'; path.write_text(json.dumps(record()) + '\n'); return path

    def matrix_fixture(self, old_rank=10, new_rank=None):
        correspondence = [{'index': 0, 'legacy_id': 'legacy-target', 'portable_id': 'portable-target'}]
        correspondence += [{'index': i, 'legacy_id': 'filler-' + str(i), 'portable_id': 'filler-' + str(i)} for i in range(1, 11)]
        result = {}
        for condition in review.CONDITIONS:
            rows = []
            for mode, qid in sorted(expected_inventory(self.spec)):
                q = next(q for q in self.spec['questions'] if q['id'] == qid); found = []
                rank = old_rank if condition in {'A', 'B'} else new_rank
                if mode == 'combined' and qid == 'B-02' and rank:
                    found = [record('filler-' + str(i), source_path='repos/SPHinXsys/unrelated.cpp') for i in range(1, rank)]
                    found.append(record('legacy-target' if condition in {'A', 'C'} else 'portable-target'))
                rows.append({'id': qid, 'mode': mode, 'group': q['group'], 'evidence_available': True,
                             'strict': outcome(found, q['requirements']),
                             'top_10': [{'record': r, 'chunk_id': r['chunk_id'], 'score_hex': (1.0).hex()} for r in found],
                             'top_10_ids': [r['chunk_id'] for r in found]})
            result[condition] = rows
        return result, correspondence

    def test_adapter_uses_existing_evaluator_function(self):
        self.assertIs(review.evaluate, frozen.evaluate)
        self.assertIs(review.current_availability, frozen.current_availability)

    def test_explicit_condition_selection(self):
        path = self.corpus()
        self.assertIs(type(review.condition_factory('A')(path)), RetrievalV1)
        self.assertIs(type(review.condition_factory('B')(path)), RetrievalV1)
        self.assertIs(type(review.condition_factory('C')(path)), RetrievalV2)
        self.assertIs(type(review.condition_factory('D')(path)), RetrievalV2)

    def test_unknown_condition_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown evaluation'):
            review.condition_factory('unknown')

    def test_reject_existing_output_before_reading_inputs(self):
        with self.assertRaises(FileExistsError):review.run_condition(P, self.root / 'missing', 'C', self.root)

    def manifest(self, **updates):
        m = {'schema_version': 1, 'implementation_files': {'src/retrieval_v1.py': file_digest(P/'src/retrieval_v1.py')},
             'input_files': {str(BENCHMARK): file_digest(BENCHMARK)}, 'benchmark': str(BENCHMARK),
             'parameters': {'k1': 1.5, 'b': .75}, 'exclusion_contract': {'exclude': ['parent_context.original_function_chunk_id']}}
        m['R2_fingerprint'] = retrieval_fingerprint(P, m['exclusion_contract'], 1.5, .75)
        m.update(updates); path = self.root / 'inputs.json'; write_json(path, m); return path

    def test_verified_frozen_benchmark_and_seed(self):
        with patch.dict(os.environ, {'PYTHONHASHSEED': '0'}):
            _, spec = review.verify_experiment(P, self.manifest())
            self.assertEqual(len(expected_inventory(spec)), 128)

    def test_parameters_may_not_be_retuned(self):
        with patch.dict(os.environ, {'PYTHONHASHSEED': '0'}):
            with self.assertRaisesRegex(ValueError, 'parameters'):
                review.verify_experiment(P, self.manifest(parameters={'k1': 1.6, 'b': .75}))

    def test_wrong_input_checksum_rejected(self):
        with patch.dict(os.environ, {'PYTHONHASHSEED': '0'}):
            with self.assertRaisesRegex(ValueError, 'Input checksum'):
                review.verify_experiment(P, self.manifest(input_files={str(BENCHMARK): '0'*64}))

    def test_seed_checked_before_missing_manifest(self):
        with patch.dict(os.environ, {'PYTHONHASHSEED': 'wrong'}):
            with self.assertRaisesRegex(ValueError, 'PYTHONHASHSEED'):review.verify_experiment(P, self.root/'missing')

    def test_duplicate_mode_question_key_rejected(self):
        rows, _ = self.matrix_fixture()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):review.indexed_results(rows['A'] + rows['A'][:1], self.spec)

    def test_missing_combination_rejected(self):
        rows, _ = self.matrix_fixture()
        with self.assertRaisesRegex(ValueError, 'Missing'):review.indexed_results(rows['A'][:-1], self.spec)

    def test_512_unique_keys_and_separate_denominators(self):
        rows, correspondence = self.matrix_fixture(); matrix = review.compare_matrix(rows, self.spec, correspondence)
        self.assertEqual(len(matrix['flat']), 512)
        groups = matrix['transition_summaries']
        self.assertEqual(groups['A→D:combined:primary']['n'], 84)
        self.assertEqual(groups['A→D:combined:diagnostic']['n'], 4)
        self.assertEqual(groups['A→D:documentation_only:primary']['n'], 40)

    def test_hit5_and_hit10_transitions_independent(self):
        rows, correspondence = self.matrix_fixture(); matrix = review.compare_matrix(rows, self.spec, correspondence)
        detail = next(r for r in matrix['per_question'] if r['question_id']=='B-02' and r['corpus_mode']=='combined')
        pair = next(p for p in detail['comparisons'] if p['from']=='A' and p['to']=='D')
        self.assertEqual(pair['hit5_transition'], 'FAIL→FAIL'); self.assertEqual(pair['hit10_transition'], 'PASS→FAIL')

    def test_improvement_does_not_cancel_another_loss_in_quality_logic(self):
        rows, correspondence = self.matrix_fixture(); matrix = review.compare_matrix(rows, self.spec, correspondence)
        gain=copy.deepcopy(matrix['per_question'][0]);gain['question_id']='independent-policy-fixture'
        for pair in gain['comparisons']:
            if pair['to']=='D' and pair['from'] in {'A','B'}:
                pair['hit5_transition']='FAIL→PASS';pair['hit10_transition']='FAIL→PASS'
        matrix['per_question'].append(gain)
        decision = review.quality_review(matrix)
        self.assertEqual(decision['retrieval_quality'], 'QUALITY_REVIEW_REQUIRED')
        self.assertEqual({r['metric'] for r in decision['new_or_unresolved_primary_losses']}, {'hit10'})
        self.assertTrue(decision['primary_gains'])
        self.assertFalse(decision['automatic_reuse'])

    def test_known_failures_alone_are_not_new_regressions(self):
        rows, correspondence = self.matrix_fixture(old_rank=None, new_rank=None)
        matrix = review.compare_matrix(rows, self.spec, correspondence)
        self.assertEqual(review.quality_review(matrix)['retrieval_quality'], 'QUALITY_ACCEPTED')
        self.assertEqual(review.quality_review(matrix, unresolved=True)['retrieval_quality'], 'QUALITY_REVIEW_REQUIRED')

    def test_alternative_b02_content_cannot_be_relabelled_strict_pass(self):
        rows, correspondence = self.matrix_fixture()
        row = next(r for r in rows['D'] if r['id']=='B-02' and r['mode']=='combined')
        row['strict']['hit_at_10'] = True
        with self.assertRaisesRegex(ValueError, 'grading'):review.compare_matrix(rows, self.spec, correspondence)

    def test_comparison_is_deterministic_and_inputs_not_mutated(self):
        rows, correspondence = self.matrix_fixture(); before=copy.deepcopy(rows)
        first = review.compare_matrix(rows, self.spec, correspondence)
        second = review.compare_matrix(rows, self.spec, correspondence)
        self.assertEqual(json.dumps(first,sort_keys=True),json.dumps(second,sort_keys=True));self.assertEqual(rows,before)

    def test_exact_rank_is_post_dedup_rank(self):
        rows=[record()]; scores=[scored(rows[0])]
        trace={'retained_indices':[0],'trace':[{'record_index':0,'skipped_by_record_index':None}]}
        criterion={'path':rows[0]['source_path'],'range':[98,104]}
        result=review.requirement_ranks(rows,[criterion],scores,trace)[0]
        self.assertEqual(result['full_target_rank'],1);self.assertTrue(result['rank_verified'])

    def test_nonpositive_candidate_has_no_invented_rank(self):
        rows=[record()];scores=[scored(rows[0],0.0)];trace={'retained_indices':[],'trace':[]}
        result=review.requirement_ranks(rows,[{'path':rows[0]['source_path']}],scores,trace)[0]
        self.assertTrue(result['evidence_available']);self.assertIsNone(result['full_target_rank'])
        self.assertEqual(result['rank_diagnostic_status'],'NONPOSITIVE_SCORE_FILTERED')

    def test_dedup_suppressed_target_has_no_invented_rank(self):
        rows=[record(),record('other',content='different')];scores=[scored(r) for r in rows]
        trace={'retained_indices':[1],'trace':[{'record_index':0,'skipped_by_record_index':1},{'record_index':1,'skipped_by_record_index':None}]}
        criterion={'path':rows[0]['source_path'],'contains_all':['pressure_relaxation']}
        result=review.requirement_ranks(rows,[criterion],scores,trace)[0]
        self.assertEqual(result['rank_diagnostic_status'],'DEDUP_SUPPRESSED');self.assertIsNone(result['full_target_rank'])

    def test_multiple_requirements_remain_separate_from_unavailable(self):
        rows=[record()];scores=[scored(rows[0])];trace={'retained_indices':[0],'trace':[{'record_index':0}]}
        requirements=[{'path':rows[0]['source_path']},{'path':'repos/SPHinXsys/missing.cpp'}]
        result=review.requirement_ranks(rows,requirements,scores,trace)
        self.assertEqual(len(result),2);self.assertEqual(result[0]['full_target_rank'],1)
        self.assertEqual(result[1]['rank_diagnostic_status'],'EVIDENCE_UNAVAILABLE')


if __name__=='__main__':unittest.main()
