"""Manual workflow gates and inspection of a genuine archived integration run."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import ci_v2_research as c, schema2_workflow as w, trusted_snapshot as t
from src.corpus_snapshot import digest, file_digest, read_json, write_json

P = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_dispatch_only_readonly_and_pinned_actions(self):
        import yaml
        doc = yaml.safe_load((P / '.github/workflows/ci_v2_manual_research.yml').read_text())
        self.assertEqual(set(doc.get('on', doc.get(True))), {'workflow_dispatch'})
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        steps = doc['jobs']['candidate']['steps']
        run = '\n'.join(s.get('run', '') for s in steps)
        self.assertIn('ci_v2_schema2_research.py plan', run)
        self.assertIn('ci_v2_schema2_research.py execute-plan', run)
        self.assertNotIn('ci_v2_manual_research.py', run)
        upload = next(s for s in steps if s.get('id') == 'evidence')
        self.assertEqual(upload['if'], 'always()')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        for step in steps:
            if 'uses' in step: self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')

    def test_remote_registry_remains_unconfigured(self):
        registry = read_json(t.REGISTRY)
        self.assertEqual(registry['baselines'], {})
        with self.assertRaises(c.GateError) as caught:
            w.plan(P, registry, 'unconfigured', c.read(c.LOCK)['upstreams'])
        self.assertEqual(caught.exception.status, 'BOOTSTRAP_REQUIRED')

    def test_stdlib_only_plan_reports_bootstrap_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run(['python3', '-B', str(P / 'scripts/ci_v2_schema2_research.py'), 'plan',
                '--key', 'unconfigured', '--sys-revision', c.read(c.LOCK)['upstreams']['SPHinXsys'],
                '--sim-revision', c.read(c.LOCK)['upstreams']['SPHinXsim'], '--output', tmp + '/plan'],
                cwd=tmp, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(run.stdout)['status'], 'BOOTSTRAP_REQUIRED')

    def test_no_approval_or_publication_command(self):
        run = subprocess.run([sys.executable, '-B', str(P / 'scripts/ci_v2_schema2_research.py'), '--help'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertNotIn('approve', run.stdout.split('positional arguments:')[-1])
        self.assertNotIn('publish', run.stdout)

    def test_unqualified_q1_still_emits_uncertain_nonblocking_attribution(self):
        from src.regression_attribution import summarize
        spec = {'questions': [{'id': 'unit', 'evidence_mode': 'ALL_OF', 'requirements': []}]}
        row = {'id': 'unit', 'mode': 'combined', 'strict': {'hit_at_5': False, 'hit_at_10': False}, 'top_10': []}
        report = summarize(spec, [row], [row], None, bindings={})
        self.assertEqual(report['Q1_status'], 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR')
        self.assertFalse(report['blocking'])

    def test_research_parent_execution_blocker_not_removed(self):
        ref = {'package_format': 'schema2-research-baseline'}
        entry = {'reference': ref, 'build_policy_path': 'policy.json', 'build_policy_sha256': 'pinned'}
        revisions = c.read(c.LOCK)['upstreams']
        policy = {'scope': {'target_upstreams': revisions}, 'provenance_type': 'GENUINE_UPSTREAM'}
        with patch.object(t, 'registry_reference', return_value=entry), patch.object(w, 'checked_file', return_value=Path('dummy')), \
             patch.object(w, 'read_json', return_value=policy), patch.object(w, 'file_digest', return_value='pinned'):
            with self.assertRaises(c.GateError) as caught: w.plan(P, {}, 'valid', revisions)
        self.assertEqual(caught.exception.status, 'PARENT_RETRIEVAL_ANCHOR_NOT_QUALIFIED')

    def test_unsafe_manual_revision_input(self):
        with self.assertRaises(c.GateError):
            w.plan(P, {}, 'bad', {'SPHinXsys': 'main', 'SPHinXsim': '$(id)'})

    def test_qualified_plan_revalidates_scope(self):
        ref = {'package_format': 'p2-snapshot-v1'}
        entry = {'reference': ref, 'build_policy_path': 'policy.json', 'build_policy_sha256': 'pinned'}
        revisions = c.read(c.LOCK)['upstreams']
        policy = {'scope': {'target_upstreams': revisions}, 'provenance_type': 'GENUINE_UPSTREAM'}
        with patch.object(t, 'registry_reference', return_value=entry), patch.object(w, 'checked_file', return_value=Path('dummy')), \
             patch.object(w, 'read_json', return_value=policy), patch.object(w, 'file_digest', return_value='pinned'):
            planned = w.plan(P, {}, 'valid', revisions)
        self.assertEqual(planned['status'], 'SCHEMA2_MANUAL_PLAN')
        self.assertFalse(planned['automatic_research_approval'])

    def test_optional_q1_error_does_not_claim_success_or_approve(self):
        from src import version_aware_evidence as q
        spec = {'questions': [{'id': 'unit', 'evidence_mode': 'ALL_OF', 'requirements': []}]}
        row = {'id': 'unit', 'mode': 'combined', 'strict': {'hit_at_5': False, 'hit_at_10': False}, 'top_10': []}
        with tempfile.TemporaryDirectory() as tmp, patch.object(q, 'generate', side_effect=ValueError('Unqualified pair')):
            status, report = w.diagnostics(P, Path(tmp), spec, [row], [row], {}, q1_inputs=Path(tmp) / 'inputs.json', q1_sha='0' * 64)
            self.assertEqual(status['status'], 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR')
            self.assertFalse(report['automatic_research_approval'])
            self.assertFalse(report['blocking'])


class GenuineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('SPHINX_P5A2_RUN')
        if not path: raise unittest.SkipTest('Pinned genuine P5A.2 integration output not configured')
        cls.root = Path(path); cls.result = read_json(cls.root / 'result.json')

    def test_actual_schema2_parent_and_upstream_transition(self):
        m = read_json(self.root / 'candidate/upstream-candidate.json')
        self.assertEqual(m['schema_version'], 2)
        self.assertEqual(m['identity']['parent']['node_id'], self.result['baseline_id'])
        self.assertNotEqual(m['identity']['base_upstreams'], m['identity']['upstreams'])
        self.assertEqual(m['identity']['provenance_type'], 'GENUINE_UPSTREAM')

    def test_trusted_parent_restored_exactly(self):
        ref = read_json(self.root / 'trusted-reference.json')
        self.assertEqual(file_digest(self.root / 'parent/checksums.json'), ref['manifest_sha256'])
        self.assertEqual(file_digest(self.root / 'parent/corpus.jsonl'), ref['corpus_sha256'])

    def test_independent_full_four_artifact_exact_parity(self):
        parity = read_json(self.root / 'parity.json')
        self.assertEqual(parity['status'], 'PARITY_PASS')
        self.assertEqual(len(parity['comparisons']), 4)
        for name, value in parity['comparisons'].items():
            self.assertTrue(value['exact_bytes_equal'])
            self.assertEqual((self.root / 'candidate' / name).read_bytes(), (self.root / 'full' / name).read_bytes())

    def test_frozen_results_and_actual_replays(self):
        raw = read_json(self.root / 'quality/incremental-raw-results.json')
        self.assertEqual(len(raw), 128)
        self.assertEqual(raw, read_json(self.root / 'quality/full-raw-results.json'))
        self.assertEqual(raw, read_json(self.root / 'execution-verification/incremental-replay.json'))
        self.assertEqual(raw, read_json(self.root / 'execution-verification/full-replay.json'))

    def test_Q1_and_attribution_present_but_nonblocking(self):
        self.assertEqual(read_json(self.root / 'q1-status.json')['status'], 'QUALIFIED')
        report = read_json(self.root / 'regression-attribution.json')
        self.assertFalse(report['blocking'])
        self.assertEqual(report['inventory']['question_mode_combinations'], 128)

    def test_no_baseline_created_or_pointer_update(self):
        self.assertFalse(self.result['RESEARCH_BASELINE_APPROVED'])
        self.assertFalse(self.result['RETRIEVAL_QUALITY_ACCEPTED'])
        self.assertFalse(self.result['automatic_research_approval'])
        self.assertEqual(self.result['production_promotion'], 'NOT_AUTHORIZED')
        self.assertFalse((self.root / 'candidate/upstream-baseline.json').exists())
        self.assertEqual(read_json(t.REGISTRY)['baselines'], {})

    def test_no_change_genuine_transition_reuses_every_file(self):
        unchanged = os.environ.get('SPHINX_P5A2_NOCHANGE_RUN')
        self.assertIsNotNone(unchanged, 'Final qualification must configure no-change output')
        root = Path(unchanged); result = read_json(root / 'result.json')
        metadata = read_json(root / 'candidate/upstream-candidate.json')['identity']
        self.assertEqual(metadata['base_upstreams'], metadata['upstreams'])
        self.assertEqual(result['reuse']['actual_extraction_invocations'], 0)
        self.assertEqual(result['reuse']['reused_files'], result['reuse']['selected_files'])
        self.assertEqual(result['STRUCTURAL_PARITY'], 'PASS')
        self.assertEqual(read_json(root / 'q1-status.json')['status'], 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR')


if __name__ == '__main__': unittest.main()
