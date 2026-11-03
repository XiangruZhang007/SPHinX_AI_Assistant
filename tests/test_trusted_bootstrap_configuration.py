"""Exact initial P2 configuration and rejection gates; no network or full build."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src import ci_v2_research as c, schema2_workflow as w, trusted_snapshot as t
from src import upstream_provenance as u
from src.corpus_snapshot import digest, file_digest, read_json

P = Path(__file__).resolve().parents[1]
KEY = 'p2-initial-bootstrap'
REFERENCE_SHA = '52c6a32d77ddefec103e5d882655b7ac58756c4bab57edd3ad4b83eec2b0ea48'
POLICY_SHA = '9fc2729a4b2881ebd557e797554f5ec6696f877fffbe9b523f270098e2df4fb5'
TARGETS = {'SPHinXsys': '351368e108f47f8d15f89baac96683ecadaee32c',
           'SPHinXsim': 'dceacd4e509a8592b802cf44bb2885bea7ddfdb3'}


class TrustedBootstrapConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.registry = read_json(t.REGISTRY)
        self.entry = self.registry['baselines'][KEY]
        self.ref = self.entry['reference']

    def test_exact_reviewed_reference_and_canonical_digest(self):
        self.assertEqual(self.entry['expected_reference_sha256'], REFERENCE_SHA)
        canonical = json.dumps(self.ref, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), REFERENCE_SHA)
        self.assertEqual(digest(self.ref), REFERENCE_SHA)
        self.assertNotEqual(hashlib.sha256((json.dumps(self.ref, indent=2) + '\n').encode()).hexdigest(),
                            REFERENCE_SHA)
        self.assertEqual(t.registry_reference(self.registry, KEY), self.entry)

    def test_exact_p2_identity_and_research_only_authorization(self):
        self.assertEqual(self.registry['status'], 'P2_BOOTSTRAP_CONFIGURED')
        self.assertEqual(set(self.registry['baselines']), {KEY})
        self.assertEqual((self.ref['schema_version'], self.ref['package_format'],
                          self.ref['accepted_package_schema']), (2, 'p2-snapshot-v1', 1))
        self.assertEqual(self.ref['node_id'],
                         'sha256-798d0d1284c7dbba06d5788a062973bf7290cbbfdef28150037371fa78b7188a')
        self.assertEqual(self.ref['upstreams'], {
            'SPHinXsys': '4e686ee357de1cb798ac47eb760dd79e2fb407ff',
            'SPHinXsim': '9cdae783e3ef3f8926c2174a0db89e96846befad'})
        self.assertEqual(self.ref['authorization'], {
            'type': 'EXPLICIT_P2_BOOTSTRAP', 'scope': 'ISOLATED_RESEARCH_PARENT_REUSE',
            'research_only': True, 'experiment_id': 'P5A.1-D-both-update',
            'automatic_research_approval': False, 'production_promotion': 'NOT_AUTHORIZED'})

    def test_both_exact_published_asset_bindings(self):
        for field, asset_id, name, sha_field, expected_sha in [
            ('storage', 691803006, 'p2-bootstrap-798d0d12.tar.gz', 'transport_sha256',
             '56fa2921d0a2a7efb02361e89cdcf40e70d77cc98381f0fdeed634b19598ff0c'),
            ('parent_rows_storage', 691803341, 'p2-parent-r2-results.json', 'parent_rows_sha256',
             '659af559de9d648a73c2aa0706b0308721bd16f66da2a76012e7767d6b1e323e')]:
            with self.subTest(field=field):
                self.assertEqual(self.ref[field], {
                    'kind': 'GITHUB_RELEASE_ASSET', 'repository': 'XiangruZhang007/SPHinX_AI_Assistant',
                    'release_id': 428919662, 'asset_id': asset_id,
                    'tag': 'ci-v2-p2-bootstrap-798d0d12', 'asset_name': name, 'immutable_required': True})
                self.assertEqual(self.ref[sha_field], expected_sha)

    def test_frozen_policy_matches_exact_parent_target_and_implementation(self):
        self.assertEqual(self.entry['build_policy_path'], 'configs/ci_v2_p2_bootstrap_build_policy.json')
        self.assertEqual(self.entry['build_policy_sha256'], POLICY_SHA)
        path = P / self.entry['build_policy_path']
        self.assertEqual(file_digest(path), POLICY_SHA)
        policy = u._policy(path, POLICY_SHA)
        self.assertEqual(policy['scope']['target_upstreams'], TARGETS)
        self.assertEqual(policy['scope']['base_upstreams'], self.ref['upstreams'])
        self.assertEqual(policy['scope']['base_source_state'], self.ref['source_state'])
        self.assertEqual(policy['scope']['parent'], {'kind': 'P2_ANCHOR',
            'node_id': self.ref['node_id'], 'package_checksum': self.ref['package_checksum']})
        self.assertEqual(policy['scope']['build_fingerprint'], digest(c.read(c.LOCK)['P2']))
        self.assertEqual(policy['scope']['orchestration_fingerprint'], digest(u.contract(P)))
        self.assertEqual(policy['experiment_id'], self.ref['authorization']['experiment_id'])
        self.assertTrue(policy['p2_anchor_authorized'])
        self.assertFalse(policy['automatic_research_approval'])
        self.assertFalse(policy['automatic_reuse'])

    def test_configured_plan_passes_without_granting_approval(self):
        plan = w.plan(P, self.registry, KEY, TARGETS)
        self.assertEqual(plan['status'], 'SCHEMA2_MANUAL_PLAN')
        self.assertEqual(plan['registry_entry_sha256'], digest(self.entry))
        self.assertEqual(plan['upstreams'], TARGETS)
        for name in ('automatic_reuse', 'automatic_research_approval',
                     'scheduled_execution', 'research_baseline_created'):
            self.assertIs(plan[name], False)
        self.assertEqual(plan['production_promotion'], 'NOT_AUTHORIZED')

    def test_workflow_defaults_match_authorized_targets(self):
        import yaml
        doc = yaml.safe_load((P / '.github/workflows/ci_v2_manual_research.yml').read_text())
        inputs = doc.get('on', doc.get(True))['workflow_dispatch']['inputs']
        self.assertEqual(inputs['sys_revision']['default'], TARGETS['SPHinXsys'])
        self.assertEqual(inputs['sim_revision']['default'], TARGETS['SPHinXsim'])

    def test_parent_revisions_and_other_targets_remain_unauthorized(self):
        for revisions in [self.ref['upstreams'], {**TARGETS, 'SPHinXsim': 'a' * 40}]:
            with self.subTest(revisions=revisions), self.assertRaises(c.GateError) as caught:
                w.plan(P, self.registry, KEY, revisions)
            self.assertEqual(caught.exception.status, 'AUTHORIZATION_FAILED')

    def test_changed_asset_or_parent_rows_cannot_use_existing_reference_pin(self):
        for field in ('storage', 'parent_rows_storage'):
            registry = copy.deepcopy(self.registry)
            registry['baselines'][KEY]['reference'][field]['asset_id'] += 1
            with self.subTest(field=field), self.assertRaises(c.GateError) as caught:
                w.plan(P, registry, KEY, TARGETS)
            self.assertEqual(caught.exception.status, 'INVALID_REFERENCE')

    def test_invalid_reference_digest_remains_rejected(self):
        self.entry['expected_reference_sha256'] = '0' * 64
        with self.assertRaises(c.GateError) as caught:
            w.plan(P, self.registry, KEY, TARGETS)
        self.assertEqual(caught.exception.status, 'INVALID_REFERENCE')

    def test_storage_safety_checks_survive_a_recomputed_reference_digest(self):
        for field in ('storage', 'parent_rows_storage'):
            for name, value in [('repository', 'untrusted/repo'), ('asset_id', 0),
                                ('release_id', None), ('immutable_required', False), ('tag', 'latest')]:
                ref = copy.deepcopy(self.ref); ref[field][name] = value
                with self.subTest(field=field, name=name), self.assertRaises(c.GateError) as caught:
                    t.validate_reference(ref, digest(ref), remote=True)
                self.assertEqual(caught.exception.status, 'UNSAFE_INPUT')

    def test_approval_and_production_flags_remain_rejected(self):
        for name, value in [('type', 'EXPLICIT_RESEARCH_BASELINE_REUSE'),
                            ('scope', 'INITIAL_BOOTSTRAP_ONLY'), ('research_only', False),
                            ('automatic_research_approval', True), ('production_promotion', 'AUTHORIZED')]:
            ref = copy.deepcopy(self.ref); ref['authorization'][name] = value
            with self.subTest(name=name), self.assertRaises(c.GateError) as caught:
                t.validate_reference(ref, digest(ref), remote=True)
            self.assertEqual(caught.exception.status, 'AUTHORIZATION_FAILED')

    def test_research_parent_execution_still_requires_separate_qualification(self):
        ref = self.ref
        ref.update(package_format='schema2-research-baseline', accepted_package_schema=2)
        ref['authorization']['type'] = 'EXPLICIT_RESEARCH_BASELINE_REUSE'
        self.entry['expected_reference_sha256'] = digest(ref)
        with self.assertRaises(c.GateError) as caught:
            w.plan(P, self.registry, KEY, TARGETS)
        self.assertEqual(caught.exception.status, 'PARENT_RETRIEVAL_ANCHOR_NOT_QUALIFIED')

    def test_incorrect_policy_pin_remains_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'policy.json').write_bytes((P / self.entry['build_policy_path']).read_bytes())
            self.entry.update(build_policy_path='policy.json', build_policy_sha256='0' * 64)
            with self.assertRaises(c.GateError) as caught:
                w.plan(project, self.registry, KEY, TARGETS)
            self.assertEqual(caught.exception.status, 'AUTHORIZATION_FAILED')


if __name__ == '__main__':
    unittest.main()
