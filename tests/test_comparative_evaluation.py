"""Focused tests of evaluation validity; no real benchmark retrieval here."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from src.comparative_evaluation import Sources,sha256,score,availability,whole_line_budget,physical_lines,intervals_union,skill_evidence_stream


class ComparativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.file=self.project/'repos/SPHinXsim/test.py'
        self.file.parent.mkdir(parents=True);self.file.write_bytes('α = 1\r\n    important()\r\nlast'.encode())
        self.sources=Sources(self.project);self.raw=self.file.read_bytes()

    def record(self,a,b):
        return {'chunk_id':f'id-{a}-{b}','repository':'SPHinXsim','source_path':'repos/SPHinXsim/test.py',
          'byte_start':a,'byte_end':b,'start_line':self.raw[:a].count(b'\n')+1,'end_line':self.raw[:b-1].count(b'\n')+1,
          'content':self.raw[a:b].decode()}

    def skill(self,start,end):
        spans=physical_lines(self.raw);a,b=spans[start-1][0],spans[end-1][1]
        return {'evidence_id':f'skill-{start}-{end}','repository':'SPHinXsim','path':'test.py','sha256':sha256(self.raw),
         'text':self.raw[a:b].decode(),'location':{'start_line':start,'end_line':end},'citation':f'repos/SPHinXsim/test.py:L{start}-L{end}','truncated':False}

    def question(self):
        anchor=self.sources.anchors('SPHinXsim','test.py',2,2)
        return {'components':[{'id':'fact','alternatives':[[anchor]]}],'multi_file_required':False}

    def test_unicode_crlf_indentation_partial_physical_lines(self):
        a=self.raw.index(b'important');b=a+len(b'important()')
        r=self.record(a,b);proof=self.sources.validate(r,'bm25')
        self.assertTrue(proof['valid'],proof)
        self.assertNotEqual(r['content'],self.raw.splitlines(keepends=True)[1].decode())
        self.assertEqual(proof['method'],'exact-original-byte-range')
        r=self.record(0,len('α = 1'.encode()));self.assertTrue(self.sources.validate(r,'bm25')['valid'])

    def test_complete_lines_and_missing_terminal_newline(self):
        for start,end in [(1,1),(2,2),(3,3),(1,3)]:
            e=self.skill(start,end);self.assertTrue(self.sources.validate(e,'skill')['valid'])
        self.assertEqual(self.skill(3,3)['text'],'last')
        self.assertEqual(self.skill(1,1)['text'],'α = 1\r\n')

    def test_wrong_content_is_not_normalized(self):
        e=self.skill(2,2);e['text']=e['text'].lstrip()
        self.assertFalse(self.sources.validate(e,'skill')['valid'])
        r=self.record(0,len(self.raw));r['content']=r['content'].replace('\r\n','\n')
        self.assertFalse(self.sources.validate(r,'bm25')['valid'])

    def test_byte_metadata_mismatch_must_not_fallback(self):
        r=self.record(0,len(self.raw));r['byte_start']=1
        self.assertFalse(self.sources.validate(r,'bm25')['valid'])

    def test_unique_fallback_and_ambiguous_fallback(self):
        a=self.raw.index(b'important');r=self.record(a,a+len(b'important()'));r.pop('byte_start');r.pop('byte_end')
        p=self.sources.validate(r,'bm25');self.assertTrue(p['valid']);self.assertIn('limitation',p)
        self.file.write_bytes(b'x x\n');self.sources=Sources(self.project)
        r.update(content='x',start_line=1,end_line=1)
        self.assertFalse(self.sources.validate(r,'bm25')['valid'])

    def test_wrong_path_and_line_identity(self):
        r=self.record(0,7);r['source_path']='repos/SPHinXsys/test.py'
        self.assertFalse(self.sources.validate(r,'bm25')['valid'])
        r=self.record(0,len(self.raw));r['end_line']=1
        self.assertFalse(self.sources.validate(r,'bm25')['valid'])
        r['source_path']='../outside.py';self.assertFalse(self.sources.validate(r,'bm25')['valid'])

    def test_multiple_fragments_jointly_satisfy_anchor(self):
        a=self.raw.index(b'important');split=a+4;b=a+len(b'important()')
        records=[self.record(a,split),self.record(split,b)]
        result=score(self.question(),records,'bm25',self.sources)
        self.assertTrue(result['complete_evidence']);self.assertEqual(result['component_fraction'],1)
        self.assertEqual(result['evidence_unit_precision'],0) # no single unit contains a full annotated line
        self.assertEqual(result['source_character_precision'],1)

    def test_overlap_filename_or_symbol_alone_is_not_success(self):
        a=self.raw.index(b'important');r=self.record(a,a+4);r['symbol']='important'
        result=score(self.question(),[r],'bm25',self.sources)
        self.assertFalse(result['complete_evidence']);self.assertEqual(result['component_fraction'],0)

    def test_all_of_and_frozen_alternatives(self):
        q=self.question();other=self.sources.anchors('SPHinXsim','test.py',3,3)
        q['components'].append({'id':'last','alternatives':[[other]]})
        result=score(q,[self.skill(2,2)],'skill',self.sources)
        self.assertFalse(result['complete_evidence']);self.assertTrue(result['partial_evidence']);self.assertEqual(result['component_fraction'],.5)
        q['components'][1]['alternatives'].append(q['components'][0]['alternatives'][0])
        self.assertTrue(score(q,[self.skill(2,2)],'skill',self.sources)['complete_evidence'])

    def test_precision_character_denominator_counts_repeats(self):
        e=self.skill(1,3);r=score(self.question(),[e,e],'skill',self.sources)
        self.assertEqual(r['retained_units'],2);self.assertEqual(r['returned_evidence_characters'],2*len(e['text']))
        self.assertEqual(r['annotated_relevant_characters'],2*len('important()'))
        self.assertTrue(r['complete_evidence'])

    def test_invalid_citation_cannot_satisfy_requirements(self):
        e=self.skill(2,2);e['citation']='incorrect'
        result=score(self.question(),[e],'skill',self.sources)
        self.assertFalse(result['complete_evidence']);self.assertEqual(result['citation_accuracy'],0)

    def test_truncation_only_credits_returned_lines(self):
        q=self.question();e=self.skill(1,1);e['truncated']=True
        r=score(q,[e],'skill',self.sources);self.assertFalse(r['complete_evidence'])
        self.assertEqual(r['annotated_relevant_characters'],0)

    def test_availability_distinguishes_scope_and_missing_chunks(self):
        q=self.question()
        self.assertEqual(availability(q,[],self.sources)['status'],'NOT_INDEXED')
        self.assertEqual(availability(q,None,self.sources,allowed=set())['status'],'OUT_OF_SCOPE')
        self.assertEqual(availability(q,None,self.sources,allowed={('SPHinXsim','test.py')})['status'],'EVIDENCE_AVAILABLE')
        r=self.record(0,len(self.raw));self.assertEqual(availability(q,[r],self.sources)['status'],'EVIDENCE_AVAILABLE')

    def test_common_character_cap_preserves_original_lines(self):
        e=self.skill(1,3);first=len(self.skill(1,1)['text'])
        out=whole_line_budget([e],'skill',self.sources,first+1)
        self.assertEqual(len(out),1);self.assertEqual(out[0]['content'],self.skill(1,1)['text'])
        self.assertTrue(self.sources.validate(out[0],'bm25')['valid'])
        self.assertLessEqual(sum(len(r['content']) for r in out),first+1)

    def test_empty_results_have_no_perfect_citation_precision(self):
        r=score(self.question(),[],'bm25',self.sources)
        self.assertFalse(r['complete_evidence']);self.assertIsNone(r['citation_accuracy']);self.assertIsNone(r['evidence_unit_precision'])

    def test_interval_union_and_duplicate_coverage(self):
        self.assertEqual(intervals_union([(2,5),(1,3),(2,5),(5,8),(10,10)]),[[1,8]])

    def test_precision_stream_keeps_repeated_cost_after_dedup(self):
        relevant=self.skill(2,2);unrelated=self.skill(1,1)
        r=score(self.question(),[relevant,unrelated],'skill',self.sources,[relevant,relevant,unrelated])
        self.assertEqual(r['retained_units'],2)
        self.assertEqual(r['returned_evidence_characters'],2*len(relevant['text'])+len(unrelated['text']))
        self.assertEqual(r['annotated_relevant_characters'],2*len('important()'))

    def test_replay_returned_reference_text_without_new_source_reads(self):
        session=self.project/'session';(session/'operations').mkdir(parents=True)
        state={'manifest':{'files':[{'repository':'SPHinXsim','path':'test.py','sha256':sha256(self.raw)}]},
           'trace':[{'sequence':1,'parameters':{'repo':'SPHinXsim','path':'test.py'}},
                    {'sequence':2,'parameters':{'repo':'SPHinXsim','path':'test.py'}}]}
        (session/'state.json').write_text(json.dumps(state))
        response={'result':{'references':[{'evidence':'important()','line':2}]}}
        for i in [1,2]:(session/'operations'/f'{i:04d}.json').write_text(json.dumps(response))
        retained,stream=skill_evidence_stream(session)
        self.assertEqual(len(retained),1);self.assertEqual(len(stream),2)
        self.assertTrue(self.sources.validate(retained[0],'skill')['valid'])


if __name__=='__main__':unittest.main()
