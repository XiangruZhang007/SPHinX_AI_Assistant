"""Real frozen-128/R2 approval tests; small corpora, no mocked ranker/scorer."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from src import research_baseline as life
from src import approval_evidence as ev
from src.corpus_snapshot import SnapshotError, digest, file_digest, read_json, read_records
from src.corpus_snapshot_build import build_full_snapshot, selected_files
from src.incremental_corpus import build_incremental, current_contract, verify_candidate
from src.research_quality import frozen_policy, review_not_evaluated, evaluate_quality
from src.retrieval_identity_diagnostics import retrieval_fingerprint
from src.retrieval_evaluation_io import validate_structure
from src.retrieval_evaluation_criteria import outcome
from test_incremental_corpus import make_sources
from test_research_baseline import reseal_transport

P = Path(__file__).resolve().parents[1]


def write_json(path, value):
    # Deliberate adversarial mutations apply only to disposable test fixtures.
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


class ApprovalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_shared.cleanup)
        r = cls.shared = Path(cls.temp_shared.name)
        source = r / 'source'; make_sources(source)
        contract = current_contract(P); cls.fp = digest(contract)
        inputs = {'chunker_commit': contract['external_chunker_commit'], 'project_head': 'P41-real-fixture',
                  'build_contract': contract, 'upstreams': {'SPHinXsys': 'a'*40, 'SPHinXsim': 'b'*40},
                  'selection': selected_files(P, source)}
        build_full_snapshot(P, source, r / 'parent', inputs, identity_scheme='portable-v1')
        cls.parent_report = life.read_parent(P, r / 'parent', cls.fp, baseline_format='p2')
        cls.spec = read_json(P / ev.BENCHMARK)
        # Test uses a real frozen runtime contract; retrieval itself is unchanged.
        r2 = retrieval_fingerprint(P, {'exclude_exact_paths': ['parent_context.original_function_chunk_id']}, 1.5, .75)
        cls.quality_policy = frozen_policy(ev.BENCHMARK_SHA, r2)
        reference = ev.replay(cls.spec, r / 'parent/corpus.jsonl', r / 'parent/components/documentation.jsonl')
        evidence = r / 'raw'; evidence.mkdir()
        for n in ev.RAW_FILES[2:]:write_json(evidence / n, reference)
        cls.policy = {'schema_version': 1, 'mode': 'RETRIEVAL_EVALUATED',
            'scope': {'experiment_id': 'P41-frozen-fixture', 'parent_baseline_id': cls.parent_report['baseline_id'],
                      'target_source_state': 'sha256-' + digest(selected_files(P, source)),
                      'build_fingerprint': cls.fp, 'quality_policy_sha256': digest(cls.quality_policy)},
            'reference_rows_sha256': {n: file_digest(evidence / n) for n in ev.RAW_FILES[2:]},
            'quality_status': 'QUALITY_REVIEW_REQUIRED', 'automatic_reuse': False, 'production_promotion': 'NOT_AUTHORIZED'}
        write_json(r / 'policy.json', cls.policy)  # before candidate execution
        structural = copy.deepcopy(cls.policy)
        structural['mode'] = 'STRUCTURAL_ONLY'
        structural['scope']['experiment_id'] = 'P41-structural-fixture'
        write_json(r / 'structural-policy.json', structural)
        built = build_incremental(P, r / 'parent', source, r / 'candidate', expected_fingerprint=cls.fp,
                                  research_only=True, expected_snapshot_id=cls.parent_report['baseline_id'])
        build_full_snapshot(P, source, r / 'full', inputs, identity_scheme='portable-v1')
        cls.quality = evaluate_quality(P, r / 'candidate', r / 'full', r / 'evaluation',
             benchmark=P / ev.BENCHMARK, policy=cls.quality_policy, expected_fingerprint=cls.fp,
             historical_rows=reference, parent_rows=reference, parent_corpus=r / 'parent/corpus.jsonl', parent_sources=source)
        for n in ev.RAW_FILES[:2]:shutil.copy2(r / 'evaluation' / n, evidence / n)
        write_json(evidence / 'inputs.json', ev.input_manifest(r / 'candidate', r / 'full', cls.quality, file_digest(r / 'policy.json'), evidence))
        cls.auth = {'schema_version': 1, 'approved': True, 'research_only': True, 'candidate_id': built['candidate_id'],
                    'parent_baseline_id': cls.parent_report['baseline_id'], 'experiment_id': 'P41-frozen-fixture',
                    'quality_status': 'QUALITY_REVIEW_REQUIRED', 'allow_quality_review': True}

    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root = Path(t.name)
        for n in ('candidate', 'parent', 'full', 'raw'):shutil.copytree(self.shared / n, self.root / n)
        shutil.copy2(self.shared / 'policy.json', self.root / 'policy.json')
        self.policy_sha = file_digest(self.root / 'policy.json')
        self.q, self.auth = copy.deepcopy(self.quality), copy.deepcopy(self.__class__.auth)
        self.evidence = self.root / 'raw'

    def approve(self, **kwargs):
        return life.approve_baseline(P, self.root / 'candidate', self.root / 'parent', self.root / 'full',
               self.root / 'approved', expected_fingerprint=self.fp, parent_format='p2',
               quality=self.q, authorization=self.auth, approval_policy=self.root / 'policy.json',
               expected_policy_sha256=self.policy_sha, evidence=self.evidence, **kwargs)

    def reject(self):
        with self.assertRaises((ValueError, KeyError, OSError, TypeError, IndexError)):
            self.approve()
        self.assertFalse((self.root / 'approved').exists())

    def modify_rows(self, callback, reseal=False):
        for n in ev.RAW_FILES[:2]:
            rows = read_json(self.evidence / n); callback(rows); write_json(self.evidence / n, rows)
        if reseal:
            self.q['raw_results_sha256'] = file_digest(self.evidence / ev.RAW_FILES[0])
            write_json(self.evidence / 'inputs.json', ev.input_manifest(self.root / 'candidate', self.root / 'full',
                       self.q, self.policy_sha, self.evidence))

    def test_valid_evaluated_approval_and_distinct_statuses(self):
        before = file_digest(self.root / 'candidate/candidate.json')
        result = self.approve()
        self.assertEqual(result['P4_1_verification'], 'P4_1_VERIFIED')
        self.assertEqual(result['incremental_parity'], 'NOT_ASSESSED')
        self.assertEqual(result['research_baseline_parity'], 'PARITY_PASS')
        self.assertEqual(before, file_digest(self.root / 'candidate/candidate.json'))
        self.assertFalse(result['automatic_reuse'])
        self.assertEqual(result['production_promotion'], 'NOT_AUTHORIZED')
        self.assertEqual(read_json(self.root / 'approved/approval-evidence/verification.json')['execution'], 'INDEPENDENT_R2_REPLAY_VERIFIED')

    def test_bundle_relocated_fresh_replay(self):
        self.approve(); original = self.root / 'approved/approval-evidence'; h = file_digest(original / 'checksums.json')
        moved = self.root / 'moved'; shutil.copytree(original, moved)
        shutil.rmtree(self.root / 'approved'); shutil.rmtree(self.evidence)
        result = ev.verify_bundle(P, moved, expected_manifest_sha=h, fresh_replay=True)
        self.assertEqual(result['inspection'], 'FRESH_REPLAY')

    def test_forged_complete_without_evidence(self):
        self.evidence = None; self.q['evaluation_status'] = 'COMPLETE'; self.reject()

    def test_missing_raw_file(self):
        (self.evidence / ev.RAW_FILES[0]).unlink(); self.reject()

    def test_missing_key(self):
        self.modify_rows(lambda rows: rows.pop(), True); self.reject()

    def test_duplicate_key(self):
        self.modify_rows(lambda rows: rows.append(copy.deepcopy(rows[0])), True); self.reject()

    def test_unexpected_key(self):
        self.modify_rows(lambda rows: rows[0].update(id='unfrozen-question'), True); self.reject()

    def test_original_raw_checksum_mismatch(self):
        self.modify_rows(lambda rows: rows[0].update(question='forged')); self.reject()

    def test_correct_id_altered_content(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10'])['top_10'][0]['record'].update(content='forged'), True); self.reject()

    def test_correct_id_altered_source_metadata(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10'])['top_10'][0]['record'].update(start_line=999), True); self.reject()

    def test_record_numeric_type_forgery_is_not_canonical(self):
        def change(rows):
            r=next(r for r in rows if r['top_10'])['top_10'][0]['record']
            r['start_line']=float(r['start_line'])
        self.modify_rows(change,True);self.reject()

    def test_absent_canonical_record(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10'])['top_10'][0].update(chunk_id='absent'), True); self.reject()

    def test_altered_display_metadata(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10'])['top_10'][0]['metadata'].update(source_role='forged'), True); self.reject()

    def test_self_consistent_score_forgery_replay_rejects(self):
        def change(rows):
            x = next(r for r in rows if r['top_10'])['top_10'][0]
            x['score'] += .125; x['score_hex'] = x['score'].hex()
        self.modify_rows(change, True)
        with self.assertRaisesRegex(SnapshotError, 'Independent R2 replay'):
            self.approve()

    def test_score_hex_forgery(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10'])['top_10'][0].update(score_hex='0x1.0p+0'), True); self.reject()

    def test_self_consistent_ranking_forgery_replay_rejects(self):
        def change(rows):
            for row in rows:
                if len(row['top_10']) >= 2 and not any(row['strict']['evidence_ranks']):
                    row['top_10'][0], row['top_10'][1] = row['top_10'][1], row['top_10'][0]
                    for i,x in enumerate(row['top_10'],1):x['rank']=i
                    row['top_10_ids'] = [x['chunk_id'] for x in row['top_10']];row['top_5_ids']=row['top_10_ids'][:5]
                    return
            self.fail('fixture needs two real retrieved records')
        self.modify_rows(change, True)
        with self.assertRaisesRegex(SnapshotError, 'Independent R2 replay'):
            self.approve()

    def test_bad_top5_prefix(self):
        self.modify_rows(lambda rows: next(r for r in rows if r['top_10']).update(top_5_ids=[]), True); self.reject()

    def test_hit5_forgery(self):
        self.modify_rows(lambda rows: rows[0]['strict'].update(hit_at_5=not rows[0]['strict']['hit_at_5']), True); self.reject()

    def test_hit10_forgery(self):
        self.modify_rows(lambda rows: rows[0]['strict'].update(hit_at_10=not rows[0]['strict']['hit_at_10']), True); self.reject()

    def test_denominator_forgery(self):
        self.q['metrics']['combined_primary_all']['n'] = 80; self.reject()

    def test_transition_forgery(self):
        self.q['new_losses']['historical'].append({'id':'forged'}); self.reject()

    def test_source_diagnostics_forgery(self):
        self.q['applicability_changes'].append({'id':'forged'}); self.reject()

    def test_wrong_candidate_id(self):
        self.q['candidate_id'] = self.auth['candidate_id'] = 'sha256-'+'0'*64; self.reject()

    def test_wrong_corpus_sha(self):
        self.q['corpus_sha256'] = '0'*64; self.reject()

    def test_wrong_parent_id(self):
        self.auth['parent_baseline_id'] = 'wrong'; self.reject()

    def test_wrong_source_state_binding(self):
        m=read_json(self.evidence/'inputs.json');m['bindings']['target_source_state']='wrong';write_json(self.evidence/'inputs.json',m);self.reject()

    def test_full_reference_association_mismatch(self):
        m=read_json(self.evidence/'inputs.json');m['bindings']['full_snapshot_id']='wrong';write_json(self.evidence/'inputs.json',m);self.reject()

    def test_frozen_benchmark_identity_mismatch(self):
        self.q['policy']['benchmark_sha256']='0'*64;self.q['policy_sha256']=digest(self.q['policy']);self.reject()

    def test_r2_identity_mismatch(self):
        self.q['policy']['R2_fingerprint']['fingerprint']='0'*64;self.q['policy_sha256']=digest(self.q['policy']);self.reject()

    def test_evaluator_identity_mismatch(self):
        self.q['policy']['evaluation_implementation']['src/remediation_evaluation.py']='0'*64;self.q['policy_sha256']=digest(self.q['policy']);self.reject()

    def test_reference_raw_anchor_changed_with_local_checksums(self):
        rows=read_json(self.evidence/ev.RAW_FILES[2]); rows[0]['strict']['hit_at_5']=not rows[0]['strict']['hit_at_5']
        write_json(self.evidence/ev.RAW_FILES[2],rows)
        write_json(self.evidence/'inputs.json',ev.input_manifest(self.root/'candidate',self.root/'full',self.q,self.policy_sha,self.evidence));self.reject()

    def test_cannot_downgrade_failed_evaluation_to_structural(self):
        self.q['assessment']='RECORDED_REVIEW_NOT_EVALUATED';self.evidence=None;self.reject()

    def test_missing_external_policy(self):
        (self.root/'policy.json').unlink();self.reject()

    def test_external_policy_tamper(self):
        p=read_json(self.root/'policy.json');p['mode']='STRUCTURAL_ONLY';write_json(self.root/'policy.json',p);self.reject()

    def test_policy_scope_substitution(self):
        p=read_json(self.root/'policy.json');p['scope']['experiment_id']='other';write_json(self.root/'policy.json',p);self.policy_sha=file_digest(self.root/'policy.json');self.reject()

    def test_frozen_all_of_and_individual_requirements(self):
        validate_structure(self.spec)
        self.assertEqual({q['evidence_mode'] for q in self.spec['questions']},{'ALL_OF'})
        q=next(q for q in self.spec['questions'] if q['id']=='DOC-Q01')
        records=[{'source_path':c['path'],'source_role':c['role'],'content':'','section_title':c['value']} for c in q['requirements']]
        self.assertTrue(outcome(records,q['requirements'])['hit_at_5'])
        incomplete=outcome(records[:1],q['requirements']);self.assertFalse(incomplete['hit_at_5']);self.assertEqual(len(incomplete['evidence_ranks']),2)

    def test_unsupported_and_forged_any_of_rejected(self):
        for op in ('ANY_OF','OR','unknown',None):
            s=copy.deepcopy(self.spec);s['questions'][0]['evidence_mode']=op
            with self.subTest(operator=op),self.assertRaisesRegex(ValueError,'Unsupported evidence operator'):validate_structure(s)

    def test_external_bundle_pin_blocks_resealed_forgery(self):
        self.approve();root=self.root/'approved/approval-evidence';h=file_digest(root/'checksums.json')
        q=read_json(root/'quality.json');q['metrics']['combined_primary_all']['n']=1;write_json(root/'quality.json',q)
        (root/'checksums.json').unlink();ev.seal(root)
        with self.assertRaisesRegex(SnapshotError,'External bundle checksum'):ev.verify_bundle(P,root,expected_manifest_sha=h)

    def legacy_fixture(self):
        """Synthetic original-format package; real archive checked separately."""
        self.approve(); root=self.root/'approved'
        shutil.rmtree(root/'approval-evidence')
        metadata=read_json(root/'baseline.json');identity=metadata['identity'];identity.pop('P4_1_evidence_sha256')
        payload={p.relative_to(root).as_posix():file_digest(p) for p in sorted(root.rglob('*'))
                 if p.is_file() and p.name not in ('checksums.json','baseline.json')}
        identity['payload_sha256']=digest(payload);metadata['baseline_id']='sha256-'+digest(identity)
        write_json(root/'baseline.json',metadata);reseal_transport(root)
        return root,life.verify_baseline(P,root,self.fp,metadata['baseline_id'])

    def test_legacy_readable_unverified_and_relocated_identity_unchanged(self):
        root,before=self.legacy_fixture();shutil.copytree(root,self.root/'legacy-relocated')
        after=life.verify_baseline(P,self.root/'legacy-relocated',self.fp,before['baseline_id'])
        self.assertEqual(before,after);self.assertEqual(after['P4_1_verification'],'P4_1_NOT_VERIFIED')
        self.assertEqual(before['package_checksum'],after['package_checksum'])

    def test_legacy_revalidation_produces_sidecar_without_modification(self):
        root,before=self.legacy_fixture();h=file_digest(root/'checksums.json')
        result=life.revalidate_legacy(P,root,self.root/'parent',self.root/'full',self.root/'sidecar',
             expected_fingerprint=self.fp,expected_id=before['baseline_id'],parent_format='p2',quality=self.q,
             authorization=self.auth,approval_policy=self.root/'policy.json',expected_policy_sha256=self.policy_sha,
             evidence=self.evidence)
        self.assertEqual(result['status'],'P4_1_VERIFIED');self.assertEqual(file_digest(root/'checksums.json'),h)
        self.assertEqual(result['historical_subject']['baseline_id'],before['baseline_id'])
        self.assertEqual(life.verify_baseline(P,root,self.fp)['P4_1_verification'],'P4_1_NOT_VERIFIED')

    def test_missing_legacy_evidence_cannot_claim_success(self):
        root,before=self.legacy_fixture()
        with self.assertRaisesRegex(SnapshotError,'Missing original raw'):
            life.revalidate_legacy(P,root,self.root/'parent',self.root/'full',self.root/'sidecar',
                expected_fingerprint=self.fp,expected_id=before['baseline_id'],parent_format='p2',quality=self.q,
                authorization=self.auth,approval_policy=self.root/'policy.json',expected_policy_sha256=self.policy_sha)
        self.assertFalse((self.root/'sidecar').exists())

    def structural_setup(self):
        shutil.copy2(self.shared/'structural-policy.json',self.root/'policy.json')
        self.policy_sha=file_digest(self.root/'policy.json');self.auth['experiment_id']='P41-structural-fixture'
        self.q=review_not_evaluated(self.root/'candidate',self.fp,self.quality_policy);self.evidence=None

    def test_explicit_structural_mode_no_quality_or_production_upgrade(self):
        self.structural_setup();result=self.approve()
        v=read_json(self.root/'approved/approval-evidence/verification.json')
        self.assertEqual(v['execution'],'NOT_EVALUATED');self.assertEqual(v['quality_status'],'QUALITY_REVIEW_REQUIRED')
        self.assertEqual(result['research_baseline_parity'],'PARITY_PASS');self.assertFalse(result['automatic_reuse'])
        self.assertEqual(result['production_promotion'],'NOT_AUTHORIZED')

    def test_structural_policy_rejects_attempted_evaluation_evidence(self):
        self.structural_setup();self.evidence=self.root/'raw';self.reject()

    def test_quality_policy_cannot_allow_general_legacy_bypass(self):
        p=read_json(self.root/'policy.json');p['allow_legacy']=True
        write_json(self.root/'policy.json',p);h=file_digest(self.root/'policy.json')
        legacy={**self.parent_report,'generation':1,'P4_1_verification':'P4_1_NOT_VERIFIED'}
        with self.assertRaisesRegex(SnapshotError,'exact externally frozen'):
            ev.external_policy(self.root/'policy.json',h,candidate=self.root/'candidate',parent=legacy,
                               quality=self.q,authorization=self.auth)


if __name__=='__main__':unittest.main()
