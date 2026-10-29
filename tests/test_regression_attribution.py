"""Controlled attribution facts and a separately qualified genuine DOC-Q27 pair."""
import copy
import json
import os
from pathlib import Path
import unittest

from src import regression_attribution as a, version_aware_evidence as q
from src.retrieval_evaluation_criteria import outcome
from test_version_aware_evidence import make_state, criterion, fixture_spec, PATH
import test_version_aware_evidence as mapping_fixtures


class AttributionTests(unittest.TestCase):
    def setUp(self):
        raw = b'# Heading\nLEFT unique anchor\noriginal evidence text\nRIGHT unique anchor\n'
        self.base = make_state({PATH: raw}); self.target = self.base
        self.criteria = [criterion()]
        self.base_rank = 1; self.target_rank = 1

    def row(self, state, rank, mode='combined'):
        records = state.records
        found = [] if rank is None else [{'rank': rank, 'record': records[0], 'chunk_id': records[0]['chunk_id']}]
        # Controlled rank positions; source records and source mappings are real.
        strict = outcome([x['record'] for x in found], self.criteria)
        strict['hit_at_5'] = bool(rank is not None and rank <= 5 and strict['hit_at_5'])
        strict['hit_at_10'] = bool(rank is not None and rank <= 10 and strict['hit_at_10'])
        return {'id': 'F-01', 'mode': mode, 'strict': strict, 'evidence_available': bool(records), 'top_10': found}

    def summary(self, *, mode='combined', missing_base=False, q1=True):
        spec = fixture_spec(self.criteria)
        old = [] if missing_base else [self.row(self.base, self.base_rank, mode)]
        new = [self.row(self.target, self.target_rank, mode)]
        report = q.analyze(spec, self.base, self.target, benchmark_sha256='fixture', implementation_contract={}, base_rows=old, target_rows=new)
        return a.summarize(spec, old, new, report if q1 else None, bindings={'provenance': 'CONTROLLED_TEST_FIXTURE'})

    def failed(self, **kwargs):
        self.target_rank = None
        return self.summary(**kwargs)['failures'][0]

    def test_new_ranking_regression_requires_threshold_crossing(self):
        row = self.failed()
        self.assertIn('RANKING_REGRESSION', row['supported_attribution'])
        self.assertNotIn('BENCHMARK_LOCATOR_INVALIDATED', row['supported_attribution'])

    def test_historical_ranking_failure_is_not_new_regression(self):
        self.base_rank = None; row = self.failed()
        self.assertIn('PREEXISTING_RANKING_FAILURE', row['supported_attribution'])
        self.assertNotIn('RANKING_REGRESSION', row['supported_attribution'])
        self.assertIn('NO_NEW_REGRESSION_IDENTIFIED', row['review_recommendations'])

    def test_hit5_regression_does_not_invent_hit10_failure(self):
        self.target_rank = 6
        row = self.summary()['failures'][0]
        self.assertFalse(row['observed_facts']['target_strict']['hit_at_5'])
        self.assertTrue(row['observed_facts']['target_strict']['hit_at_10'])
        self.assertNotIn('10', row['review_at_k'])

    def test_rank_absence_is_not_rank_eleven(self):
        row = self.failed()
        rank = row['requirements'][0]['ranks']['target']
        self.assertEqual(rank, {'status': 'NOT_RETURNED_IN_TOP10', 'rank': None})

    def test_source_content_changed(self):
        self.target = make_state({PATH: self.base.sources[PATH].replace(b'original evidence text', b'changed text')})
        row = self.failed()
        self.assertIn('SOURCE_CONTENT_CHANGED', row['supported_attribution'])
        self.assertNotIn('RANKING_REGRESSION', row['supported_attribution'])

    def test_source_removed_needs_demonstrated_absence(self):
        self.target = make_state({}, absences=(PATH,)); row = self.failed()
        self.assertIn('SOURCE_EVIDENCE_REMOVED', row['supported_attribution'])

    def test_missing_selected_source_is_not_proven_removal(self):
        self.target = make_state({}); row = self.failed()
        self.assertIn('ATTRIBUTION_UNCERTAIN', row['supported_attribution'])
        self.assertNotIn('SOURCE_EVIDENCE_REMOVED', row['supported_attribution'])

    def test_source_without_corpus(self):
        self.target = make_state(self.base.sources, records=[])
        self.assertIn('CORPUS_COVERAGE_GAP', self.failed()['supported_attribution'])

    def split(self, base=False, gap=False):
        helper = mapping_fixtures.EvidenceMappingTests(); helper.setUp()
        self.target = helper.split_target(gap=gap)
        if base: self.base = self.target

    def test_new_fragmentation(self):
        self.split(); row = self.failed()
        self.assertIn('EVIDENCE_FRAGMENTATION', row['supported_attribution'])
        self.assertEqual(row['requirements'][0]['change_stage']['EVIDENCE_FRAGMENTATION'], 'NEWLY_INTRODUCED')

    def test_preexisting_fragmentation(self):
        self.split(base=True); row = self.failed()
        self.assertEqual(row['requirements'][0]['change_stage']['EVIDENCE_FRAGMENTATION'], 'PREEXISTING')
        self.assertNotIn('RANKING_REGRESSION', row['supported_attribution'])

    def test_partial_byte_gaps_preserve_uncertainty(self):
        self.split(gap=True); row = self.failed()
        self.assertIn('ATTRIBUTION_UNCERTAIN', row['supported_attribution'])
        self.assertNotIn('EVIDENCE_FRAGMENTATION', row['supported_attribution'])

    def test_ambiguous_correspondence(self):
        self.target = make_state({PATH: self.base.sources[PATH] + b'original evidence text\n'})
        self.assertIn('ATTRIBUTION_UNCERTAIN', self.failed()['supported_attribution'])

    def test_unresolved_correspondence(self):
        self.target = make_state({PATH: b'# Different\nEntirely different contents.\n'})
        self.assertIn('ATTRIBUTION_UNCERTAIN', self.failed()['supported_attribution'])

    def test_missing_parent_rank_does_not_invent_regression(self):
        row = self.failed(missing_base=True)
        self.assertIn('ATTRIBUTION_UNCERTAIN', row['supported_attribution'])
        self.assertNotIn('RANKING_REGRESSION', row['supported_attribution'])

    def test_unqualified_q1_is_nonblocking_unknown(self):
        row = self.failed(q1=False)
        self.assertEqual(row['supported_attribution'], ['ATTRIBUTION_UNCERTAIN'])

    def test_both_modes_preserved(self):
        self.target_rank = None
        for mode in ('combined', 'documentation_only'):
            self.assertEqual(self.summary(mode=mode)['failures'][0]['mode'], mode)

    def test_multiple_requirements_keep_indices(self):
        self.criteria.append(criterion(2, 2)); row = self.failed()
        self.assertEqual([r['requirement_index'] for r in row['requirements']], [0, 1])

    def test_moved_locator_and_other_rank_failure_coexist(self):
        self.target = make_state({PATH: b'prefix\n' + self.base.sources[PATH]})
        self.criteria.append({'kind': 'passage', 'path': PATH, 'value': 'LEFT unique anchor', 'role': 'documentation'})
        row = self.failed()
        self.assertIn('BENCHMARK_LOCATOR_INVALIDATED', row['supported_attribution'])
        self.assertIn('RANKING_REGRESSION', row['supported_attribution'])
        self.assertNotIn('EXPLAINED_BENCHMARK_APPLICABILITY_CHANGE', row['review_recommendations'])

    def test_stale_locator_does_not_prove_mapped_evidence_was_retrieved(self):
        self.target = make_state({PATH: b'prefix\n' + self.base.sources[PATH]})
        row = self.failed()
        self.assertIn('ATTRIBUTION_UNCERTAIN', row['supported_attribution'])
        self.assertNotIn('EXPLAINED_BENCHMARK_APPLICABILITY_CHANGE', row['review_recommendations'])

    def test_moved_evidence_retrieved_explains_without_scoring_replacement(self):
        self.target = make_state({PATH: b'prefix\n' + self.base.sources[PATH]})
        # Frozen passage remains contained by the whole-document fixture chunk,
        # so use a strict Notes-section fixture below rather than force FAIL.
        self.target_rank = None
        before = copy.deepcopy(self.base.sources)
        result = self.summary()
        self.assertEqual(self.base.sources, before)
        self.assertFalse(result['official_score_adjustment'])
        self.assertFalse(result['blocking'])

    def test_summary_has_no_approval_privilege(self):
        self.target_rank = None; report = self.summary()
        self.assertFalse(report['automatic_research_approval'])
        self.assertFalse(report['research_baseline_created'])
        self.assertEqual(report['human_decision'], 'PENDING')
        self.assertEqual(report['production_promotion'], 'NOT_AUTHORIZED')

    def test_new_requirement_loss_visible_even_if_question_already_failed(self):
        self.target_rank = None
        self.criteria.append({'kind': 'passage', 'path': PATH, 'value': 'LEFT unique anchor', 'role': 'documentation'})
        spec = fixture_spec(self.criteria)
        old = self.row(self.base, 1)
        old['strict']['hit_at_5'] = False
        old['strict']['hit_at_10'] = False  # Another ALL_OF requirement already failed.
        new = self.row(self.target, None)
        mapped = q.analyze(spec, self.base, self.target, benchmark_sha256='fixture', implementation_contract={})
        result = a.summarize(spec, [old], [new], mapped, bindings={})['failures'][0]
        self.assertIn('RANKING_REGRESSION', result['supported_attribution'])
        self.assertFalse(result['observed_facts']['pass_to_fail_observed'])

    def test_missing_returned_rank_evidence_is_unknown(self):
        self.target_rank = None; spec = fixture_spec(self.criteria)
        old = self.row(self.base, 1); new = self.row(self.target, None)
        del new['top_10']
        report = q.analyze(spec, self.base, self.target, benchmark_sha256='fixture', implementation_contract={})
        row = a.summarize(spec, [old], [new], report, bindings={})['failures'][0]
        self.assertIn('ATTRIBUTION_UNCERTAIN', row['supported_attribution'])
        self.assertNotIn('RANKING_REGRESSION', row['supported_attribution'])

    def test_reproducible_report(self):
        self.target_rank = None
        self.assertEqual(self.summary(), self.summary())


class HistoricalAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('SPHINX_P5A2_RUN')
        if not path: raise unittest.SkipTest('Pinned P5A.2 historical integration output not configured')
        cls.root = Path(path)
        cls.report = json.loads((cls.root / 'regression-attribution.json').read_text())

    def test_DOC_Q27_both_modes_frozen_fail_rank_one(self):
        rows = [r for r in self.report['failures'] if r['id'] == 'DOC-Q27']
        self.assertEqual({r['mode'] for r in rows}, {'combined', 'documentation_only'})
        for row in rows:
            self.assertFalse(row['observed_facts']['target_strict']['hit_at_10'])
            self.assertEqual(row['supported_attribution'], ['BENCHMARK_LOCATOR_INVALIDATED'])
            self.assertFalse(row['ranking_regression_demonstrated'])
            self.assertEqual(row['review_recommendations'], ['EXPLAINED_BENCHMARK_APPLICABILITY_CHANGE'])
            mapping = row['requirements'][0]['Q1_evidence']
            self.assertEqual(mapping['original_passage']['line_range'], [101, 104])
            self.assertEqual(mapping['target_passage']['line_range'], [103, 106])
            self.assertEqual(mapping['diagnostic_mapped_single_chunk_top10_ranks'], [1])
            self.assertEqual(mapping['source_content_state'], 'UNCHANGED')

    def test_full_inventory_and_raw_rows_remain_unchanged(self):
        self.assertEqual(self.report['inventory']['question_mode_combinations'], 128)
        self.assertGreater(self.report['inventory']['failed_question_modes'], 0)
        for row in self.report['failures']:
            self.assertEqual(row['final_human_decision'], 'PENDING')


if __name__ == '__main__': unittest.main()
