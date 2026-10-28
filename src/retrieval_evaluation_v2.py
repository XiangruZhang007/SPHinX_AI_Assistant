"""Step1.6 v2 evaluation body preserved from its archived worker.

Only corpus-path injection and progress reporting were parameterized. Ranking,
criteria, result fields and diagnostics preserve the archived implementation.
"""
from pathlib import Path
from .retrieval_v1 import RetrievalV1
from .retrieval_evaluation_criteria import matches, outcome, union_covers


def evaluate(specification, corpus, documentation_corpus, progress=None):
    S = specification
    results = []
    for mode,path,questions in [('documentation_only',str(documentation_corpus),[q for q in S['questions'] if q['group']=='A']),('combined',str(corpus),S['questions'])]:
     retriever=RetrievalV1(Path(path),k1=S['config']['k1'],b=S['config']['b']);assert len(retriever.records)==(354 if mode=='documentation_only' else 11338)
     for q in questions:
      found=retriever.search(q['question'],top_k=10);top5=retriever.search(q['question'],top_k=5)
      assert [(x.record['chunk_id'],x.score) for x in found[:5]]==[(x.record['chunk_id'],x.score) for x in top5]
      records=[x.record for x in found];strict=outcome(records,q['requirements']);semantic_criteria=S['semantic_supplement']['questions'].get(q['id'],q['requirements']);semantic=outcome(records,semantic_criteria)
      row={'id':q['id'],'mode':mode,'group':q['group'],'category':q['category'],'question':q['question'],'evidence_available':q['exact_evidence_available'],'primary_score_eligible':q['primary_score_eligible'],'multi_evidence':len(q['requirements'])>1,'strict':strict,'semantic_anchor':semantic,'semantic_method':'Separate pre-ranking source anchor supplement' if q['id'] in S['semantic_supplement']['questions'] else 'Same fixed source anchor; not independent free-form answer adjudication','top_10':[{'rank':i,'chunk_id':x.record['chunk_id'],'score':x.score,'score_hex':x.score.hex(),'metadata':x.as_dict(include_debug=True),'record':x.record} for i,x in enumerate(found,1)],'top_5_ids':[x.record['chunk_id'] for x in found[:5]],'top_10_ids':[x.record['chunk_id'] for x in found]}
      if q['id']=='DOC-Q30':
       c=q['requirements'][0];row['union_coverage']={str(k):union_covers(records[:k],c['path'],*c['range']) for k in (5,10)}
      if q['group']=='D':
       if q['exact_evidence_available']:row['diagnostic_outcome']='RETRIEVED' if strict['hit_at_5'] else 'RETRIEVAL_FAILURE'
       else:row['diagnostic_outcome']=q['pre_ranking_coverage']
       row['retrieved_partial_evidence_ids']=[r['chunk_id'] for r in records if any(c.get('range') and c['path']==r['source_path'] and r['start_line']<=c['range'][1] and r['end_line']>=c['range'][0] for c in q['requirements'])]
      elif not q['exact_evidence_available']:row['failure_type']='STRUCTURAL_FRAGMENTATION' if q['id']=='DOC-Q30' else 'COVERAGE_GAP'
      else:row['failure_type']='NONE' if strict['hit_at_5'] else 'RETRIEVAL_FAILURE'
      # Fragments are diagnosed separately; unrelated same-topic text is not auto-labeled relevant.
      row['alternative_evidence']={'semantic_supplement_sufficient_at_5':semantic['hit_at_5'],'expected_source_partial_fragments_at_5':[r['chunk_id'] for r in records[:5] if any(c.get('range') and r['source_path']==c['path'] and r['start_line']<=c['range'][1] and r['end_line']>=c['range'][0] for c in q['requirements'])],'other_source_relevance':'NOT_AUTOMATICALLY_ADJUDICATED; see post-run failure review'}
      results.append(row)
      if progress is not None: progress({'mode':mode,'id':q['id'],'strict_hit5':strict['hit_at_5'],'strict_hit10':strict['hit_at_10']})
    return results
