"""Synthetic Git objects are CONTROLLED_GIT_FIXTURE, never published upstreams."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from src import upstream_provenance as u
from src import incremental_corpus as inc
from src.corpus_snapshot import digest, file_digest, read_json, write_json, SnapshotError
from src.corpus_snapshot_build import build_full_snapshot
from src.research_quality import review_not_evaluated
from test_incremental_corpus import make_sources

P = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'user.name=Research Fixture', '-c', 'user.email=fixture@example.invalid',
                                   '-c', 'core.autocrlf=false', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()


def commit(root):
    git(root, 'add', '.'); git(root, 'commit', '-m', 'Synthetic research fixture')
    return git(root, 'rev-parse', 'HEAD')


def policy_for(parent, base_source, target_source, revisions, fp, *, fixture=None, publications=None, genuine=False):
    policy = {'schema_version': 2, 'operation': 'BUILD_CANDIDATE', 'research_only': True,
              'experiment_id': 'P5A.1-controlled-test', 'p2_anchor_authorized': True,
              'provenance_type': 'GENUINE_UPSTREAM' if genuine else 'CONTROLLED_GIT_FIXTURE', **u.LIMITS}
    if fixture is not None: policy['controlled_chain_fixture'] = fixture
    if publications: policy['publications'] = publications
    receipt, _, old_selection, _ = u._parent(P, parent, fp, policy, base_source)
    selection, _ = u.verify_sources(P, target_source, revisions)
    policy['scope'] = {'parent': u._edge(receipt), 'base_source_state': u.state(old_selection),
          'target_source_state': u.state(selection), 'base_upstreams': receipt['identity']['upstreams'],
          'target_upstreams': revisions, 'build_fingerprint': fp, 'orchestration_fingerprint': digest(u.contract(P))}
    return policy


def replace(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


class UpstreamProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.tmp.cleanup)
        cls.shared = Path(cls.tmp.name); source = cls.shared / 'source'; make_sources(source)
        cls.revisions = {}
        for repo, url in u.REPOSITORIES.items():
            root = source / 'repos' / repo
            git(root, 'init'); git(root, 'remote', 'add', 'origin', url)
            cls.revisions[repo] = commit(root)
        cls.fp = digest(inc.current_contract(P)); cls.contract = inc.current_contract(P)
        selection, _ = u.verify_sources(P, source, cls.revisions)
        build_full_snapshot(P, source, cls.shared / 'base', {'chunker_commit': cls.contract['external_chunker_commit'],
            'project_head': 'CONTROLLED_GIT_FIXTURE', 'build_contract': cls.contract,
            'upstreams': cls.revisions, 'selection': selection}, identity_scheme='portable-v1')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        for name in ('source', 'base'): shutil.copytree(self.shared / name, self.root / name)
        shutil.copytree(self.root / 'source', self.root / 'target')
        self.base = self.root / 'base'; self.source = self.root / 'source'; self.target = self.root / 'target'
        self.parent = self.base; self.revs = dict(self.revisions); self.fixture = None

    def update(self, repo='SPHinXsys', name='src/a.cpp', raw=b'int a() { return 21; }\n'):
        root = self.target / 'repos' / repo; path = root / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        self.revs[repo] = commit(root)

    def prepare(self):
        policy = policy_for(self.parent, self.source, self.target, self.revs, self.fp, fixture=self.fixture)
        replace(self.root / 'policy.json', policy)
        return policy

    def build(self, name='out', policy=None):
        if policy is None: policy = self.prepare()
        else: replace(self.root / 'policy.json', policy)
        return u.build(P, self.parent, self.source, self.target, self.root / name, policy_path=self.root / 'policy.json',
                       expected_policy_sha256=file_digest(self.root / 'policy.json'), expected_fingerprint=self.fp)

    def reject(self, policy):
        with self.assertRaises((ValueError, KeyError, OSError, TypeError, subprocess.CalledProcessError)):
            self.build(policy=policy)
        self.assertFalse((self.root / 'out').exists())

    def parity(self):
        u.full_reference(P, self.target, self.root / 'full', self.revs, self.fp)
        self.assertEqual(u.compare_full(P, self.root / 'out', self.root / 'full', self.fp)['status'], 'PARITY_PASS')

    def test_unchanged_git_pair_zero_extraction(self):
        r = self.build(); self.assertEqual(r['generation'], 1)
        self.assertEqual(read_json(self.root / 'out/reuse-summary.json')['actual_extraction_invocations'], 0)
        self.parity(); self.assertFalse(r['automatic_research_approval'])
        self.assertFalse((self.root / 'out/upstream-baseline.json').exists())

    def test_sys_only_change(self):
        self.update(); r = self.build(); self.assertEqual(r['upstreams'], self.revs); self.parity()

    def test_sim_only_change(self):
        self.update('SPHinXsim', 'sphinxsim/bindings/a.py', b'def f():\n    return 7\n'); self.build(); self.parity()

    def test_both_repositories_change(self):
        self.update(); self.update('SPHinXsim', 'sphinxsim/bindings/a.py', b'def f():\n    return 8\n'); self.build(); self.parity()

    def test_out_of_scope_commit_changes_provenance_not_source_state(self):
        self.update(name='outside.txt', raw=b'non-indexed change\n'); self.build()
        i = read_json(self.root / 'out/upstream-candidate.json')['identity']
        self.assertEqual(i['base_source_state'], i['target_source_state'])
        self.assertNotEqual(i['base_upstreams'], i['upstreams'])
        self.assertEqual(read_json(self.root / 'out/reuse-summary.json')['actual_extraction_invocations'], 0)
        self.parity()

    def test_add_delete_rename_and_zero_chunk(self):
        r = self.target / 'repos/SPHinXsys/src'
        (r / 'a.cpp').rename(r / 'renamed.cpp'); (r / 'b.cpp').unlink()
        (r / 'new.cpp').write_text('int added(){ return 5; }\n'); (r / 'zero.h').write_text('// still no chunks\n')
        self.revs['SPHinXsys'] = commit(r.parent); self.build(); self.parity()
        changes = read_json(self.root / 'out/change-manifest.json')
        self.assertEqual(Counter(c['change'] for c in changes)['DELETED'], 2)
        self.assertFalse(any('src/a.cpp' in r['source_path'] for r in inc._records(self.root / 'out/corpus.jsonl')))

    def test_executable_mode_change(self):
        p = self.target / 'repos/SPHinXsys/src/a.cpp'; p.chmod(0o755)
        self.revs['SPHinXsys'] = commit(p.parent.parent); self.build(); self.parity()
        selected = read_json(self.root / 'out/selection.json')
        self.assertEqual(next(s for s in selected if s['path'] == 'src/a.cpp')['git_mode'], '100755')

    def test_immediate_parent_multigeneration_fixture(self):
        self.update(); self.build('g1'); parent = self.root / 'g1'
        report = u.verify(P, parent, self.fp); meta = read_json(parent / 'upstream-candidate.json')
        self.fixture = {'candidate_id': report['node_id'], 'package_checksum': report['receipt']['package_checksum'],
                        'source_state': report['target_source_state'], 'parent': meta['identity']['parent']}
        self.parent = parent; self.source = self.target
        self.target = self.root / 'target2'; shutil.copytree(self.source, self.target)
        self.update('SPHinXsim', 'sphinxsim/bindings/a.py', b'def f():\n    return 33\n')
        result = self.build(); self.assertEqual(result['generation'], 2); self.parity()
        summary = read_json(self.root / 'out/reuse-summary.json')
        self.assertEqual(summary['actual_extraction_invocations'], 1)
        self.assertEqual(read_json(self.root / 'out/upstream-candidate.json')['identity']['base_upstreams'], meta['identity']['upstreams'])

    def test_unapproved_candidate_not_automatically_reusable(self):
        self.build('g1'); self.parent = self.root / 'g1'
        with self.assertRaisesRegex(ValueError, 'controlled chain fixture'): self.prepare()

    def test_incorrect_base_commit(self):
        p=self.prepare(); p['scope']['base_upstreams']['SPHinXsys']='0'*40; self.reject(p)

    def test_incorrect_target_commit(self):
        p=self.prepare(); p['scope']['target_upstreams']['SPHinXsys']='0'*40; self.reject(p)

    def test_missing_commit_object(self):
        with self.assertRaises(subprocess.CalledProcessError): u.relationship(self.target/'repos/SPHinXsys','f'*40,self.revs['SPHinXsys'])

    def test_wrong_repository_identity(self):
        p=self.prepare(); git(self.target/'repos/SPHinXsys','remote','set-url','origin','https://example.invalid/forged.git'); self.reject(p)

    def test_source_bytes_differ_from_commit(self):
        p=self.prepare(); (self.target/'repos/SPHinXsys/src/a.cpp').write_text('int forged(){return 0;}\n'); self.reject(p)

    def test_git_mode_mismatch(self):
        p=self.prepare(); (self.target/'repos/SPHinXsys/src/a.cpp').chmod(0o755); self.reject(p)

    def test_dirty_unselected_file(self):
        p=self.prepare(); (self.target/'repos/SPHinXsys/extra.txt').write_text('dirty\n'); self.reject(p)

    def test_derived_fixture_cannot_be_genuine(self):
        p=self.prepare(); p['provenance_type']='GENUINE_UPSTREAM'; self.reject(p)

    def test_changed_commit_cannot_be_labeled_pinned(self):
        p=self.prepare(); self.update(); self.reject(p)

    def test_invalid_base_source_state(self):
        p=self.prepare(); p['scope']['base_source_state']='sha256-'+'0'*64; self.reject(p)

    def test_invalid_target_source_state(self):
        p=self.prepare(); p['scope']['target_source_state']='sha256-'+'0'*64; self.reject(p)

    def test_wrong_parent_identity(self):
        p=self.prepare(); p['scope']['parent']['node_id']='wrong'; self.reject(p)

    def test_wrong_parent_package_checksum(self):
        p=self.prepare(); p['scope']['parent']['package_checksum']='0'*64; self.reject(p)

    def mutate_output(self, callback):
        self.build(); root=self.root/'out'; callback(root)
        (root/'checksums.json').unlink(); u._seal(root)
        with self.assertRaises((ValueError,KeyError,TypeError)): u.verify(P,root,self.fp)

    def test_unknown_lineage_schema(self):
        def f(r):
            m=read_json(r/'upstream-candidate.json');m['schema_version']=3;replace(r/'upstream-candidate.json',m)
        self.mutate_output(f)

    def test_invalid_generation(self):
        self.build(); r=u.verify(P,self.root/'out',self.fp)['receipt']; r=copy.deepcopy(r)
        r['identity']['generation']=5
        with self.assertRaises(ValueError):u.verify_lineage([r],self.fp)

    def test_cycle(self):
        self.build(); rs=u.verify(P,self.root/'out',self.fp)['lineage']
        with self.assertRaises(ValueError):u.verify_lineage([*rs,rs[-1]],self.fp)

    def test_rollback_commit_rejected(self):
        self.update()
        with self.assertRaisesRegex(ValueError,'OLDER_HISTORICAL_COMMIT'):u.relationship(self.target/'repos/SPHinXsys',self.revs['SPHinXsys'],self.revisions['SPHinXsys'])

    def test_divergent_commit_rejected(self):
        self.update(); first=self.revs['SPHinXsys'];r=self.target/'repos/SPHinXsys'
        git(r,'checkout','--detach',self.revisions['SPHinXsys']);self.update(raw=b'int a(){return 91;}\n')
        with self.assertRaisesRegex(ValueError,'DIVERGENT_HISTORY'):u.relationship(r,first,self.revs['SPHinXsys'])

    def test_wrong_build_fingerprint(self):
        p=self.prepare();p['scope']['build_fingerprint']='0'*64;self.reject(p)

    def test_invalid_selected_manifest(self):
        self.mutate_output(lambda r:replace(r/'selection.json',[]))

    def test_invalid_inventory_offsets(self):
        def f(r):
            v=read_json(r/'inventory.json');v[0]['record_start']=999;replace(r/'inventory.json',v)
        self.mutate_output(f)

    def test_invalid_native_graph(self):
        self.mutate_output(lambda r:replace(r/'extraction-graph.json',[]))

    def test_missing_required_document(self):
        p=self.prepare();(self.target/'repos/SPHinXsim/docs/cli-usage.md').unlink();self.reject(p)

    def test_relocation_process_and_determinism(self):
        self.build(); policy=read_json(self.root/'policy.json'); self.build('again',policy)
        self.assertEqual(read_json(self.root/'out/upstream-candidate.json'),read_json(self.root/'again/upstream-candidate.json'))
        shutil.copytree(self.root/'out',self.root/'relocated');shutil.rmtree(self.root/'out')
        result=subprocess.run([sys.executable,'-B',str(P/'scripts/upstream_research.py'),'verify','--candidate',str(self.root/'relocated'),
            '--expected-fingerprint',self.fp],cwd=self.root,env={**os.environ,'PYTHONHASHSEED':'0'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_corrupt_proof_after_relocation(self):
        def f(r):
            v=read_json(r/'git-proofs.json');v['target']['SPHinXsys']['commit']='0'*40;replace(r/'git-proofs.json',v)
        self.mutate_output(f)

    def test_no_silent_historical_conversion(self):
        self.build()
        with self.assertRaisesRegex(ValueError,'Unsupported research candidate'):inc.verify_candidate(P,self.root/'out',self.fp)
        with self.assertRaises(ValueError):u.verify(P,self.base,self.fp)

    def test_auto_approval_rejected(self):
        p=self.prepare();p['automatic_research_approval']=True;self.reject(p)

    def test_production_promotion_rejected(self):
        p=self.prepare();p['production_promotion']='AUTHORIZED';self.reject(p)

    def test_source_symlink_rejected(self):
        p=self.target/'repos/SPHinXsys/src/a.cpp';p.unlink();p.symlink_to('b.cpp')
        self.revs['SPHinXsys']=commit(p.parent.parent)
        with self.assertRaises(ValueError):self.prepare()

    def test_externally_pinned_policy_required(self):
        p=self.prepare()
        with self.assertRaises(ValueError):u.build(P,self.base,self.source,self.target,self.root/'out',policy_path=self.root/'policy.json',expected_policy_sha256='0'*64,expected_fingerprint=self.fp)

    def test_p2_anchor_requires_explicit_authorization(self):
        p=self.prepare();p['p2_anchor_authorized']=False;self.reject(p)

    def test_unknown_unrelated_git_history_rejected(self):
        r=self.target/'repos/SPHinXsys';before=self.revs['SPHinXsys']
        git(r,'checkout','--orphan','unrelated-fixture')
        (r/'unrelated.txt').write_text('new history\n');after=commit(r)
        with self.assertRaisesRegex(ValueError,'UNKNOWN_RELATIONSHIP'):u.relationship(r,before,after)

    def test_parent_payload_mutation_during_build_rejected(self):
        from unittest.mock import patch
        self.prepare();original=u.engine._extract_changed
        def mutate(work,changed):
            result=original(work,changed)
            (self.base/'execution.json').write_text('{}\n')
            return result
        with patch.object(u.engine,'_extract_changed',side_effect=mutate):
            with self.assertRaises(ValueError):self.build(policy=read_json(self.root/'policy.json'))
        self.assertFalse((self.root/'out').exists())

    def test_tree_merkle_proof_tamper_rejected(self):
        selection,proof=u.verify_sources(P,self.target,self.revs)
        obj=proof['SPHinXsys'];oid=next(iter(obj['trees']))
        obj['trees'][oid]='AAAA'
        with self.assertRaisesRegex(ValueError,'Git object checksum'):u.verify_proof(obj,selection)

    def test_legacy_p2_package_checksum_not_reinterpreted(self):
        from src.research_baseline import read_parent
        policy=self.prepare()
        old=read_parent(P,self.base,self.fp,baseline_format='p2')
        self.assertEqual(policy['scope']['parent']['package_checksum'],old['package_checksum'])

    def test_explicit_structural_approval_uses_p41_and_versions_lineage(self):
        self.build();self.parity();c=self.root/'out';m=read_json(c/'upstream-candidate.json');i=m['identity']
        lock=read_json(P/'configs/ci_v2_runtime_lock.json');q=review_not_evaluated(c,self.fp,lock['quality_policy'])
        auth={'schema_version':1,'research_only':True,'approved':True,'candidate_id':m['candidate_id'],
              'parent_baseline_id':i['parent']['node_id'],'experiment_id':'explicit-structural-test',
              'quality_status':'QUALITY_REVIEW_REQUIRED','allow_quality_review':True}
        ap={'schema_version':1,'scope':{'experiment_id':auth['experiment_id'],'parent_baseline_id':auth['parent_baseline_id'],
            'target_source_state':i['target_fixture_id'],'build_fingerprint':self.fp,'quality_policy_sha256':digest(q['policy'])},
            'mode':'STRUCTURAL_ONLY','quality_status':'QUALITY_REVIEW_REQUIRED','automatic_reuse':False,'production_promotion':'NOT_AUTHORIZED'}
        write_json(self.root/'approval-policy.json',ap)
        r=u.approve(P,c,self.base,self.root/'full',self.root/'approved',expected_fingerprint=self.fp,quality=q,authorization=auth,
                    approval_policy=self.root/'approval-policy.json',expected_policy_sha256=file_digest(self.root/'approval-policy.json'))
        self.assertEqual(r['P4_1_verification'],'P4_1_VERIFIED');self.assertEqual(r['receipt']['kind'],'RESEARCH_BASELINE')
        # This is an explicitly requested synthetic test decision, never a genuine
        # experiment approval or a global approved-pointer update.
        self.parent=self.root/'approved';policy=self.prepare();self.build('next',policy)


from collections import Counter
if __name__ == '__main__': unittest.main()
