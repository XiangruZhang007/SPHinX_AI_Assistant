"""Snapshot format invariants, adversarial payloads and real completion events."""
import copy
import hashlib
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
from unittest.mock import patch

from src.corpus_snapshot import (FORMAT,SnapshotError,canonical,digest,file_digest,write_json,
    read_json,read_records,snapshot_id,seal_snapshot,verify_snapshot,package_snapshot,restore_snapshot)
from src.corpus_snapshot_build import FileCollector
from src import chunk_generation_v1 as g

P=Path(__file__).resolve().parents[1]


def fixture(root):
    root.mkdir();items=[];records={'sys':[],'sim':[],'documentation':[]};graphs=[]
    specs=[('SPHinXsys','src/a.cpp','sys',b'int a() { return 1; }\n','core_library','cpp'),
           ('SPHinXsys','src/empty.h','sys',b'// no definitions\n','core_library','cpp'),
           ('SPHinXsim','sphinxsim/bindings/a.py','sim',b'def f():\n    return 1\n','binding','python'),
           ('SPHinXsim','docs/a.md','documentation',b'# A\n\nText.\n','documentation','md')]
    selection=[]
    for repo,path,component,raw,role,language in specs:
        source=root/'sources'/repo/path;source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(raw)
        entry={'repository':repo,'path':path,'component':component,'git_mode':'100644',
               'git_blob':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
               'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),'source_role':role,'language':language}
        selection.append(entry);start=len(records[component]);emitted=[];native=[]
        if not path.endswith('empty.h'):
            text=raw.decode() if component=='documentation' else raw[:-1].decode()
            identifier=('doc-' if component=='documentation' else '')+hashlib.sha1((repo+'/'+path).encode()).hexdigest()
            record={'chunk_id':identifier,'repository':repo,'source_role':role,'source_path':'repos/'+repo+'/'+path,
                    'chunk_type':'documentation_section' if component=='documentation' else 'function_definition',
                    'node_type':'documentation_section' if component=='documentation' else 'function_definition',
                    'symbol':'a','start_line':1,'end_line':text.rstrip('\n').count('\n')+1,
                    'byte_start':0,'byte_end':len(text.encode()),'parent_context':{},'content':text}
            emitted=[record];records[component]+=emitted
            if component!='documentation':native=[{'chunk_id':identifier,'node_type':'function_definition','byte_start':0,'byte_end':len(text.encode()),'parent_chunk_id':None,'content_sha256':hashlib.sha256(text.encode()).hexdigest()}]
        items.append({**entry,'status':'SUCCESS_WITH_CHUNKS' if emitted else 'SUCCESS_ZERO_CHUNKS','error':None,
                      'record_start':start,'chunk_count':len(emitted),'chunk_ids':[r['chunk_id'] for r in emitted],
                      'record_digest':digest(emitted)})
        graphs.append({'repository':repo,'path':path,'native':native,'transformed':native,'replacements':[]})
    def lines(path,rows):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n' for r in rows))
    records['source']=records['sys']+records['sim'];all_records=records['source']+records['documentation']
    for component,rows in records.items():lines(root/'components'/(component+'.jsonl'),rows)
    lines(root/'corpus.jsonl',all_records)
    contract={'identity_scheme':'portable-v1','snapshot_format':FORMAT,'test_contract':1}
    for name,data in [('build-contract.json',contract),('selection.json',selection),('inventory.json',items),('extraction-graph.json',graphs),('validation.json',{'structural_status':'STRUCTURALLY_VALID','retrieval_quality_status':'QUALITY_ACCEPTED'})]:write_json(root/name,data)
    identity={'snapshot_type':'full-bootstrap','parent_snapshot_id':None,'identity_scheme':'portable-v1',
              'upstreams':{'SPHinXsys':'a'*40,'SPHinXsim':'b'*40},'build_fingerprint':digest(contract),
              'inventory':{'path':'inventory.json','sha256':file_digest(root/'inventory.json'),'files':len(items)},
              'extraction_graph':{'path':'extraction-graph.json','sha256':file_digest(root/'extraction-graph.json'),'files':len(items)},
              'components':{k:{'path':'components/'+k+'.jsonl','sha256':file_digest(root/'components'/(k+'.jsonl')),'records':len(v)} for k,v in records.items()},
              'corpus':{'path':'corpus.jsonl','sha256':file_digest(root/'corpus.jsonl'),'records':len(all_records)}}
    seal_snapshot(root,identity,{'structural_status':'STRUCTURALLY_VALID','retrieval_quality_status':'QUALITY_ACCEPTED','report':'validation.json'})
    return identity


def replace_json(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def reseal(root,identity=None):
    """Negative tests update transport checksums to test deeper invariants."""
    identity=copy.deepcopy(identity or read_json(root/'manifest.json')['identity'])
    for name in ['inventory','extraction_graph','corpus']:
        identity[name]['sha256']=file_digest(root/identity[name]['path'])
    for descriptor in identity['components'].values():descriptor['sha256']=file_digest(root/descriptor['path'])
    (root/'manifest.json').unlink();(root/'checksums.json').unlink()
    validation=read_json(root/'validation.json')
    seal_snapshot(root,identity,{'structural_status':validation['structural_status'],
        'retrieval_quality_status':validation['retrieval_quality_status'],'report':'validation.json'})


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'snapshot';self.identity=fixture(self.root)

    def test_complete_integrity_and_independent_compatibility(self):
        result=verify_snapshot(self.root,self.identity['build_fingerprint'])
        self.assertEqual(result['structural_status'],'STRUCTURALLY_VALID')
        self.assertEqual(result['build_compatibility'],'COMPATIBLE');self.assertTrue(result['incremental_reuse_eligible'])
        self.assertEqual(result['records'],3);self.assertEqual(result['selected_files'],4);self.assertEqual(result['zero_chunk_files'],1)
        self.assertEqual(result['production_promotion'],'NOT_AUTHORIZED')

    def test_missing_expected_fingerprint_is_not_checked_or_reusable(self):
        result=verify_snapshot(self.root);self.assertEqual(result['build_compatibility'],'NOT_CHECKED');self.assertFalse(result['incremental_reuse_eligible'])

    def test_incompatible_expected_fingerprint(self):
        result=verify_snapshot(self.root,'bad');self.assertEqual(result['build_compatibility'],'INCOMPATIBLE');self.assertFalse(result['incremental_reuse_eligible'])

    def test_incompatible_package_rejected_before_writing(self):
        target=self.base/'wrong.tar.gz'
        with self.assertRaisesRegex(SnapshotError,'incompatible'):package_snapshot(self.root,target,'bad')
        self.assertFalse(target.exists())

    def test_invalid_upstream_identity(self):
        identity=copy.deepcopy(self.identity);identity['upstreams']['SPHinXsys']='not-a-commit';reseal(self.root,identity)
        with self.assertRaisesRegex(SnapshotError,'upstream'):verify_snapshot(self.root)

    def test_graph_descriptor_count(self):
        identity=copy.deepcopy(self.identity);identity['extraction_graph']['files']+=1;reseal(self.root,identity)
        with self.assertRaisesRegex(SnapshotError,'count'):verify_snapshot(self.root)

    def test_canonical_identity_does_not_depend_on_key_order(self):
        self.assertEqual(canonical({'z':None,'a':[2,1]}),b'{"a":[2,1],"z":null}')
        self.assertEqual(snapshot_id(self.identity),snapshot_id(dict(reversed(list(self.identity.items())))))
        changed=copy.deepcopy(self.identity);changed['upstreams']['SPHinXsys']='c'*40
        self.assertNotEqual(snapshot_id(changed),snapshot_id(self.identity))

    def test_frozen_hash_contract_vector(self):
        # Exact core contract; no implicit optional/null omission.
        with self.assertRaises(SnapshotError):snapshot_id({**self.identity,'timestamp':'later'})
        changed=copy.deepcopy(self.identity);changed.pop('parent_snapshot_id')
        with self.assertRaises(SnapshotError):snapshot_id(changed)

    def test_runtime_report_changes_are_integrity_protected_not_content_identity(self):
        old_id=snapshot_id(self.identity)
        (self.root/'diagnostic.log').write_text('timestamp /some/physical/path\n')
        reseal(self.root)
        self.assertEqual(verify_snapshot(self.root)['snapshot_id'],old_id)
        (self.root/'diagnostic.log').write_text('corrupted\n')
        with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_corrupt_corpus_bytes(self):
        with (self.root/'corpus.jsonl').open('ab') as f:f.write(b'bad')
        with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_truncated_jsonl_even_when_outer_hashes_updated(self):
        p=self.root/'corpus.jsonl';p.write_bytes(p.read_bytes()[:-1]);reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'unterminated'):verify_snapshot(self.root)

    def test_missing_component(self):
        (self.root/'components/sim.jsonl').unlink()
        with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_invalid_checksum(self):
        m=read_json(self.root/'manifest.json');m['files']['corpus.jsonl']='0'*64;replace_json(self.root/'manifest.json',m)
        with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_unsupported_schema(self):
        m=read_json(self.root/'manifest.json');m['schema_version']=2;replace_json(self.root/'manifest.json',m)
        with self.assertRaisesRegex(SnapshotError,'schema'):verify_snapshot(self.root)

    def test_unknown_or_legacy_identity_scheme(self):
        for scheme in ['legacy','unknown']:
            identity=copy.deepcopy(self.identity);identity['identity_scheme']=scheme;reseal(self.root,identity)
            with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_wrong_count_after_resealing(self):
        identity=copy.deepcopy(self.identity);identity['corpus']['records']+=1;reseal(self.root,identity)
        with self.assertRaisesRegex(SnapshotError,'descriptor'):verify_snapshot(self.root)

    def test_duplicate_id_after_resealing(self):
        p=self.root/'corpus.jsonl';rows=read_records(p);rows[1]['chunk_id']=rows[0]['chunk_id']
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows));reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'duplicate'):verify_snapshot(self.root)

    def test_missing_zero_chunk_inventory_member(self):
        p=self.root/'inventory.json';items=read_json(p);items.pop(1);replace_json(p,items);reseal(self.root)
        with self.assertRaises(SnapshotError):verify_snapshot(self.root)

    def test_inconsistent_inventory_mapping_after_resealing(self):
        p=self.root/'inventory.json';items=read_json(p);items[0]['chunk_ids']=['unknown'];replace_json(p,items);reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'mapping'):verify_snapshot(self.root)

    def test_failed_file_cannot_be_labeled_reusable(self):
        p=self.root/'inventory.json';items=read_json(p);items[1]['status']='FAILED';replace_json(p,items);reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'Failed'):verify_snapshot(self.root)

    def test_changed_stored_fingerprint_contract_rejected(self):
        replace_json(self.root/'build-contract.json',{'identity_scheme':'portable-v1','snapshot_format':FORMAT,'test_contract':2});reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'fingerprint'):verify_snapshot(self.root)

    def test_changed_file_or_component_order_rejected(self):
        p=self.root/'inventory.json';items=read_json(p);items[0],items[1]=items[1],items[0];replace_json(p,items);reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'provenance'):verify_snapshot(self.root)

    def test_invalid_structural_reference_rejected(self):
        p=self.root/'extraction-graph.json';graph=read_json(p);graph[0]['native'][0]['parent_chunk_id']='unknown'
        graph[0]['transformed']=graph[0]['native'];replace_json(p,graph);reseal(self.root)
        with self.assertRaisesRegex(SnapshotError,'parent'):verify_snapshot(self.root)

    def test_nonfinal_semantic_parent_is_valid_without_placeholder(self):
        p=self.root/'corpus.jsonl';records=read_records(p);r=records[0];native_id=r['chunk_id']
        r['chunk_id']=hashlib.sha1(b'section').hexdigest();r['node_type']='semantic_section';r['chunk_type']='semantic_section'
        r['parent_context']={'original_function_chunk_id':native_id,'original_function_byte_start':0,'original_function_byte_end':r['byte_end']}
        for path,rows in [('corpus.jsonl',records),('components/sys.jsonl',[r]),('components/source.jsonl',records[:2])]:
            (self.root/path).write_text(''.join(json.dumps(x)+'\n' for x in rows))
        items=read_json(self.root/'inventory.json');items[0]['chunk_ids']=[r['chunk_id']];items[0]['record_digest']=digest([r]);replace_json(self.root/'inventory.json',items)
        reseal(self.root);result=verify_snapshot(self.root)
        self.assertGreater(result['reference_checks']['native_targets'],0)
        self.assertNotIn(native_id,{x['chunk_id'] for x in records})

    def test_quality_review_remains_separate_from_structural_validity(self):
        replace_json(self.root/'validation.json',{'structural_status':'STRUCTURALLY_VALID','retrieval_quality_status':'QUALITY_REVIEW_REQUIRED'})
        reseal(self.root);result=verify_snapshot(self.root,self.identity['build_fingerprint'])
        self.assertEqual(result['structural_status'],'STRUCTURALLY_VALID');self.assertFalse(result['incremental_reuse_eligible'])

    def test_package_restore_and_relocation_in_independent_process(self):
        archive=self.base/'snapshot.tar.gz';package_snapshot(self.root,archive)
        restored=self.base/'relocated';result=restore_snapshot(archive,restored,self.identity['build_fingerprint'])
        self.assertEqual(result['snapshot_id'],snapshot_id(self.identity))
        self.assertEqual((restored/'corpus.jsonl').read_bytes(),(self.root/'corpus.jsonl').read_bytes())
        shutil.rmtree(self.root)
        run=subprocess.run([sys.executable,'-B',str(P/'scripts/corpus_snapshot.py'),'verify','--snapshot',str(restored)],cwd=self.base,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)['build_compatibility'],'NOT_CHECKED')

    def test_no_overwriting_of_snapshot_or_package(self):
        with self.assertRaises(SnapshotError):seal_snapshot(self.root,self.identity,{})
        archive=self.base/'a.tar.gz';package_snapshot(self.root,archive)
        with self.assertRaises(FileExistsError):package_snapshot(self.root,archive)
        with self.assertRaises(SnapshotError):restore_snapshot(archive,self.root)

    def test_archive_path_traversal_and_links_rejected_before_extraction(self):
        for index,(name,kind) in enumerate([('../escape','file'),('/absolute','file'),('link','symlink')]):
            archive=self.base/(str(index)+'.tar')
            with tarfile.open(archive,'w') as t:
                member=tarfile.TarInfo(name)
                if kind=='symlink':member.type=tarfile.SYMTYPE;member.linkname='../escape';t.addfile(member)
                else:member.size=1;t.addfile(member,io.BytesIO(b'x'))
            destination=self.base/('bad'+str(index))
            with self.assertRaises(ValueError):restore_snapshot(archive,destination)
            self.assertFalse(destination.exists())


class CompletionObservationTests(unittest.TestCase):
    def test_real_zero_chunk_completion_is_observed_not_inferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'repos/SPHinXsys/src/a.h';path.parent.mkdir(parents=True);path.write_text('// no functions\n')
            collector=FileCollector(root,[{'repository':'SPHinXsys','path':'src/a.h','component':'sys'}])
            with patch.object(g,'_source_files',return_value=iter([path])):
                g.generate(root,root/'chunks',root/'summary',identity_scheme='portable-v1',file_observer=collector)
            self.assertEqual(collector.inventory[0]['status'],'SUCCESS_ZERO_CHUNKS');self.assertEqual(collector.inventory[0]['chunk_ids'],[])

    def test_failed_source_is_observed_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'repos/SPHinXsys/src/missing.h'
            collector=FileCollector(root,[{'repository':'SPHinXsys','path':'src/missing.h','component':'sys'}])
            with patch.object(g,'_source_files',return_value=iter([path])):
                summary=g.generate(root,root/'chunks',root/'summary',file_observer=collector)
            self.assertTrue(summary['skipped_files']);self.assertEqual(collector.inventory[0]['status'],'FAILED')


if __name__=='__main__':unittest.main()
