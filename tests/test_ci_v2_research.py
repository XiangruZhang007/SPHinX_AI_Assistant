"""P5A actual pinned pipeline and rejection gates; no Git/network writes."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

from src import ci_v2_research as c
from src.corpus_snapshot import digest, file_digest, read_json, write_json, package_snapshot
from src.corpus_snapshot_build import build_full_snapshot, selected_files
from src.research_baseline import read_parent
from src.research_quality import frozen_policy
from src.approval_evidence import replay
from test_incremental_corpus import make_sources

P=Path(__file__).resolve().parents[1]


def replace(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


class ManualResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared_tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.shared_tmp.cleanup)
        r=cls.shared=Path(cls.shared_tmp.name);cls.lock=c.read(c.LOCK);cls.fp=digest(cls.lock['P2'])
        cls.source=r/'source';make_sources(cls.source)
        inputs={'chunker_commit':cls.lock['external_chunker_commit'],'project_head':'P5A-test-byte-fixture',
                'build_contract':cls.lock['P2'],'upstreams':cls.lock['upstreams'],'selection':selected_files(P,cls.source)}
        build_full_snapshot(P,cls.source,r/'base',inputs,identity_scheme='portable-v1')
        info=read_parent(P,r/'base',cls.fp,baseline_format='p2')
        package=package_snapshot(r/'base',r/'base.tar.gz',cls.fp)
        spec=c.read(P/'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json')
        write_json(r/'history.json',replay(spec,r/'base/corpus.jsonl',r/'base/components/documentation.jsonl'))
        scope={'baseline_id':info['baseline_id'],'package_checksum':info['package_checksum'],
               'source_state':'sha256-'+digest(inputs['selection']),'upstreams':cls.lock['upstreams'],'build_fingerprint':cls.fp}
        cls.policy={'schema_version':1,'scope':scope,'profile':'pinned-research','operation':'PINNED_RESEARCH_CANDIDATE',
                    'experiment_id':'P5A-isolated-test','research_only':True,'p2_anchor_authorized':True,
                    'historical_rows_sha256':file_digest(r/'history.json'),**c.RESTRICTIONS}
        write_json(r/'policy.json',cls.policy)
        cls.ref={**scope,'baseline_kind':'p2','generation':0,'parent':None,
             'P3_fingerprint':digest(cls.lock['P3']),'P4_1_fingerprint':digest(cls.lock['P4_1']),
             'approval_identity':'P5A-isolated-test-only','P4_1_verification':'NOT_APPLICABLE_P2_ANCHOR',
             'storage_locator':'LOCAL_TEST_ONLY','transport_sha256':package['archive_sha256'],
             'policy_sha256':file_digest(r/'policy.json'),'source_provenance':'PINNED_BYTE_COPY'}

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        for n in ('base','source'):shutil.copytree(self.shared/n,self.root/n)
        for n in ('policy.json','history.json','base.tar.gz'):shutil.copy2(self.shared/n,self.root/n)
        self.reference=copy.deepcopy(self.ref);self.lock_local=copy.deepcopy(self.lock)
        self.expected=digest(self.reference);self.profile='pinned-research'

    def run_pipeline(self):
        return c.run_local(P,self.root/'base',self.root/'source',self.root/'out',self.reference,
                          self.root/'policy.json',self.expected,profile=self.profile,lock=self.lock_local,
                          historical_path=self.root/'history.json')

    def reject(self):
        with self.assertRaises((ValueError,KeyError,OSError,TypeError)):
            self.run_pipeline()
        self.assertFalse((self.root/'out/baseline.json').exists())

    def test_real_pipeline_independent_full_and_256_rows(self):
        result=self.run_pipeline()
        self.assertEqual(result['parity'],'PARITY_PASS');self.assertEqual(result['formal_acceptance'],'NOT_ASSESSED')
        self.assertEqual(result['candidate_evaluation_combinations'],128);self.assertEqual(result['full_evaluation_combinations'],128)
        self.assertEqual(result['reuse']['actual_extraction_invocations'],0)
        self.assertFalse(result['automatic_research_approval']);self.assertFalse(result['research_baseline_created'])
        self.assertEqual(result['production_promotion'],'NOT_AUTHORIZED')
        c.package_candidate(self.root/'out',self.root/'candidate.tar.gz')
        self.assertTrue((self.root/'candidate.tar.gz').is_file())

    def test_restore_independent_process_and_no_original_root(self):
        ref=self.root/'reference.json';write_json(ref,self.reference)
        run=subprocess.run([sys.executable,'-B',str(P/'scripts/ci_v2_manual_research.py'),'restore',
           '--archive',str(self.root/'base.tar.gz'),'--destination',str(self.root/'restored'),
           '--reference',str(ref),'--expected-reference-sha256',self.expected],cwd=self.root,
           env={**os.environ,'PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertEqual(json.loads(run.stdout)['baseline_id'],self.reference['baseline_id'])
        shutil.rmtree(self.root/'base')
        self.assertEqual(c.verify_baseline(P,self.root/'restored',self.reference,self.lock)['baseline_id'],self.reference['baseline_id'])

    def test_wrong_initial_checkpoint(self):
        with self.assertRaises(c.GateError):c.initial_checkpoint('0'*40,'ci-upstream-refresh','',self.lock)

    def test_dirty_initial_worktree(self):
        with self.assertRaises(c.GateError):c.initial_checkpoint(self.lock['starting_checkpoint'],'ci-upstream-refresh',' M x',self.lock)

    def test_invalid_p41_fingerprint(self):
        self.lock_local['P4_1']['schema_version']=99;self.reject()

    def test_wrong_runtime(self):
        self.lock_local['quality_policy']['R2_fingerprint']['inputs']['runtime']['python']='0.0.0';self.reject()

    def test_missing_registry_baseline(self):
        with self.assertRaisesRegex(c.GateError,'No reviewed trusted'):c.reference({'schema_version':1,'baselines':{}},'missing')

    def test_missing_baseline_directory(self):
        shutil.rmtree(self.root/'base');self.reject()

    def test_invalid_baseline_checksum(self):
        (self.root/'base/corpus.jsonl').write_text('corrupt\n');self.reject()

    def test_reference_and_download_cannot_self_authorize(self):
        self.reference['baseline_id']='forged';self.reject()

    def test_p2_not_p41_approved(self):
        self.reference['P4_1_verification']='P4_1_VERIFIED';self.expected=digest(self.reference);self.reject()

    def test_p2_requires_explicit_bootstrap(self):
        policy=c.read(self.root/'policy.json');policy.pop('p2_anchor_authorized');replace(self.root/'policy.json',policy)
        self.reference['policy_sha256']=file_digest(self.root/'policy.json');self.expected=digest(self.reference);self.reject()

    def test_legacy_p4_not_p41(self):
        self.reference['baseline_kind']='research';self.reference['P4_1_verification']='P4_1_NOT_VERIFIED'
        self.expected=digest(self.reference);self.reject()

    def test_s4_cannot_be_relabeled_real(self):
        self.reference['source_provenance']='PINNED_UPSTREAM';self.expected=digest(self.reference);self.reject()

    def test_changed_upstream_pair_blocked(self):
        self.reference['upstreams']['SPHinXsim']='f'*40;self.expected=digest(self.reference);self.reject()

    def test_formal_live_profile_blocked(self):
        self.profile='formal';self.reject()

    def test_source_bytes_must_match_authorized_anchor(self):
        (self.root/'source/repos/SPHinXsys/src/a.cpp').write_text('int a(){return 99;}\n');self.reject()

    def test_wrong_parent_lineage(self):
        self.reference['parent']={'baseline_id':'wrong'};self.expected=digest(self.reference);self.reject()

    def test_required_document_missing(self):
        (self.root/'source/repos/SPHinXsim/docs/cli-usage.md').unlink();self.reject()

    def test_selected_file_manifest_corrupt(self):
        (self.root/'base/selection.json').write_text('[]\n');self.reject()

    def test_inventory_offsets_corrupt(self):
        rows=c.read(self.root/'base/inventory.json');rows[0]['record_start']=999
        replace(self.root/'base/inventory.json',rows);self.reject()

    def test_native_graph_corrupt(self):
        (self.root/'base/extraction-graph.json').write_text('[]\n');self.reject()

    def test_historical_rows_missing(self):
        (self.root/'history.json').unlink();self.reject()

    def test_historical_rows_checksum_tamper(self):
        rows=c.read(self.root/'history.json');rows.pop();replace(self.root/'history.json',rows);self.reject()

    def test_authorization_cannot_approve_candidate(self):
        policy=c.read(self.root/'policy.json');policy['automatic_research_approval']=True
        replace(self.root/'policy.json',policy);self.reference['policy_sha256']=file_digest(self.root/'policy.json');self.expected=digest(self.reference);self.reject()

    def test_authorization_cannot_update_approved_pointer(self):
        policy=c.read(self.root/'policy.json');policy['operation']='UPDATE_APPROVED_POINTER'
        replace(self.root/'policy.json',policy);self.reference['policy_sha256']=file_digest(self.root/'policy.json');self.expected=digest(self.reference);self.reject()

    def test_authorization_cannot_promote_production(self):
        policy=c.read(self.root/'policy.json');policy['production_promotion']='AUTHORIZED'
        replace(self.root/'policy.json',policy);self.reference['policy_sha256']=file_digest(self.root/'policy.json');self.expected=digest(self.reference);self.reject()

    def test_diagnostic_cannot_claim_formal_pass(self):
        result=c.decision('diagnostic',{k:True for k in ('runtime','baseline','provenance','independent_full','four_artifacts','candidate_128','full_128','execution')})
        self.assertEqual(result['formal_acceptance'],'NOT_ASSESSED')

    def test_self_declared_formal_completion_rejected(self):
        with self.assertRaises(c.GateError):c.decision('formal',{k:True for k in ('runtime','baseline','provenance','independent_full','four_artifacts','candidate_128','full_128','execution')})

    def test_corrupted_transport(self):
        with self.assertRaises(c.GateError):c.restore(P,self.root/'base.tar.gz',self.root/'restore',
            {**self.reference,'transport_sha256':'0'*64},self.lock)

    def test_path_traversal_archive(self):
        target=self.root/'unsafe.tar.gz'
        with tarfile.open(target,'w:gz') as tar:
            item=tarfile.TarInfo('../escape');item.size=1;tar.addfile(item,io.BytesIO(b'x'))
        with self.assertRaises(c.GateError):c.check_archive(target,file_digest(target))

    def test_symlink_archive(self):
        target=self.root/'unsafe.tar.gz'
        with tarfile.open(target,'w:gz') as tar:
            item=tarfile.TarInfo('link');item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd';tar.addfile(item)
        with self.assertRaises(c.GateError):c.check_archive(target,file_digest(target))

    def test_unsafe_manual_inputs(self):
        for profile,key,revs in [('pinned-research','../../secret',self.lock['upstreams']),
           ('approve','safe',self.lock['upstreams']),('pinned-research','safe',{'SPHinXsys':'main','SPHinXsim':'$(id)'})]:
            with self.subTest(key=key),self.assertRaises(c.GateError):c.validate_request(profile,key,revs)

    def test_untrusted_download_url(self):
        with self.assertRaises(c.GateError):c.download('https://example.com/fake',self.root/'download','0'*64)

    def test_missing_expired_download_reports_storage_failure(self):
        # Public URL is not requested: missing registry identity prevents fallback.
        with self.assertRaises(c.GateError):c.reference({'schema_version':1,'baselines':{}},'expired')

    def test_workflow_failure_and_upload_contract(self):
        import yaml
        doc=yaml.safe_load((P/'.github/workflows/ci_v2_manual_research.yml').read_text())
        triggers=doc.get('on',doc.get(True))
        self.assertEqual(set(triggers),{'workflow_dispatch'})
        self.assertEqual(doc['permissions'],{'contents':'read'})
        steps=doc['jobs']['candidate']['steps'];upload=next(s for s in steps if s.get('id')=='evidence')
        self.assertEqual(upload['if'],'always()');self.assertNotIn('continue-on-error',upload)
        self.assertEqual(upload['with']['if-no-files-found'],'error')
        self.assertEqual(upload['with']['retention-days'],30)
        for s in steps:
            if 'uses' in s:self.assertRegex(s['uses'],r'^actions/[a-z-]+@[0-9a-f]{40}$')

    def test_plan_cli_returns_explicit_bootstrap_blocker(self):
        out=self.root/'plan'
        run=subprocess.run([sys.executable,'-B',str(P/'scripts/ci_v2_manual_research.py'),'plan',
            '--key','unconfigured','--profile','pinned-research','--sys-revision',self.lock['upstreams']['SPHinXsys'],
            '--sim-revision',self.lock['upstreams']['SPHinXsim'],'--output',str(out)],cwd=self.root,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
        self.assertEqual(run.returncode,2);self.assertEqual(json.loads(run.stdout)['status'],'BOOTSTRAP_REQUIRED')
        self.assertTrue((out/'failure.json').is_file())

    def test_artifact_member_corruption_blocks_packaging(self):
        self.run_pipeline();(self.root/'out/candidate/corpus.jsonl').write_text('corrupt\n')
        with self.assertRaises(c.GateError):c.package_candidate(self.root/'out',self.root/'bad.tar.gz')

    def test_local_upload_failure_preserves_existing_output(self):
        self.run_pipeline();(self.root/'exists.tar.gz').write_bytes(b'previous artifact')
        with self.assertRaises(FileExistsError):c.package_candidate(self.root/'out',self.root/'exists.tar.gz')
        self.assertEqual((self.root/'exists.tar.gz').read_bytes(),b'previous artifact')

    def verify_outputs(self,quality=None):
        return c.verify_evaluated_pair(P,self.root/'out/candidate',self.root/'out/full',self.root/'out/quality',
                  quality or c.read(self.root/'out/quality/quality-report.json'),self.lock,self.root/'proof-negative')

    def test_missing_candidate_retrieval_rows(self):
        self.run_pipeline();(self.root/'out/quality/incremental-raw-results.json').unlink()
        with self.assertRaises((OSError,ValueError)):self.verify_outputs()

    def test_missing_full_reference_rows(self):
        self.run_pipeline();(self.root/'out/quality/full-raw-results.json').unlink()
        with self.assertRaises((OSError,ValueError)):self.verify_outputs()

    def test_incomplete_128_evaluation(self):
        self.run_pipeline();p=self.root/'out/quality/incremental-raw-results.json';rows=c.read(p);rows.pop();replace(p,rows)
        with self.assertRaises(ValueError):self.verify_outputs()

    def test_duplicate_evaluation_combination(self):
        self.run_pipeline();p=self.root/'out/quality/incremental-raw-results.json';rows=c.read(p);rows.append(rows[0]);replace(p,rows)
        with self.assertRaises(ValueError):self.verify_outputs()

    def test_unsupported_scoring_operator(self):
        from src.retrieval_evaluation_io import validate_structure
        spec=c.read(P/'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json');spec['questions'][0]['evidence_mode']='ANY_OF'
        with self.assertRaisesRegex(ValueError,'Unsupported evidence operator'):validate_structure(spec)

    def test_failed_retrieval_execution_jointly_forged_scores(self):
        self.run_pipeline();quality=c.read(self.root/'out/quality/quality-report.json')
        for n in ['incremental','full']:
            path=self.root/'out/quality'/(n+'-raw-results.json');rows=c.read(path)
            item=next(r for r in rows if r['top_10'])['top_10'][0]
            item['score']+=1;item['score_hex']=item['score'].hex();replace(path,rows)
        quality['raw_results_sha256']=file_digest(self.root/'out/quality/incremental-raw-results.json')
        with self.assertRaisesRegex(c.GateError,'Independent frozen R2 replay'):self.verify_outputs(quality)

    def test_bad_frozen_strict_scores(self):
        self.run_pipeline();p=self.root/'out/quality/incremental-raw-results.json';rows=c.read(p)
        rows[0]['strict']['hit_at_5']=not rows[0]['strict']['hit_at_5'];replace(p,rows)
        with self.assertRaises(ValueError):self.verify_outputs()

    def test_independent_full_valid_but_wrong_target(self):
        self.run_pipeline();(self.root/'source/repos/SPHinXsys/src/a.cpp').write_text('int changed(){return 9;}\n')
        inputs={'chunker_commit':self.lock['external_chunker_commit'],'project_head':'negative-only',
                'build_contract':self.lock['P2'],'upstreams':self.lock['upstreams'],'selection':selected_files(P,self.root/'source')}
        build_full_snapshot(P,self.root/'source',self.root/'other-full',inputs,identity_scheme='portable-v1')
        from src.research_baseline import compare_full
        with self.assertRaisesRegex(ValueError,'parity'):compare_full(P,self.root/'out/candidate',self.root/'other-full',self.fp)

    def test_fake_git_checkout_identity_rejected(self):
        with self.assertRaises(subprocess.CalledProcessError):
            c.verify_sources(P,self.root/'source',self.root/'base',self.lock['upstreams'],git_checkout=True)

    def test_restored_executable_source_materialized_from_verified_modes(self):
        source=self.root/'source';(source/'repos/SPHinXsys/src/a.cpp').chmod(0o755)
        inputs={'chunker_commit':self.lock['external_chunker_commit'],'project_head':'executable-restore-fixture',
                'build_contract':self.lock['P2'],'upstreams':self.lock['upstreams'],'selection':selected_files(P,source)}
        base=self.root/'executable-base';build_full_snapshot(P,source,base,inputs,identity_scheme='portable-v1')
        info=read_parent(P,base,self.fp,baseline_format='p2')
        ref={**self.reference,'baseline_id':info['baseline_id'],'package_checksum':info['package_checksum'],
             'source_state':'sha256-'+digest(inputs['selection'])}
        package_snapshot(base,self.root/'exec.tar.gz',self.fp);ref['transport_sha256']=file_digest(self.root/'exec.tar.gz')
        c.restore(P,self.root/'exec.tar.gz',self.root/'restored-exec',ref,self.lock)
        before=file_digest(self.root/'restored-exec/checksums.json')
        result=c.materialize_anchor(P,self.root/'restored-exec',self.root/'materialized',ref,self.lock)
        self.assertEqual(result['executable_files'],1)
        self.assertTrue((self.root/'materialized/repos/SPHinXsys/src/a.cpp').stat().st_mode & 0o111)
        self.assertEqual(selected_files(P,self.root/'materialized'),inputs['selection'])
        self.assertEqual(before,file_digest(self.root/'restored-exec/checksums.json'))

    def test_materialization_never_overwrites_existing_source(self):
        with self.assertRaises(c.GateError):c.materialize_anchor(P,self.root/'base',self.root/'source',self.reference,self.lock)

    def test_isolated_python_cannot_fake_hash_seed_via_environment(self):
        code=('import sys;sys.path.insert(0,'+repr(str(P))+');'
              'from src.ci_v2_research import frozen_runtime;frozen_runtime()')
        run=subprocess.run([sys.executable,'-I','-B','-c',code],env={**os.environ,'PYTHONHASHSEED':'0'},
                           capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('Actual hash seed is randomized',run.stderr)


if __name__=='__main__':unittest.main()
