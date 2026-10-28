"""Current-corpus diagnostics and controlled-runner regression tests."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from src.remediation_evaluation import current_availability, validate_manifest
from src.retrieval_evaluation_criteria import outcome
from src.retrieval_evaluation_reports import aggregate_metrics

P = Path(__file__).resolve().parents[1]


def record(identifier='new', start=1, end=8):
    return dict(chunk_id=identifier, source_path='repos/SPHinXsim/example.cpp',
                source_role='simulator_library', repository='SPHinXsim',
                symbol='f', start_line=start, end_line=end, content='void f() {}')


def question(group='D'):
    return dict(id='fixture', group=group, exact_evidence_available=False,
                pre_ranking_coverage='COVERAGE_GAP',
                requirements=[dict(path='repos/SPHinXsim/example.cpp', kind='symbol', symbol='f', range=[1, 8])])


class CurrentEvidenceTests(unittest.TestCase):
    def test_current_evidence_independent_of_historical_gap_and_ids(self):
        q = question(); before = copy.deepcopy(q)
        report = current_availability({'questions': [q]}, [record('replacement-ID')], 'combined')[0]
        self.assertFalse(report['historical_evidence_available'])
        self.assertTrue(report['current_evidence_available'])
        self.assertEqual(report['requirements'][0]['matching_chunk_ids'], ['replacement-ID'])
        self.assertEqual(q, before)
        self.assertFalse(report['included_in_primary_denominator'])

    def test_overlap_does_not_relax_single_chunk_range(self):
        rs = [record('a', 1, 4), record('b', 5, 8)]
        r = current_availability({'questions': [question()]}, rs, 'combined')[0]
        self.assertEqual(r['current_pre_ranking_coverage'], 'PARTIAL_EVIDENCE')
        self.assertFalse(outcome(rs, question()['requirements'])['hit_at_10'])

    def test_all_of_not_one_hit(self):
        q = question(); q['requirements'].append({**q['requirements'][0], 'symbol': 'missing'})
        self.assertFalse(current_availability({'questions': [q]}, [record()], 'combined')[0]['current_evidence_available'])
        self.assertEqual(outcome([record()], q['requirements'])['partial_coverage_at_5'], .5)

    def test_missing_legacy_ids_do_not_matter(self):
        self.assertEqual(current_availability({'questions': [question()]}, [], 'combined')[0]['current_pre_ranking_coverage'], 'COVERAGE_GAP')

    def test_diagnostics_excluded_from_primary_aggregation(self):
        q = dict(group='D', mode='combined', category='fixture')
        metrics = aggregate_metrics([q])
        self.assertEqual(metrics['combined_primary_all']['n'], 0)
        self.assertEqual(metrics['group'], {})

    def test_documentation_control_selects_only_A(self):
        self.assertEqual(current_availability({'questions': [question()]}, [], 'documentation_only'), [])

    def test_manifest_rejects_output_overwrite_before_other_input_access(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):
                validate_manifest(Path('missing'), Path('missing'), Path('missing'), Path('missing'), Path(d), P, Path('missing'))

    def test_manifest_accepts_new_count_and_rejects_wrong_checksum(self):
        from src.retrieval_evaluation_io import file_sha256, canonical_sha256
        benchmark = P/'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json'
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); docs = d/'docs'; combined = d/'combined'; lock = d/'lock'
            r = {**record(), 'source_role': 'documentation'}
            docs.write_text(json.dumps(r)+'\n'); combined.write_text(json.dumps(r)+'\n')
            files = ['src/retrieval_evaluation_criteria.py', 'src/retrieval_evaluation_io.py',
                     'src/retrieval_evaluation_reports.py', 'src/remediation_evaluation.py',
                     'scripts/evaluate_remediation_step_1_7.py']
            m = dict(schema_version=1, experiment_id='fixture', benchmark_sha256=file_sha256(benchmark),
                configuration_sha256=canonical_sha256(json.loads(benchmark.read_text())['config']),
                retriever_sha256=file_sha256(P/'src/retrieval_v1.py'),
                implementation_files={f:file_sha256(P/f) for f in files},
                corpora={role:dict(sha256=file_sha256(path),record_count=1) for role,path in
                         [('combined',combined),('documentation_only',docs)]})
            lock.write_text(json.dumps(m))
            spec,manifest,rs = validate_manifest(benchmark,combined,docs,lock,d/'out',P,P/'src/retrieval_v1.py')
            self.assertEqual(len(rs['combined']),1)
            combined.write_text('tampered')
            with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
                validate_manifest(benchmark,combined,docs,lock,d/'out',P,P/'src/retrieval_v1.py')

    def test_cli_requires_independent_manifest_and_inputs(self):
        spec = importlib.util.spec_from_file_location('step17_cli_test', P/'scripts/evaluate_remediation_step_1_7.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        parsed = module.build_parser().parse_args(['--benchmark','b','--corpus','c','--documentation-corpus','d','--output-dir','o','--manifest','m'])
        self.assertEqual(parsed.manifest, Path('m'))
        self.assertEqual(parsed.corpus, Path('c'))


if __name__ == '__main__':
    unittest.main()
