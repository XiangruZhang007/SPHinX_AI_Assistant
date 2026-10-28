"""Archived Step1.6 aggregation and post-run diagnostics, without file/path I/O.

The original metric function and diagnostic blocks are retained verbatim.
Post-run alternative reviews remain separate from frozen oracle scoring.
"""
import collections
import statistics
from .retrieval_evaluation_criteria import matches


def metric(rs):
 n=len(rs);single=[r for r in rs if not r['multi_evidence']];multi=[r for r in rs if r['multi_evidence']];available=[r for r in rs if r['evidence_available']]
 return {'n':n,'strict_hits_at_5':sum(r['strict']['hit_at_5'] for r in rs),'strict_hits_at_10':sum(r['strict']['hit_at_10'] for r in rs),'Hit_at_5':sum(r['strict']['hit_at_5'] for r in rs)/n if n else None,'Hit_at_10':sum(r['strict']['hit_at_10'] for r in rs)/n if n else None,'single_n':len(single),'single_hits_at_5':sum(r['strict']['hit_at_5'] for r in single),'single_MRR_at_5':statistics.mean(r['strict']['reciprocal_rank_at_5_single_only'] for r in single) if single else None,'multi_n':len(multi),'complete_multi_hits_at_5':sum(r['strict']['hit_at_5'] for r in multi),'complete_multi_hits_at_10':sum(r['strict']['hit_at_10'] for r in multi),'complete_multi_coverage_at_5':sum(r['strict']['hit_at_5'] for r in multi)/len(multi) if multi else None,'complete_multi_coverage_at_10':sum(r['strict']['hit_at_10'] for r in multi)/len(multi) if multi else None,'mean_partial_evidence_coverage_at_5':statistics.mean(r['strict']['partial_coverage_at_5'] for r in rs) if rs else None,'mean_partial_evidence_coverage_at_10':statistics.mean(r['strict']['partial_coverage_at_10'] for r in rs) if rs else None,'exact_evidence_available_questions':len(available),'availability_conditioned_hit5':sum(r['strict']['hit_at_5'] for r in available)/len(available) if available else None,'availability_conditioned_hit10':sum(r['strict']['hit_at_10'] for r in available)/len(available) if available else None,'ranking_failures_at_5_with_available_evidence':sum(r['evidence_available'] and not r['strict']['hit_at_5'] for r in rs),'structural_fragmentation_questions':sum(r.get('failure_type')=='STRUCTURAL_FRAGMENTATION' for r in rs),'coverage_gap_questions':sum(r.get('failure_type')=='COVERAGE_GAP' for r in rs)}


def aggregate_metrics(rows):
    metrics={'group':{},'category':{},'notes':{'primary':'A/B/C exact frozen target metrics; D excluded','A_denominator':'All40 strict questions including fragmentedQ30; availability-conditioned39 additionally reported','B':'14 unchanged legacy query texts. CLI5 keep original symbol expectations; other9 use newly frozen source passage bindings, not an original historical gold set.','semantic':'Supplementary fixed-anchor sufficiency diagnostics; no independent free-form answer/LLM adjudication','stale_oracles':'None identified against current source; broad legacy targets have specificity limitations, reported separately, not retrospectively relabeled stale.'}}
    for mode in ['documentation_only','combined']:
     for group in ['A','B','C']:
      rs=[r for r in rows if r['mode']==mode and r['group']==group]
      if rs:metrics['group'][mode+':'+group]=metric(rs)
    for group,category in sorted({(r['group'],r['category']) for r in rows if r['mode']=='combined' and r['group']!='D'}):metrics['category'][group+':'+category]=metric([r for r in rows if r['mode']=='combined' and r['group']==group and r['category']==category])
    metrics['combined_primary_all']=metric([r for r in rows if r['mode']=='combined' and r['group'] in ('A','B','C')]);metrics['stale_oracle_count']=0;metrics['unverified_oracle_count']=0;metrics['post_run_semantic_uniqueness_uncertain_count']=4;metrics['post_run_semantic_uniqueness_uncertain_ids']=['B-05','B-06','B-07','B-09']
    return metrics


def aggregate_and_diagnose(S, rows, corpus, historic):
    Q = {q['id']: q for q in S['questions']}
    byid = {r['chunk_id']: r for r in corpus}
    hist = {q['id']: q for q in historic['questions']}
    rowby = {(r['mode'], r['id']): r for r in rows}
    artifacts = {}

    def save(name, value):
        artifacts[name] = value

    metrics = aggregate_metrics(rows)
    # Source-observed alternate support is separate; frozen criteria/results never changed.
    manual={
     'DOC-Q01':{'status':'RELEVANT_ALTERNATIVE_WORKFLOW_AT5','note':'index.md Core workflow includes both generate and validate usage and direct command descriptions, but does not satisfy the frozen cli-usage.md Generate+Validate section targets. Strict FAIL retained.','match':lambda r:r['source_path']=='repos/SPHinXsim/docs/index.md' and 'sphinxsim generate' in r['content'] and 'sphinxsim validate' in r['content']},
     'DOC-Q35':{'status':'PARTIAL_RELEVANT_ALTERNATIVE_AT5','note':'Local instructions and CI policy paragraphs are relevant, but exact three-row test matrix is rank6. These paragraphs do not replace the historical matrix interval.','match':lambda r:r['source_path']=='repos/SPHinXsim/docs/llm-testing.md' and ('CI runs the mocked tests' in r['content'] or 'Test with live Ollama' in r['content'])},
     'B-05':{'status':'BROAD_CASE_LOCATION_ANSWER_SUPPORTED','note':'Top1 main preamble identifies the exact heat-transfer entry file; further body initialization fragments are retrieved. Broad original request finds the case, but the new specific247-248 thermal-step binding misses Top10. Oracle specificity limitation, no strict score change.','match':lambda r:r['source_path'].endswith('test_2d_heat_transfer/heat_transfer.cpp')},
     'B-06':{'status':'ALTERNATIVE_2D_CONFIGURATION_SUPPORTED','note':'Top2 examples/input/test_simulation_2d/config.json includes fluid_dynamics and 2D bounds. This supports the broad original question; frozen target was another current test JSON. Exact target failure is not proof of no useful configuration answer.','match':lambda r:r['source_path']=='repos/SPHinXsim/examples/input/test_simulation_2d/config.json' and 'fluid_dynamics' in r['content'] and '[0.0, 0.0]' in r['content']},
     'B-07':{'status':'RELATED_BASE_BUILDER_ONLY','note':'SimulationBuilder constructor is retrieved but alone does not explain dispatch to FluidSimulationBuilder/ContinuumSimulationBuilder. Broad query and new dispatch target need human interpretation; no complete success inferred.','match':lambda r:r['source_path'].endswith('base_simulation_builder.cpp') and r.get('symbol')=='SimulationBuilder'},
     'B-09':{'status':'RELEVANT_SCHEMA_SUBSECTION_SUPPORTED','note':'GeometriesConfig and shape-reference validation explain schema information, whereas frozen binding requires top-level SimulationConfig fields. Broad query does not uniquely specify that class; report the specificity caveat, not a silent oracle correction.','match':lambda r:r['source_path'].endswith('config/schemas.py') and r.get('symbol')=='GeometriesConfig'},
     'C-24':{'status':'RELATED_DELEGATING_CONSTRUCTOR_OVERLOAD','note':'Top1 is WeaklyCompressibleFluid(ConstructArgs)14-15, delegating to the frozen8-12 constructor. It is related backend constructor evidence, but the exact two-sided wiring target still fails.','match':lambda r:r['source_path'].endswith('materials/weakly_compressible_fluid.cpp') and r.get('symbol')=='WeaklyCompressibleFluid' and r['start_line']==14},
     'C-26':{'status':'RELATED_DELEGATING_DIFFUSION_OVERLOAD','note':'Top1 IsotropicDiffusion(ConstructArgs)27-28 delegates to the target21-25 overload. It supports backend identity but does not meet the fixed exact interval. Strict multi-evidence FAIL retained.','match':lambda r:r['source_path'].endswith('materials/diffusion_reaction.cpp') and r.get('symbol')=='IsotropicDiffusion' and r['start_line']==27}}
    alternative=[]
    for key,entry in manual.items():
     row=rowby[('combined',key)];found=[x for x in row['top_10'][:5] if entry['match'](x['record'])];assert found,key;alternative.append({'id':key,'status':entry['status'],'note':entry['note'],'actual_evidence':[{'rank':x['rank'],'chunk_id':x['chunk_id'],'path':x['record']['source_path'],'lines':[x['record']['start_line'],x['record']['end_line']],'content':x['record']['content']} for x in found],'frozen_scores_changed':False,'review_type':'Post-run observed-evidence interpretation; not a revised metric/oracle'})
    save('alternative-evidence-review.json',alternative)
    # Detect concrete dedup collisions and inspect competing content/roles. No new query/scoring.
    analysis=[]
    for row in rows:
     if row['mode']!='combined' or row['strict']['hit_at_5']:continue
     q=Q[row['id']];collisions=[];large=[]
     for i,a in enumerate(q['availability']):
      for targetid in a['matching_chunk_ids']:
       target=byid[targetid];key=(target['repository'],target['source_path'],target.get('symbol') or target.get('section_title'))
       if len(target['content'].encode())>5000:large.append({'criterion':i,'id':targetid,'bytes':len(target['content'].encode())})
       for item in row['top_10']:
        r=item['record'];other=(r['repository'],r['source_path'],r.get('symbol') or r.get('section_title'))
        if other==key and r['chunk_id']!=targetid and not matches(r,a['criterion']):collisions.append({'criterion':i,'expected_id':targetid,'retained_id':r['chunk_id'],'retained_rank':item['rank'],'dedup_key':list(key),'target_lines':[target['start_line'],target['end_line']],'retained_lines':[r['start_line'],r['end_line']],'observation':'Same source-path/symbol evidence key retained a different overload; search dedup cannot retain both.'})
     flags=[]
     if row.get('failure_type')=='STRUCTURAL_FRAGMENTATION':flags.append('STRUCTURAL_FRAGMENTATION_NOT_RANKING_FAILURE')
     if row['group']=='D':flags.append('KNOWN_COVERAGE_GAP_OR_PARTIAL_EXTRACTION_EXCLUDED_FROM_PRIMARY')
     if collisions:flags.append('OBSERVED_SAME_SYMBOL_DEDUP_COLLISION')
     if large:flags.append('OVERSIZED_AVAILABLE_TARGET_OBSERVED_NOT_PROVEN_CAUSE')
     if row['multi_evidence'] and any(x is not None for x in row['strict']['evidence_ranks']):flags.append('PARTIAL_MULTI_EVIDENCE_RETRIEVED')
     if row['id'] in manual:flags.append('ALTERNATIVE_OR_ORACLE_SPECIFICITY_REVIEW_AVAILABLE')
     rolecounts=dict(collections.Counter(x['record']['source_role'] for x in row['top_10'][:5]));flags.append('OBSERVED_COMPETING_ROLES_OR_SYMBOLS_SEE_CONTENT')
     analysis.append({'id':row['id'],'question':row['question'],'failure_type':row.get('failure_type',row.get('diagnostic_outcome')),'strict_ranks':row['strict']['evidence_ranks'],'flags':flags,'top5_roles':rolecounts,'dedup_collisions':collisions,'oversized_target_records':large,'observed_top5':[{'id':x['chunk_id'],'path':x['record']['source_path'],'symbol':x['record'].get('symbol'),'role':x['record']['source_role'],'lines':[x['record']['start_line'],x['record']['end_line']],'score_components':x['metadata']['score_components'],'content':x['record']['content']} for x in row['top_10'][:5]],'causality_limit':'Role/size/terminology observations are not ablation-proven causes. Source-key dedup collision is directly implied by unchanged search code and observed retained key.'})
    save('failure-analysis.json',analysis)
    # Exact per-question docs comparison: historical is reference; two current modes controlled same-run.
    doccomparison=[]
    for q in S['questions']:
     if q['group']!='A':continue
     d=rowby[('documentation_only',q['id'])];c=rowby[('combined',q['id'])];h=hist[q['id']]
     doccomparison.append({'id':q['id'],'question':q['question'],'historical':{'status':h['historical_doc_only_result'],'ranks':h['observed_ranks']},'documentation_only':d['strict'],'combined':c['strict'],'semantic_source_anchors_docs_only':d['semantic_anchor'],'semantic_source_anchors_combined':c['semantic_anchor'],'combined_union_coverage':c.get('union_coverage'),'alternative_support_review':next((x for x in alternative if x['id']==q['id']),None),'historical_comparison_limit':'Historical byte-identical docs and code evidence helps comparison; original complete process environment/hash seed not independently reproduced. Causal expansion assessment uses current same-run comparison only.'})
    save('documentation-comparison.json',doccomparison);save('metrics.json',metrics)
    p1=[{'id':r['id'],'question':r['question'],'outcome':r['diagnostic_outcome'],'exact_evidence_available':r['evidence_available'],'strict':r['strict'],'retrieved_partial_evidence_ids':r['retrieved_partial_evidence_ids'],'included_in_primary_denominator':False} for r in rows if r['mode']=='combined' and r['group']=='D'];save('p1-diagnostics.json',p1)
    # Persist benchmark limitations separately from frozen primary metrics; never retroactively alter denominator.
    limitations={'stale_oracle_count':0,'post_run_semantic_uniqueness_uncertain_count':4,'unverified_frozen_source_binding_count':0,'legacy_gold_standard_limitations':[{'id':x['id'],'interpretation':x['note']} for x in alternative if x['id'].startswith('B-')],'legacy_question_oracle_provenance':'9 queries had no original fixed passage oracle; newv2 source bindings are explicit operationalizations, not unique gold answers. CLI5 original symbol criteria retained.','denominator_changes_after_ranking':False,'post_run_alternatives_do_not_change_metrics':True,'semantic_adjudication':'Special frozen anchor supplement for knownDOC cases, manual observed alternatives separately; not a complete semantic answer correctness benchmark.'};save('oracle-limitations.json',limitations)
    return artifacts
