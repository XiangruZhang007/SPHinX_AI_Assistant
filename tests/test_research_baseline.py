"""P4 lifecycle/lineage tests using actual P2/P3 builders and curated scope."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.corpus_snapshot import SnapshotError, digest, file_digest, read_json, write_json
from src.corpus_snapshot_build import build_full_snapshot, selected_files
from src.incremental_corpus import build_incremental, current_contract
from src import research_baseline as life
from src.research_quality import (frozen_policy, review_not_evaluated, compare_outcomes,
                                  oracle_applicability)
from src.retrieval_evaluation_io import expected_inventory, validate_structure
from test_incremental_corpus import make_sources

P = Path(__file__).resolve().parents[1]


def replace_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def reseal_transport(root):
    checks = root / 'checksums.json'
    checks.unlink()
    write_json(checks, {p.relative_to(root).as_posix(): file_digest(p)
                        for p in sorted(root.rglob('*')) if p.is_file()})


class ResearchBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.shared.cleanup)
        cls.root_shared = Path(cls.shared.name)
        cls.original = cls.root_shared / 'source'
        make_sources(cls.original)
        cls.contract = current_contract(P)
        cls.fp = digest(cls.contract)
        cls.inputs = {'chunker_commit': cls.contract['external_chunker_commit'],
                      'project_head': 'P4-isolated-unit-fixture', 'build_contract': cls.contract,
                      'upstreams': {'SPHinXsys': 'a' * 40, 'SPHinXsim': 'b' * 40},
                      'selection': selected_files(P, cls.original)}
        cls.base_original = cls.root_shared / 'S0'
        build_full_snapshot(P, cls.original, cls.base_original, cls.inputs, identity_scheme='portable-v1')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        shutil.copytree(self.original, self.source)
        self.base = self.root / 'S0'
        shutil.copytree(self.base_original, self.base)
        self.policy = frozen_policy('1' * 64, {'fingerprint': 'unit-not-an-official-evaluation'})

    def generation(self, number=1, parent=None, parent_format='p2', mutate=None, approve=True):
        parent = parent or self.base
        if mutate:
            mutate(self.source)
        candidate, full, output = (self.root / (prefix + str(number)) for prefix in ('C', 'F', 'S'))
        base = life.read_parent(P, parent, self.fp, baseline_format=parent_format)
        # Freeze the external structural-only authorization before building.
        policy_path = self.root / ('policy-' + str(number) + '.json')
        if not policy_path.exists():
            write_json(policy_path, {'schema_version': 1, 'mode': 'STRUCTURAL_ONLY',
                'scope': {'experiment_id': 'predeclared-unit-fixture',
                          'parent_baseline_id': base['baseline_id'],
                          'target_source_state': 'sha256-' + digest(selected_files(P, self.source)),
                          'build_fingerprint': self.fp, 'quality_policy_sha256': digest(self.policy)},
                'quality_status': 'QUALITY_REVIEW_REQUIRED', 'automatic_reuse': False,
                'production_promotion': 'NOT_AUTHORIZED'})
        if parent_format == 'p2':
            built = build_incremental(P, parent, self.source, candidate, expected_fingerprint=self.fp,
                                      research_only=True, expected_snapshot_id=base['baseline_id'])
        else:
            built = life.build_next(P, parent, self.source, candidate,
                                    expected_fingerprint=self.fp, expected_id=base['baseline_id'])
        build_full_snapshot(P, self.source, full,
                            {**self.inputs, 'selection': selected_files(P, self.source)}, identity_scheme='portable-v1')
        quality = review_not_evaluated(candidate, self.fp, self.policy)
        auth = {'schema_version': 1, 'approved': True, 'research_only': True,
                'candidate_id': built['candidate_id'], 'parent_baseline_id': base['baseline_id'],
                'experiment_id': 'predeclared-unit-fixture', 'quality_status': quality['status'],
                'allow_quality_review': True}
        if approve:
            report = life.approve_baseline(P, candidate, parent, full, output,
                    expected_fingerprint=self.fp, parent_format=parent_format, quality=quality, authorization=auth,
                    approval_policy=policy_path, expected_policy_sha256=file_digest(policy_path))
            return output, report, built
        return candidate, parent, full, output, quality, auth

    def reject_approval(self, inputs):
        candidate, parent, full, output, quality, auth = inputs
        return life.approve_baseline(P, candidate, parent, full, output,
            expected_fingerprint=self.fp, parent_format='p2', quality=quality, authorization=auth,
            approval_policy=self.root / 'policy-1.json',
            expected_policy_sha256=file_digest(self.root / 'policy-1.json'))

    def test_three_generations_and_immediate_parent(self):
        s1, one, _ = self.generation(mutate=lambda r: (r / 'repos/SPHinXsys/src/a.cpp').write_text('int a() { return 7; }\n'))
        s2, two, built = self.generation(2, s1, 'research', lambda r: (r / 'repos/SPHinXsim/examples/a.json').write_text('{"value":9}\n'))
        self.assertEqual(built['summary']['actual_extraction_invocations'], 1)
        self.assertEqual(read_json(s2 / 'candidate.json')['identity']['base_snapshot_id'], one['baseline_id'])
        self.assertEqual((s1 / 'components/sys.jsonl').read_bytes(), (s2 / 'components/sys.jsonl').read_bytes())
        s3, three, built = self.generation(3, s2, 'research')
        self.assertEqual(three['generation'], 3)
        self.assertEqual(built['summary']['actual_extraction_invocations'], 0)
        self.assertEqual((s2 / 'corpus.jsonl').read_bytes(), (s3 / 'corpus.jsonl').read_bytes())
        self.assertEqual(two['baseline_id'], read_json(s3 / 'baseline.json')['identity']['parent']['baseline_id'])

    def test_repeated_modification_and_no_stale_records(self):
        s1, _, _ = self.generation(mutate=lambda r: (r / 'repos/SPHinXsys/src/a.cpp').write_text('int a() { return 17; }\n'))
        s2, _, built = self.generation(2, s1, 'research', lambda r: (r / 'repos/SPHinXsys/src/a.cpp').write_text('int a() { return 27; }\n'))
        self.assertEqual(built['summary']['actual_extraction_invocations'], 1)
        self.assertIn('return 27', (s2 / 'corpus.jsonl').read_text())
        self.assertNotIn('return 17', (s2 / 'corpus.jsonl').read_text())

    def test_added_file_is_reused_then_deleted(self):
        path = self.source / 'repos/SPHinXsim/examples/new.py'
        s1, _, _ = self.generation(mutate=lambda r: path.write_text('def new():\n    return 8\n'))
        s2, _, built = self.generation(2, s1, 'research')
        self.assertEqual(built['summary']['actual_extraction_invocations'], 0)
        s3, _, built = self.generation(3, s2, 'research', lambda r: path.unlink())
        self.assertEqual(built['summary']['actual_extraction_invocations'], 0)
        self.assertNotIn('examples/new.py', (s3 / 'inventory.json').read_text())

    def test_add_modify_rename(self):
        path = self.source / 'repos/SPHinXsim/examples/new.py'
        s1, _, _ = self.generation(mutate=lambda r: path.write_text('def new():\n    return 8\n'))
        s2, _, built = self.generation(2, s1, 'research', lambda r: path.write_text('def new():\n    return 9\n'))
        self.assertEqual(built['summary']['modified_files'], 1)
        s3, _, built = self.generation(3, s2, 'research', lambda r: path.rename(path.with_name('renamed.py')))
        self.assertEqual(built['summary']['added_files'], 1)
        self.assertEqual(built['summary']['deleted_files'], 1)
        self.assertNotIn('examples/new.py', (s3 / 'inventory.json').read_text())

    def test_zero_nonzero_zero_and_offsets(self):
        path = self.source / 'repos/SPHinXsys/src/a.cpp'
        s1, _, _ = self.generation(mutate=lambda r: path.write_text('// zero\n'))
        self.assertEqual(read_json(s1 / 'inventory.json')[1]['record_start'], 0)
        s2, _, _ = self.generation(2, s1, 'research', lambda r: path.write_text('int a() { return 9; }\n'))
        self.assertEqual(read_json(s2 / 'inventory.json')[1]['record_start'], 1)
        s3, _, _ = self.generation(3, s2, 'research', lambda r: path.write_text('// zero again\n'))
        self.assertEqual(read_json(s3 / 'inventory.json')[0]['status'], 'SUCCESS_ZERO_CHUNKS')

    def test_document_modified_then_reused_and_required_missing_rejected(self):
        path = self.source / 'repos/SPHinXsim/docs/cli-usage.md'
        s1, _, _ = self.generation(mutate=lambda r: path.write_text('# Changed\n\nNew text.\n'))
        s2, two, built = self.generation(2, s1, 'research')
        self.assertEqual(built['summary']['actual_extraction_invocations'], 0)
        self.assertEqual((s1 / 'components/documentation.jsonl').read_bytes(), (s2 / 'components/documentation.jsonl').read_bytes())
        path.unlink()
        with self.assertRaisesRegex(SnapshotError, 'mandatory document'):
            life.build_next(P, s2, self.source, self.root / 'bad', expected_fingerprint=self.fp, expected_id=two['baseline_id'])

    def test_unapproved_candidate_not_a_baseline(self):
        candidate, *_ = self.generation(approve=False)
        with self.assertRaises(SnapshotError):
            life.verify_baseline(P, candidate, self.fp)

    def test_explicit_expected_research_identity_required(self):
        s1, _, _ = self.generation()
        with self.assertRaisesRegex(SnapshotError, 'expected baseline identity'):
            build_incremental(P, s1, self.source, self.root / 'bad', expected_fingerprint=self.fp,
                              research_only=True, baseline_format='research')

    def test_rejected_authorization(self):
        data = self.generation(approve=False)
        data[-1]['approved'] = False
        with self.assertRaises(SnapshotError):
            self.reject_approval(data)
        self.assertFalse(data[3].exists())

    def test_quality_review_requires_explicit_exception(self):
        data = self.generation(approve=False)
        data[-1]['allow_quality_review'] = False
        with self.assertRaises(SnapshotError):
            self.reject_approval(data)

    def test_rejected_quality_cannot_be_promoted(self):
        data = self.generation(approve=False)
        data[-2]['status'] = data[-1]['quality_status'] = 'QUALITY_REJECTED'
        with self.assertRaises(SnapshotError):
            self.reject_approval(data)

    def test_historical_quality_cannot_auto_upgrade(self):
        data = self.generation(approve=False)
        data[-2]['status'] = data[-1]['quality_status'] = 'QUALITY_ACCEPTED'
        with self.assertRaisesRegex(SnapshotError, 'Historical quality'):
            self.reject_approval(data)

    def test_missing_required_quality_results(self):
        data = self.generation(approve=False)
        data[-2]['assessment'] = 'FROZEN_128_EVALUATED'
        with self.assertRaisesRegex(SnapshotError, 'Incomplete'):
            self.reject_approval(data)

    def test_partial_publication_preserves_previous_baseline(self):
        data = self.generation(approve=False)
        before = file_digest(self.base / 'corpus.jsonl')
        with patch.object(life.os, 'replace', side_effect=OSError('simulated publication interruption')):
            with self.assertRaises(OSError):
                self.reject_approval(data)
        self.assertFalse(data[3].exists())
        self.assertEqual(file_digest(self.base / 'corpus.jsonl'), before)

    def test_wrong_parent_candidate_rejected(self):
        data = self.generation(approve=False)
        data[-1]['parent_baseline_id'] = 'wrong'
        with self.assertRaises(SnapshotError):
            self.reject_approval(data)

    def test_failed_full_parity_blocks_approval(self):
        data = self.generation(approve=False)
        # Structurally valid independent full build of a different target state.
        (self.source / 'repos/SPHinXsys/src/a.cpp').write_text('int different() { return 99; }\n')
        other = self.root / 'other-full'
        build_full_snapshot(P, self.source, other, {**self.inputs, 'selection': selected_files(P, self.source)}, identity_scheme='portable-v1')
        data = list(data)
        data[2] = other
        with self.assertRaisesRegex(SnapshotError, 'parity'):
            self.reject_approval(data)

    def test_schema_rejected_after_transport_reseal(self):
        s1, _, _ = self.generation()
        metadata = read_json(s1 / 'baseline.json')
        metadata['schema_version'] = 999
        replace_json(s1 / 'baseline.json', metadata)
        reseal_transport(s1)
        with self.assertRaisesRegex(SnapshotError, 'schema'):
            life.verify_baseline(P, s1, self.fp)

    def test_parent_checksum_rejected_after_identity_and_transport_reseal(self):
        s1, _, _ = self.generation()
        metadata = read_json(s1 / 'baseline.json')
        metadata['identity']['parent']['package_checksum'] = '0' * 64
        metadata['baseline_id'] = 'sha256-' + digest(metadata['identity'])
        replace_json(s1 / 'baseline.json', metadata)
        reseal_transport(s1)
        with self.assertRaisesRegex(SnapshotError, 'parent'):
            life.verify_baseline(P, s1, self.fp)

    def test_generation_number_rejected(self):
        s1, _, _ = self.generation()
        metadata = read_json(s1 / 'baseline.json')
        metadata['identity']['generation'] = 3
        metadata['baseline_id'] = 'sha256-' + digest(metadata['identity'])
        replace_json(s1 / 'baseline.json', metadata)
        reseal_transport(s1)
        with self.assertRaises(SnapshotError):
            life.verify_baseline(P, s1, self.fp)

    def test_cycle_rejected(self):
        s1, _, _ = self.generation()
        metadata = read_json(s1 / 'baseline.json')
        metadata['ancestry'].append(metadata['ancestry'][0])
        replace_json(s1 / 'baseline.json', metadata)
        reseal_transport(s1)
        with self.assertRaisesRegex(SnapshotError, 'Cyclic'):
            life.verify_baseline(P, s1, self.fp)

    def test_corrupt_corpus_inventory_graph_and_provenance(self):
        s1, _, _ = self.generation()
        for name in ('corpus.jsonl', 'inventory.json', 'extraction-graph.json', 'provenance.json'):
            destination = self.root / name.replace('.', '-')
            shutil.copytree(s1, destination)
            (destination / name).write_bytes(b'corrupt')
            with self.subTest(name=name), self.assertRaises(SnapshotError):
                life.verify_baseline(P, destination, self.fp)

    def test_wrong_candidate_identity(self):
        data = self.generation(approve=False)
        manifest = read_json(data[0] / 'candidate.json')
        manifest['candidate_id'] = 'sha256-' + '0' * 64
        replace_json(data[0] / 'candidate.json', manifest)
        reseal_transport(data[0])
        with self.assertRaises(SnapshotError):
            self.reject_approval(data)

    def test_missing_and_incompatible_build_fingerprint(self):
        s1, _, _ = self.generation()
        for fingerprint in (None, '0' * 64):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(SnapshotError):
                life.verify_baseline(P, s1, fingerprint)

    def test_relocation_and_independent_process(self):
        s1, one, _ = self.generation()
        archive = self.root / 'baseline.tar.gz'
        package = life.package_baseline(P, s1, archive, self.fp)
        relocated = self.root / 'relocated'
        restored = life.restore_baseline(P, archive, relocated, self.fp, package['archive_sha256'])
        self.assertEqual(restored['baseline_id'], one['baseline_id'])
        shutil.rmtree(s1)
        run = subprocess.run([sys.executable, '-B', str(P / 'scripts/research_baseline.py'), 'build',
            '--base', str(relocated), '--source-root', str(self.source), '--output', str(self.root / 'C2'),
            '--expected-id', one['baseline_id'], '--expected-fingerprint', self.fp, '--research-only'],
            cwd=self.root, env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['summary']['actual_extraction_invocations'], 0)

    def test_transport_checksum_and_existing_output_rejected(self):
        s1, _, _ = self.generation()
        archive = self.root / 'baseline.tar.gz'
        life.package_baseline(P, s1, archive, self.fp)
        with self.assertRaises(SnapshotError):
            life.restore_baseline(P, archive, self.root / 'restore', self.fp, '0' * 64)
        with self.assertRaises(SnapshotError):
            self.generation()

    def test_standalone_baseline_needs_no_parent_corpus(self):
        s1, one, _ = self.generation()
        shutil.rmtree(self.base)
        _, two, _ = self.generation(2, s1, 'research')
        self.assertEqual(two['generation'], 2)
        self.assertEqual(two['ancestry_receipts'][1]['baseline_id'], one['baseline_id'])

    def test_rollback_uses_explicit_older_parent(self):
        s1, one, _ = self.generation()
        s2, two, _ = self.generation(2, s1, 'research')
        before = file_digest(s2 / 'baseline.json')
        _, branch, _ = self.generation(9, s1, 'research', lambda r: (r / 'repos/SPHinXsys/src/a.cpp').write_text('int rollback() { return 9; }\n'))
        self.assertEqual(branch['generation'], 2)
        self.assertEqual(branch['ancestry_receipts'][1]['baseline_id'], one['baseline_id'])
        self.assertEqual(file_digest(s2 / 'baseline.json'), before)
        self.assertNotEqual(branch['baseline_id'], two['baseline_id'])

    def test_independent_identity_and_production_prohibition(self):
        s1, one, _ = self.generation()
        s2, two, _ = self.generation(2)
        self.assertEqual(one['baseline_id'], two['baseline_id'])
        self.assertEqual(one['package_checksum'], two['package_checksum'])
        self.assertFalse(one['automatic_reuse'])
        self.assertEqual(one['production_promotion'], 'NOT_AUTHORIZED')


class FrozenQualityTests(unittest.TestCase):
    def test_exact_128_inventory_and_hit_transitions(self):
        spec = read_json(P / 'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json')
        validate_structure(spec)
        before = [{'mode': mode, 'id': qid, 'strict': {'hit_at_5': True, 'hit_at_10': True}, 'evidence_available': True}
                  for mode, qid in sorted(expected_inventory(spec))]
        after = copy.deepcopy(before)
        after[0]['strict']['hit_at_5'] = False
        after[1]['strict']['hit_at_10'] = False
        compared = compare_outcomes(before, after, spec)
        self.assertEqual(len(compared), 128)
        self.assertEqual(sum(r['hit5'] == 'PASS→FAIL' for r in compared), 1)
        self.assertEqual(sum(r['hit10'] == 'PASS→FAIL' for r in compared), 1)

    def test_removed_passage_is_separate_from_ranking(self):
        path = 'repos/SPHinXsim/docs/cli-usage.md'
        criterion = {'path': path, 'kind': 'passage', 'value': 'expected passage'}
        spec = {'questions': [{'id': 'fixture', 'requirements': [criterion]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in [('parent', 'expected passage'), ('target', 'removed text')]:
                dest = root / name / path
                dest.parent.mkdir(parents=True)
                dest.write_text(text)
            result = oracle_applicability(spec, [], [{'source_path': path, 'content': 'expected passage'}], root / 'target', root / 'parent')
            self.assertEqual(result[0]['classification'], 'SOURCE_PASSAGE_REMOVED_OR_CHANGED')
            self.assertFalse(result[0]['frozen_criterion_modified'])

    def test_policy_preserves_quality_and_production_limits(self):
        policy = frozen_policy('1' * 64, {})
        self.assertEqual(policy['historical_quality'], 'QUALITY_REVIEW_REQUIRED')
        self.assertFalse(policy['automatic_reuse'])
        self.assertEqual(policy['production_promotion'], 'NOT_AUTHORIZED')


if __name__ == '__main__':
    unittest.main()
