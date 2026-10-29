"""Q1 source/corpus diagnostics; controlled fixtures never claim genuine Git."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from src import version_aware_evidence as q
from src import documentation_chunking as doc
from src.corpus_snapshot import digest, file_digest, read_json
from src.incremental_corpus import _lines

P=Path(__file__).resolve().parents[1]
PATH='repos/SPHinXsim/docs/unit.md'


def make_state(files, *, records=None, absences=(), upstream='a'*40, overrides=None):
    selection=[]; generated=[]
    for path,raw in files.items():
        parts=path.split('/');repo=parts[1];relative='/'.join(parts[2:])
        selection.append({'repository':repo,'path':relative,'component':'documentation','source_role':'documentation',
            'language':'md','source_bytes':len(raw),'source_sha256':q.sha(raw),'git_blob':q.provenance._object(raw,'blob'),'git_mode':'100644'})
        rs,_=doc.chunk_document(raw,repo,relative);generated.extend(rs)
    records=generated if records is None else records
    origin={'verification':'CONTROLLED_TEST_FIXTURE','provenance_type':'CONTROLLED_TEST_FIXTURE',
            'source_state_id':q.provenance.state(selection),'corpus_sha256':q.sha(_lines(records)),
            'build_fingerprint':'fixture-build','upstreams':{'SPHinXsys':upstream,'SPHinXsim':upstream},'package_id':'controlled-fixture'}
    if overrides:origin.update(overrides)
    return q.State(origin,selection,files,records,{},absences).validate()


def criterion(first=3,last=3,kind='line_range',path=PATH):
    return {'kind':kind,'path':path,'role':'documentation','range':[first,last]}


def fixture_spec(requirements):
    return {'benchmark_id':'CONTROLLED_Q1_TEST_FIXTURE','questions':[{'id':'F-01','group':'A','evidence_mode':'ALL_OF','requirements':requirements}]}


def analyze(base,target,c):
    return q.map_requirement(base,target,c,'combined')


class EvidenceMappingTests(unittest.TestCase):
    def setUp(self):
        self.raw=b'# Heading\nLEFT unique anchor\noriginal evidence text\nRIGHT unique anchor\n'
        self.base=make_state({PATH:self.raw});self.c=criterion()

    def test_unchanged_evidence(self):
        r=analyze(self.base,self.base,self.c)
        self.assertEqual(r['classification'],'UNCHANGED');self.assertEqual(r['frozen_criterion_applicability'],'APPLICABLE')

    def test_unique_relocation_and_changed_file_unchanged_passage(self):
        target=make_state({PATH:b'inserted line\n'+self.raw})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['classification'],'MOVED');self.assertEqual(r['file_change_state'],'MODIFIED')
        self.assertEqual(r['source_content_state'],'UNCHANGED');self.assertEqual(r['target_passage']['line_range'],[4,4])
        self.assertEqual(r['original_passage']['raw_sha256'],r['target_passage']['raw_sha256'])

    def test_modified_passage_requires_semantic_review(self):
        target=make_state({PATH:self.raw.replace(b'original evidence text',b'modified evidence text')})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['classification'],'MODIFIED');self.assertEqual(r['semantic_review'],'SEMANTIC_REVIEW_REQUIRED')
        self.assertNotEqual(r['original_passage']['raw_sha256'],r['target_passage']['raw_sha256'])

    def test_modified_does_not_assert_changed_answer_semantics(self):
        target=make_state({PATH:self.raw.replace(b'original evidence text',b'original evidence TEXT')})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['semantic_review'],'SEMANTIC_REVIEW_REQUIRED');self.assertNotIn('semantically_equivalent',r)

    def test_verified_passage_removal_from_anchored_context(self):
        target=make_state({PATH:self.raw.replace(b'original evidence text\n',b'')})
        r=analyze(self.base,target,self.c);self.assertEqual(r['classification'],'REMOVED')
        self.assertEqual(r['mapping_evidence']['context']['method'],'EXACT_UNIQUE_TWO_SIDED_LINE_CONTEXT')

    def test_exact_search_failure_does_not_prove_removal(self):
        target=make_state({PATH:b'# Different\nEverything changed entirely.\n'})
        r=analyze(self.base,target,self.c);self.assertEqual(r['classification'],'UNRESOLVED')

    def split_target(self,moved=False,gap=False):
        raw=(b'prefix\n' if moved else b'')+self.raw
        state=make_state({PATH:raw});records=copy.deepcopy(state.records)
        # Exact evidence span in line 3 (or 4) partitioned into validated pieces.
        start=raw.index(b'original evidence text');cut=start+9;end=start+len(b'original evidence text')
        template=records[0];new=[]
        for i,(a,b) in enumerate([(start,cut),(cut+1 if gap else cut,end)]):
            rec=copy.deepcopy(template);rec.update(chunk_id='fixture-split-'+str(i),content=raw[a:b].decode(),byte_start=a,byte_end=b,
                start_line=raw[:a].count(b'\n')+1,end_line=raw[:b-1].count(b'\n')+1)
            new.append(rec)
        return make_state({PATH:raw},records=new)

    def test_split_requires_real_complete_byte_coverage(self):
        r=analyze(self.base,self.split_target(),self.c)
        self.assertEqual(r['classification'],'SPLIT');self.assertEqual(len(r['target_corpus_coverage']['covering_chunk_ids']),2)

    def test_moved_and_split_preserves_both_dimensions(self):
        r=analyze(self.base,self.split_target(moved=True),self.c)
        self.assertEqual(r['classification'],'SPLIT');self.assertEqual(r['source_location_state'],'MOVED')
        self.assertEqual(r['frozen_locator_state'],'STALE')

    def test_neighboring_chunks_with_gap_are_not_valid_split(self):
        r=analyze(self.base,self.split_target(gap=True),self.c)
        self.assertEqual(r['classification'],'UNRESOLVED');self.assertEqual(r['target_corpus_coverage']['state'],'PARTIAL')

    def test_multiple_identical_target_passages_are_ambiguous(self):
        target=make_state({PATH:self.raw+b'original evidence text\n'})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['classification'],'AMBIGUOUS_MAPPING');self.assertEqual(len(r['candidate_passages']),2)

    def test_unchanged_whole_file_keeps_known_position_despite_duplicates(self):
        raw=self.raw+b'original evidence text\n';base=make_state({PATH:raw})
        self.assertEqual(analyze(base,base,self.c)['classification'],'UNCHANGED')

    def test_missing_selected_source_is_unresolved_without_deletion_proof(self):
        target=make_state({})
        self.assertEqual(analyze(self.base,target,self.c)['classification'],'UNRESOLVED')

    def test_verified_file_absence_is_removed(self):
        target=make_state({},absences=(PATH,))
        self.assertEqual(analyze(self.base,target,self.c)['classification'],'REMOVED')

    def test_source_exists_but_no_indexed_chunks(self):
        target=make_state({PATH:self.raw},records=[])
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['source_content_state'],'UNCHANGED');self.assertEqual(r['target_corpus_coverage']['state'],'ABSENT')
        self.assertEqual(r['classification'],'UNRESOLVED')

    def test_rename_unique_exact_file_correspondence(self):
        newpath='repos/SPHinXsim/docs/renamed.md';target=make_state({newpath:self.raw})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['classification'],'MOVED');self.assertEqual(r['target_passage']['path'],newpath)
        self.assertEqual(r['mapping_evidence']['path_correspondence'],'UNIQUE_IDENTICAL_FILE_BLOB_RELOCATION')

    def test_modified_rename_without_correspondence_is_unresolved(self):
        target=make_state({'repos/SPHinXsim/docs/renamed.md':b'# New\nChanged content\n'})
        self.assertEqual(analyze(self.base,target,self.c)['classification'],'UNRESOLVED')

    def test_multiple_possible_renamed_paths_preserved(self):
        target=make_state({'repos/SPHinXsim/docs/a.md':self.raw,'repos/SPHinXsim/docs/b.md':self.raw})
        r=analyze(self.base,target,self.c)
        self.assertEqual(r['classification'],'AMBIGUOUS_MAPPING');self.assertEqual(len(r['mapping_evidence']['candidate_target_paths']),2)

    def test_git_commit_change_without_source_state_change(self):
        target=make_state({PATH:self.raw},upstream='b'*40)
        self.assertEqual(self.base.origin['source_state_id'],target.origin['source_state_id'])
        self.assertNotEqual(self.base.origin['upstreams'],target.origin['upstreams'])
        self.assertEqual(analyze(self.base,target,self.c)['classification'],'UNCHANGED')

    def test_wrong_source_state_identity_rejected(self):
        self.base.origin['source_state_id']='sha256-'+'0'*64
        with self.assertRaises(ValueError):self.base.validate()

    def test_wrong_corpus_association_rejected(self):
        self.base.origin['corpus_sha256']='0'*64
        with self.assertRaises(ValueError):self.base.validate()

    def test_wrong_source_range_metadata_rejected(self):
        self.base.records[0]['start_line']=900;self.base.origin['corpus_sha256']=q.sha(_lines(self.base.records))
        with self.assertRaises(ValueError):self.base.validate()

    def test_unsupported_locator_kept_in_denominator(self):
        c={'kind':'unsupported-position','path':PATH,'role':'documentation'}
        r=q.analyze(fixture_spec([c]),self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        self.assertEqual(r['summary']['unsupported_instances'],2);self.assertEqual(r['summary']['evidence_requirement_instances'],2)

    def test_all_of_instances_and_duplicate_modes_preserved(self):
        spec=fixture_spec([self.c,dict(self.c)])
        r=q.analyze(spec,self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        self.assertEqual(r['summary']['question_mode_combinations'],2)
        self.assertEqual(r['summary']['evidence_requirement_instances'],4);self.assertEqual(r['summary']['unique_frozen_locators'],1)
        self.assertEqual(len({x['requirement_id'] for x in r['requirements']}),4)
        self.assertTrue(all(x['evidence_operator']=='ALL_OF' for x in r['requirements']))

    def test_deterministic_order_and_no_input_mutation(self):
        spec=fixture_spec([self.c]);before=digest(spec)
        a=q.analyze(spec,self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        b=q.analyze(spec,self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        self.assertEqual(a,b);self.assertEqual(digest(spec),before)
        self.assertEqual([r['mode'] for r in a['requirements']],['combined','documentation_only'])

    def test_reproducible_output_directories_and_report_relocation(self):
        r=q.analyze(fixture_spec([self.c]),self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);q.write_report(root/'one',r);q.write_report(root/'two',r)
            for n in ('version_aware_evidence_report.json','summary.md','checksums.json'):self.assertEqual((root/'one'/n).read_bytes(),(root/'two'/n).read_bytes())
            shutil.move(root/'one',root/'moved')
            self.assertEqual(read_json(root/'moved/version_aware_evidence_report.json')['report_id'],r['report_id'])

    def test_original_frozen_outcomes_remain_fail(self):
        target=make_state({PATH:b'prefix\n'+self.raw});spec=fixture_spec([self.c])
        original=[{'mode':mode,'id':'F-01','strict':{'hit_at_5':False,'hit_at_10':False},'evidence_available':False,'top_10':[]} for mode in ('combined','documentation_only')]
        before=digest(original)
        r=q.analyze(spec,self.base,target,benchmark_sha256='fixture',implementation_contract={},base_rows=original,target_rows=original)
        self.assertEqual(digest(original),before)
        self.assertTrue(all(x['frozen_question_outcomes']['target']['strict']['hit_at_10'] is False for x in r['requirements']))

    def test_cannot_authorize_promotion(self):
        r=q.analyze(fixture_spec([self.c]),self.base,self.base,benchmark_sha256='fixture',implementation_contract={})
        self.assertTrue(r['diagnostic_only']);self.assertFalse(r['automatic_research_approval'])
        self.assertEqual(r['production_promotion'],'NOT_AUTHORIZED');self.assertFalse(r['official_score_adjustment'])

    def test_no_any_of(self):
        s=fixture_spec([self.c]);s['questions'][0]['evidence_mode']='ANY_OF'
        with self.assertRaises(ValueError):q.analyze(s,self.base,self.base,benchmark_sha256='fixture',implementation_contract={})

    def test_passage_locator_exact_source_anchor(self):
        c={'kind':'passage','path':PATH,'role':'documentation','value':'original evidence text'}
        self.assertEqual(analyze(self.base,self.base,c)['classification'],'UNCHANGED')

    def test_casefold_only_anchor_is_not_invented(self):
        c={'kind':'passage','path':PATH,'role':'documentation','value':'ORIGINAL EVIDENCE TEXT'}
        self.assertEqual(analyze(self.base,self.base,c)['classification'],'UNRESOLVED')

    def test_section_title_locator_uses_actual_base_chunk(self):
        c={'kind':'section_title','path':PATH,'role':'documentation','value':'Heading'}
        self.assertEqual(analyze(self.base,self.base,c)['classification'],'UNCHANGED')

    def test_symbol_locator_uses_actual_base_span(self):
        records=copy.deepcopy(self.base.records);records[0]['symbol']='fixture_function'
        state=make_state({PATH:self.raw},records=records)
        c={'kind':'symbol','path':PATH,'role':'documentation','symbol':'fixture_function'}
        self.assertEqual(analyze(state,state,c)['classification'],'UNCHANGED')

    def test_original_range_not_expanded_to_contains_all(self):
        c={**self.c,'contains_all':['RIGHT unique anchor']}
        r=analyze(self.base,self.base,c)
        self.assertEqual(r['original_passage']['text'],'original evidence text')

    def test_frozen_metadata_match_can_coexist_with_partial_literal_bytes(self):
        raw=b'# Heading\nLEFT\nbody();\n';base=make_state({PATH:raw})
        records=copy.deepcopy(base.records);end=raw.index(b';')
        records[0].update(chunk_id='fixture-no-trailing-semicolon',content=raw[:end].decode(),byte_end=end,end_line=3)
        target=make_state({PATH:raw},records=records)
        r=analyze(base,target,{**criterion(),'contains_all':['body()']})
        self.assertEqual(r['target_corpus_coverage']['state'],'PARTIAL')
        self.assertTrue(r['frozen_predicate_has_matches']);self.assertEqual(r['frozen_criterion_applicability'],'APPLICABLE')
        self.assertEqual(r['frozen_match_vs_literal_coverage'],'FROZEN_MATCH_WITH_PARTIAL_LITERAL_RANGE_BYTES')

    def test_ambiguous_original_section_preserves_all_base_candidates(self):
        state=make_state({PATH:b'# Section\nfirst text\n# Section\nsecond text\n'})
        r=analyze(state,state,{'kind':'section_title','path':PATH,'role':'documentation','value':'Section'})
        self.assertEqual(r['classification'],'AMBIGUOUS_MAPPING');self.assertEqual(len(r['candidate_passages']),2)
        self.assertEqual(r['file_change_state'],'UNCHANGED')
        self.assertEqual(r['base_file_metadata']['source_sha256'],r['target_file_metadata']['source_sha256'])

    def test_output_cannot_modify_project_or_verified_input_tree(self):
        with self.assertRaisesRegex(ValueError,'overlaps protected'):q.analyze_to(P,Path('/tmp/preserved/inputs.json'),'0'*64,P/'q1-output')
        with self.assertRaisesRegex(ValueError,'overlaps protected'):q.analyze_to(P,Path('/tmp/preserved/inputs.json'),'0'*64,Path('/tmp/preserved/report'))

    def test_raw_crlf_and_utf8_byte_provenance_preserved(self):
        raw='# 标题\r\n之前\r\n证据文本\r\n之后\r\n'.encode();state=make_state({PATH:raw})
        r=analyze(state,state,self.c)
        self.assertEqual(r['original_passage']['text'],'证据文本');self.assertEqual(r['original_passage']['raw_sha256'],q.sha('证据文本'.encode()))


INPUTS=os.environ.get('SPHINX_Q1_INPUTS')
INPUT_SHA=os.environ.get('SPHINX_Q1_INPUTS_SHA256')


@unittest.skipUnless(INPUTS and INPUT_SHA,'Requires pinned immutable historical Q1 inputs; controlled tests remain independent')
class HistoricalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec,cls.base,cls.target,cls.rows,cls.pins=q.load_inputs(P,Path(INPUTS),INPUT_SHA)
        cls.contract=q.implementation(P)
        cls.report=q.analyze(cls.spec,cls.base,cls.target,benchmark_sha256=cls.pins['benchmark_sha256'],implementation_contract=cls.contract,
                              base_rows=cls.rows['base'],target_rows=cls.rows['target'])

    def test_real_doc_q27_independent_reproduction(self):
        rows=[r for r in self.report['requirements'] if r['question_id']=='DOC-Q27']
        self.assertEqual(len(rows),2)
        for r in rows:
            self.assertEqual(r['classification'],'MOVED');self.assertEqual(r['file_change_state'],'MODIFIED')
            self.assertEqual(r['original_passage']['line_range'],[101,104]);self.assertEqual(r['target_passage']['line_range'],[103,106])
            self.assertEqual(r['original_passage']['raw_sha256'],r['target_passage']['raw_sha256'])
            self.assertEqual(r['mapping_uniqueness'],'UNIQUE');self.assertEqual(r['target_corpus_coverage']['state'],'SINGLE_CHUNK')
            self.assertFalse(r['frozen_question_outcomes']['target']['strict']['hit_at_10'])

    def test_all_frozen_combinations_and_requirements_accounted_for(self):
        expected=sum(len(qi['requirements'])*(2 if qi['group']=='A' else 1) for qi in self.spec['questions'])
        s=self.report['summary'];self.assertEqual(s['question_mode_combinations'],128);self.assertEqual(s['evidence_requirement_instances'],expected)

    def test_frozen_files_unchanged(self):
        lock=read_json(P/'configs/ci_v2_runtime_lock.json')
        self.assertEqual(file_digest(P/'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json'),q.BENCHMARK_SHA)
        for name,h in lock['quality_policy']['evaluation_implementation'].items():self.assertEqual(file_digest(P/name),h)

    def test_input_manifest_pin_required(self):
        with self.assertRaises(ValueError):q.load_inputs(P,Path(INPUTS),'0'*64)

    def test_wrong_target_corpus_pin_rejected(self):
        # Mutation stays inside a private manifest, never the historical package.
        cfg=read_json(Path(INPUTS));cfg['pins']['target_corpus_sha256']='0'*64
        with tempfile.TemporaryDirectory() as t:
            # Preserve the same input root while changing only the external pin by
            # exercising the low-level state validation on the actual target.
            state=copy.deepcopy(self.target);state.origin['corpus_sha256']=cfg['pins']['target_corpus_sha256']
            with self.assertRaises(ValueError):state.validate()

    def test_independent_process_cli_reproducibility(self):
        with tempfile.TemporaryDirectory() as t:
            output=Path(t)/'report'
            env={**os.environ,'PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1'}
            run=subprocess.run([sys.executable,'-s','-B',str(P/'scripts/version_aware_evidence.py'),'analyze','--inputs',INPUTS,
                '--expected-inputs-sha256',INPUT_SHA,'--output',str(output)],cwd=t,env=env,capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertEqual(read_json(output/'version_aware_evidence_report.json'),self.report)
            self.assertEqual(q.verify_report(P,INPUTS,INPUT_SHA,output)['status'],'VERIFIED')


if __name__=='__main__':unittest.main()
