#!/usr/bin/env python3
"""Frozen Step 1.8B retrieval evaluation. No LLM calls or Agent execution.

prepare: audit availability, derive A1 without altering C1, then freeze.
execute: run independent workers twice, score, compare and report measurements.
worker: question-text-only input; no oracle loading or scoring.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter,defaultdict
import datetime
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from src.comparative_evaluation import (Sources,sha256,canonical_sha256,write_json,
    intervals_union,availability,score,whole_line_budget,skill_evidence_stream)

BENCH=PROJECT/'tests/retrieval_benchmarks/step_1_8b_comparative_v1.json'
NEW_FILES=['src/comparative_evaluation.py','scripts/evaluate_comparative_step_1_8b.py',
    'tests/test_comparative_evaluation.py','tests/retrieval_benchmarks/step_1_8b_comparative_v1.json']


def read(path):return json.loads(Path(path).read_text())
def hashfile(path):return sha256(Path(path).read_bytes())
def git(*args,cwd=PROJECT):return subprocess.check_output(['git',*args],cwd=cwd,text=True)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def environment(root):
    root=Path(root).resolve();base=PROJECT.parent/'SPHinX_AI_Assistant_artifacts/comparative_evaluations'
    if root==base or not root.is_relative_to(base):raise ValueError('Experiment outside approved comparative_evaluations root')
    env=read(root/'environment.json')
    if env['experiment']!=str(root) or env['project']!=str(PROJECT):raise ValueError('Preflight environment mismatch')
    return env


def unchanged(root):
    env=environment(root)
    assert git('rev-parse','HEAD').strip()==env['head']
    assert git('branch','--show-current').strip()==env['branch']
    assert not git('diff','--name-only').strip() and not git('diff','--cached','--name-only').strip()
    for rel,h in read(root/'S0-checksums.json').items():assert hashfile(PROJECT/rel)==h,rel
    assert hashfile(env['C1'])==env['C1_sha256']
    for repo,h in env['upstreams'].items():
        assert git('rev-parse','HEAD',cwd=PROJECT/'repos'/repo).strip()==h
        assert not git('status','--porcelain',cwd=PROJECT/'repos'/repo).strip()
    return env


def prepare(root):
    root=Path(root).resolve();env=unchanged(root)
    assert not (root/'freeze-lock.json').exists(),'Already frozen; do not overwrite'
    sources=Sources(PROJECT);benchmark=read(BENCH)
    rawlines=Path(env['C1']).read_bytes().splitlines(keepends=True)
    records=[json.loads(line) for line in rawlines];assert len(records)==11370
    manifests={scope:read(root/'scope-manifests'/(scope+'.json')) for scope in ['curated','expanded']}
    for scope,m in manifests.items():
        body=dict(m);internal=body.pop('manifest_sha256');assert canonical_sha256(body)==internal
        for f in m['files']:assert sha256(sources.read(f['repository'],f['path']))==f['sha256']
    allowed={s:{(f['repository'],f['path']) for f in m['files']} for s,m in manifests.items()}
    included=[];excluded=[];by_path=defaultdict(list)
    for record,line in zip(records,rawlines):
        path=Sources.split_path(record['repository'],record['source_path']);key=record['repository'],path
        by_path[key].append(record)
        (included if key in allowed['curated'] else excluded).append((record,line))
    assert included
    a1=root/'derived/A1-curated-corpus.jsonl';a1.parent.mkdir(parents=True,exist_ok=False)
    a1.write_bytes(b''.join(line for _,line in included))
    a1records=[r for r,_ in included]
    write_json(root/'derived/membership.json',{'source_corpus_sha256':env['C1_sha256'],'derived_sha256':hashfile(a1),
      'included_chunks':len(included),'excluded_chunks':len(excluded),'included_chunk_ids':[r['chunk_id'] for r,_ in included],
      'excluded_chunk_ids':[r['chunk_id'] for r,_ in excluded],
      'included_files':sorted({r['source_path'] for r,_ in included}),
      'source_roles':dict(Counter(r['source_role'] for r,_ in included)),
      'derivation':'Filter original JSONL lines in original order by exact repository/relative-path membership; no record changes.',
      'ranking_caveat':'Same R1 code/settings; subset changes BM25 collection statistics and distractors. Not production-equivalent.'})
    represented=[]
    for key in sorted(allowed['expanded']):
        rs=by_path[key];proofs=[sources.validate(r,'bm25') for r in rs]
        spans=intervals_union((p['byte_start'],p['byte_end']) for p in proofs if p['valid']);raw=sources.read(*key)
        represented.append({'repository':key[0],'path':key[1],'curated':key in allowed['curated'],'source_bytes':len(raw),
          'C1_records':len(rs),'C1_valid_records':sum(p['valid'] for p in proofs),'invalid_records':[p for p in proofs if not p['valid']],
          'represented_byte_union':spans,'represented_bytes':sum(b-a for a,b in spans),'whole_file_represented':spans==[[0,len(raw)]]})
    write_json(root/'source-representation-audit.json',represented)
    matrix=[]
    for q in benchmark['questions']:
        keys={(p['repository'],p['path']) for c in q['components'] for option in c['alternatives'] for p in option}
        for c in q['components']:
            for option in c['alternatives']:
                for piece in option:
                    raw=sources.read(piece['repository'],piece['path']);assert sha256(raw)==piece['source_sha256']
                    for anchor in piece['anchors']:assert raw[anchor['byte_start']:anchor['byte_end']].decode()==anchor['text']
        rs=[r for key in sorted(keys) for r in by_path[key]]
        a1rs=[r for r in rs if (r['repository'],Sources.split_path(r['repository'],r['source_path'])) in allowed['curated']]
        row={'id':q['id'],'A0':availability(q,rs,sources),'A1':availability(q,a1rs,sources),
             'B-D':availability(q,None,sources,allowed['curated']),'C-D':availability(q,None,sources,allowed['expanded'])}
        q['evidence_availability']=row
        matrix.append(row)
    write_json(root/'availability-matrix.json',matrix)
    write_json(BENCH,benchmark)
    write_json(root/'frozen-benchmark.json',benchmark)
    write_json(root/'frozen-reference-answers.json',[{k:q[k] for k in ['id','question','reference_answer','expected_answer_elements','acceptable_equivalents','unsupported_claims','citation_expectation']} for q in benchmark['questions']])
    write_json(root/'public-questions.json',[{'id':q['id'],'question':q['question']} for q in benchmark['questions']])
    write_json(root/'frozen-scoring-rubric.json',benchmark['rubric'])
    budget=read(PROJECT/'configs/skill_doc_cli_scope_v1.json')['default_budget']
    config={'schema_version':1,'project':str(PROJECT),'principal_retrieval_conditions':['A0','B-D','C-D'],
      'principal_answer_conditions':['A0','B-D','C-D'],'diagnostic_conditions':['A1'],'budget':budget,
      'BM25':{'k1':1.5,'b':0.75,'primary_top_k':5,'diagnostic_top_k':10,'query':'Exact frozen question text, unchanged.'},
      'Skill':{'entrypoint':'src.skill_guided_retrieval.Session.create / Session.explore','query':'Exact frozen question text, no oracle arguments or manual interventions.','repository_category_overrides':None},
      'corpora':{'A0':env['C1'],'A1':str(a1)},'scope_manifest_ids':{s:m['manifest_sha256'] for s,m in manifests.items()},
      'scope_manifests':{s:str(root/'scope-manifests'/(s+'.json')) for s in manifests},
      'skill_session_root':env['skill_sessions'],'hash_seed':'0','replicates':2,
      'budget_matched_source_characters':4000,
      'retained_source_units':'BM25 returned chunks; Skill deduplicated returned evidence records plus exact source snippets actually returned in reference lists. Path/name-only reference metadata gives no source credit.',
      'precision_stream':'Unit precision on retained unique units. Character precision counts repeated returned source texts, including reference snippets, using actual operation outputs. V1 retained-record cap applies to V1 records; additional reference snippets are separately labeled source units within the same charged character/read budgets.',
      'token_estimate':'utf8_bytes/4 heuristic, not provider/model tokenizer usage.',
      'agent_status':'NOT_EXECUTED_CONTEXT_ISOLATION_UNAVAILABLE','answer_status':'ANSWER_EVALUATION_NOT_EXECUTED_AUTHORIZATION_MISSING',
      'external_api_attempts':0,'shared_character_cap_is_diagnostic_only':True}
    write_json(root/'frozen-experiment-config.json',config)
    # Read model adapter as syntax only: no importing SDK, reading env or credentials.
    tree=ast.parse((PROJECT/'src/grounded_answer_v1.py').read_text())
    system=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SYSTEM_PROMPT' for t in n.targets))
    write_json(root/'answer-prompt-template.json',{'status':config['answer_status'],'system':system,
      'user_template':'Question: {exact_question}\nEvidence: {condition_specific_validated_source_evidence}\nAnswer only from evidence with citations.',
      'model':None,'credentials_inspected':False,'requests':0,'actual_answer_prompts_generated':False,
      'integration_inspected':'src/grounded_answer_v1.py uses LLM_API_KEY/LLM_MODEL and optional LLM_BASE_URL; environment values not read.'})
    # This preparation records provenance only; no ranking has occurred.
    oldresults=read(PROJECT/'tests/retrieval_benchmarks/step_1_6_frozen_v2_support/reference-results.json')
    write_json(root/'historical-question-provenance.json',[{'id':q['id'],'historical_id':q['provenance']['historical_question_id'],
      'historical_results':[{'mode':r['mode'],'strict':r['strict']} for r in oldresults if r['id']==q['provenance']['historical_question_id']],
      'interpretation':'Historical scores are not new scores and were not used as a selection filter. New benchmark has explicit source-union criteria.'} for q in benchmark['questions']])
    frozen={str(p.relative_to(root)):hashfile(p) for p in [root/'frozen-benchmark.json',root/'public-questions.json',root/'frozen-reference-answers.json',root/'frozen-scoring-rubric.json',root/'availability-matrix.json',root/'source-representation-audit.json',root/'frozen-experiment-config.json',a1]}
    implementation={rel:hashfile(PROJECT/rel) for rel in NEW_FILES}
    for rel in NEW_FILES:
        dest=root/'frozen-implementation'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((PROJECT/rel).read_bytes())
    lock={'frozen_utc':now(),'first_retrieval_not_yet_started':True,'files':frozen,'implementation':implementation,
      'protected_implementation':{'src/retrieval_v1.py':env['R1_sha256']},'benchmark_sha256':hashfile(BENCH),
      'scope_manifest_files':{s:hashfile(root/'scope-manifests'/(s+'.json')) for s in manifests}}
    write_json(root/'freeze-lock.json',lock)
    print(json.dumps({'frozen':str(root/'freeze-lock.json'),'benchmark_sha256':lock['benchmark_sha256'],
      'A1_records':len(included),'availability':{cond:dict(Counter(r[cond]['status'] for r in matrix)) for cond in ['A0','A1','B-D','C-D']}},indent=2),flush=True)


def verify_freeze(root):
    lock=read(root/'freeze-lock.json')
    for rel,h in lock['files'].items():assert hashfile(root/rel)==h,rel
    for rel,h in lock['implementation'].items():assert hashfile(PROJECT/rel)==h,rel
    for rel,h in lock['protected_implementation'].items():assert hashfile(PROJECT/rel)==h,rel
    for scope,h in lock['scope_manifest_files'].items():assert hashfile(root/'scope-manifests'/(scope+'.json'))==h
    return lock


def worker(config_path,questions_path,condition,replicate,output):
    # No evaluation benchmark/oracle is read by this worker. It is deterministic, not a blind Agent.
    cfg=read(config_path);questions=read(questions_path);output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    timings=[];rows=[];session_index=[]
    if condition in {'A0','A1'}:
        from src.retrieval_v1 import RetrievalV1
        start=time.perf_counter();retriever=RetrievalV1(Path(cfg['corpora'][condition]),**{k:cfg['BM25'][k] for k in ['k1','b']})
        preparation=time.perf_counter()-start
        for q in questions:
            start=time.perf_counter();found5=retriever.search(q['question'],top_k=5);d5=time.perf_counter()-start
            start=time.perf_counter();found10=retriever.search(q['question'],top_k=10);d10=time.perf_counter()-start
            assert [f.record['chunk_id'] for f in found5]==[f.record['chunk_id'] for f in found10[:5]]
            rows.append({'id':q['id'],'condition':condition,'top_5_ids':[x.record['chunk_id'] for x in found5],
              'top_10':[{'rank':i,'chunk_id':f.record['chunk_id'],'score':f.score,'score_hex':f.score.hex(),
                        'score_components':f.as_dict(include_debug=True)['score_components'],'record':f.record} for i,f in enumerate(found10,1)]})
            timings.append({'id':q['id'],'search5_seconds':d5,'search10_seconds':d10,'preparation_amortized_seconds':preparation/len(questions)})
        timing={'index_preparation_seconds':preparation,'rows':timings,'records_scored_per_query':len(retriever.records),
          'input_corpus_bytes':Path(cfg['corpora'][condition]).stat().st_size,'query_source_reads':0,'index_reused_across_questions':True}
    else:
        from src.skill_guided_retrieval import Session
        scope='curated' if condition=='B-D' else 'expanded'
        for q in questions:
            directory=Path(cfg['skill_session_root'])/replicate/condition/q['id']
            start=time.perf_counter();session=Session.create(PROJECT,directory,scope)
            prepared=time.perf_counter()-start
            assert session.state['budget']==cfg['budget']
            assert session.state['manifest']['manifest_sha256']==cfg['scope_manifest_ids'][scope]
            start=time.perf_counter();summary=session.explore(q['question']);elapsed=time.perf_counter()-start
            state=read(directory/'state.json');retained,stream=skill_evidence_stream(directory)
            allowed={(f['repository'],f['path']) for f in state['manifest']['files']}
            assert all(v<=cfg['budget'][k] for k,v in state['used'].items())
            assert len(state['files_explored'])<=cfg['budget']['files_explored']
            assert len(state['evidence'])<=cfg['budget']['evidence_records']
            accounted=0
            for t in state['trace']:
                assert all((a['repository'],a['path']) in allowed for a in t['source_accesses'])
                result=read(directory/'operations'/f"{t['sequence']:04d}.json")['result']
                accounted+=len(result['evidence']['text']) if result.get('evidence') else 0
                accounted+=sum(len(e['text']) for e in result.get('matches',[]))
                if 'references' in result:accounted+=len(json.dumps(result['references'],ensure_ascii=False,sort_keys=True))
            assert accounted==state['used']['returned_characters']
            assert sum(len(e['text']) for e in stream)<=accounted
            rows.append({'id':q['id'],'condition':condition,'evidence':retained,'returned_source_stream':stream,
              'used':state['used'],'files_explored':state['files_explored'],'trace':state['trace'],
              'summary':summary,'scope_manifest_sha256':state['manifest']['manifest_sha256'],
              'V1_retained_evidence_records':len(state['evidence']),
              'source_characters_scanned':sum(a['characters_scanned'] for t in state['trace'] for a in t['source_accesses'])})
            meta=read(directory/'execution-metadata.json')
            timings.append({'id':q['id'],'manifest_preparation_seconds':prepared,'retrieval_seconds':elapsed,
                'source_bytes_scanned_at_setup':meta['administrative_manifest_scan_bytes'],
                'recorded_tool_duration_seconds':sum(read(p)['duration_seconds'] for p in (directory/'runtime').glob('*.json'))})
            session_index.append({'id':q['id'],'condition':condition,'replicate':replicate,'session_id':f'{replicate}/{condition}/{q["id"]}',
                'path':str(directory),'evidence_sha256':hashfile(directory/'evidence.json'),'trace_sha256':hashfile(directory/'trace.json'),
                'state_sha256':hashfile(directory/'state.json'),'scope_manifest_sha256':state['manifest']['manifest_sha256']})
        timing={'rows':timings,'manifest_rebuilt_per_question':True}
    write_json(output/'results.json',rows);write_json(output/'runtime.json',timing)
    write_json(output/'session-index.json',session_index)
    print(json.dumps({'condition':condition,'replicate':replicate,'questions':len(rows)}),flush=True)


def aggregate(scored,availability_rows):
    out={}
    for condition in ['A0','A1','B-D','C-D']:
        selected=[r for r in scored if r['condition']==condition and r['view']=='primary']
        available={r['id'] for r in availability_rows if r[condition]['status']=='EVIDENCE_AVAILABLE'}
        multi=[r for r in selected if r['multi_file_required']]
        valid=sum(r['metrics']['citation_valid'] for r in selected);total=sum(r['metrics']['citation_total'] for r in selected)
        def mean_metric(key):
            values=[r['metrics'][key] for r in selected if r['metrics'][key] is not None]
            return statistics.mean(values) if values else None
        out[condition]={'questions':len(selected),'complete_evidence':sum(r['metrics']['complete_evidence'] for r in selected),
         'partial_evidence':sum(r['metrics']['partial_evidence'] for r in selected),
         'mean_component_coverage':mean_metric('component_fraction'),
         'multi_file_complete':sum(r['metrics']['complete_evidence'] for r in multi),'multi_file_questions':len(multi),
         'citations_valid':valid,'citations_total':total,'citation_accuracy':valid/total if total else None,
         'mean_evidence_unit_precision':mean_metric('evidence_unit_precision'),'mean_source_character_precision':mean_metric('source_character_precision'),
         'precision_defined_questions':sum(r['metrics']['evidence_unit_precision'] is not None for r in selected),
         'zero_evidence_questions':sum(r['metrics']['retained_units']==0 for r in selected),
         'evidence_characters':sum(r['metrics']['returned_evidence_characters'] for r in selected),
         'retained_units':sum(r['metrics']['retained_units'] for r in selected),
         'budget_exhausted':sum(r.get('budget_exhausted',False) for r in selected),
         'fully_available_denominator':len(available),'complete_among_fully_available':sum(r['metrics']['complete_evidence'] for r in selected if r['id'] in available),
         'answer_correctness':None}
    return out


def execute(root):
    root=Path(root).resolve();env=unchanged(root);lock=verify_freeze(root)
    assert not (root/'retrieval-start.json').exists(),'Retrieval already started; do not overwrite/retry silently'
    write_json(root/'retrieval-start.json',{'started_utc':now(),'freeze_lock_sha256':hashfile(root/'freeze-lock.json'),
      'benchmark_sha256':lock['benchmark_sha256'],'frozen_utc':lock['frozen_utc']})
    cfg=read(root/'frozen-experiment-config.json');benchmark=read(root/'frozen-benchmark.json')
    commands=[];process_env=dict(os.environ,PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1')
    for rep in ['run1','run2']:
        for cond in ['A0','A1','B-D','C-D']:
            verify_freeze(root)
            cmd=[sys.executable,'-B',str(Path(__file__).resolve()),'worker','--config',str(root/'frozen-experiment-config.json'),
              '--questions',str(root/'public-questions.json'),'--condition',cond,'--replicate',rep,'--output',str(root/'raw'/rep/cond)]
            started=time.perf_counter();p=subprocess.run(cmd,cwd=PROJECT,env=process_env,capture_output=True,text=True)
            commands.append({'command':cmd,'cwd':str(PROJECT),'environment_overrides':{'PYTHONHASHSEED':'0','PYTHONDONTWRITEBYTECODE':'1'},
              'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'wall_seconds':time.perf_counter()-started})
            write_json(root/'executed-retrieval-commands.json',commands)
            if p.returncode:raise RuntimeError('Worker failed; retained raw state, no automatic larger-budget retry: '+p.stderr)
            print(p.stdout.strip(),flush=True)
    reproducibility=[]
    for cond in ['A0','A1','B-D','C-D']:
        a=root/'raw/run1'/cond/'results.json';b=root/'raw/run2'/cond/'results.json'
        assert a.read_bytes()==b.read_bytes(),cond
        checks={'raw_results_sha256':hashfile(a)}
        if cond in {'B-D','C-D'}:
            for q in benchmark['questions']:
                first=Path(env['skill_sessions'])/'run1'/cond/q['id'];second=Path(env['skill_sessions'])/'run2'/cond/q['id']
                for rel in ['state.json','scope-audit.json','trace.json','evidence.json','exploration-summary.json']:
                    assert (first/rel).read_bytes()==(second/rel).read_bytes(),(cond,q['id'],rel)
                for p in (first/'operations').glob('*.json'):assert p.read_bytes()==(second/'operations'/p.name).read_bytes()
        reproducibility.append({'condition':cond,'substantive_byte_identical':True,**checks,'volatile_excluded':['runtime files','session-index absolute replicate paths','execution-metadata creation time']})
    sources=Sources(PROJECT);scored=[];failures=[];runtime={};sessions=[];answer_inputs=[]
    matrix=read(root/'availability-matrix.json');matrix_by={r['id']:r for r in matrix}
    for cond in ['A0','A1','B-D','C-D']:
        rows=read(root/'raw/run1'/cond/'results.json');runtime[cond]=read(root/'raw/run1'/cond/'runtime.json')
        sessions.extend(read(root/'raw/run1'/cond/'session-index.json'));sessions.extend(read(root/'raw/run2'/cond/'session-index.json'))
        for q,row in zip(benchmark['questions'],rows):
            assert q['id']==row['id'];kind='bm25' if cond.startswith('A') else 'skill'
            primary=[x['record'] for x in row['top_10'][:5]] if kind=='bm25' else row['evidence']
            stream=None if kind=='bm25' else row['returned_source_stream']
            views={'primary':(primary,kind,stream)}
            if kind=='bm25':views['top10_diagnostic']=([x['record'] for x in row['top_10']],kind,None)
            bounded=whole_line_budget(primary,kind,sources,4000);views['4000_characters_diagnostic']=(bounded,'bm25',None)
            for view,(records,mode,returned) in views.items():
                metrics=score(q,records,mode,sources,returned)
                entry={'id':q['id'],'category':q['category'],'condition':cond,'view':view,'multi_file_required':q['multi_file_required'],
                   'availability':matrix_by[q['id']][cond]['status'],'metrics':metrics}
                if kind=='skill':entry.update(budget_exhausted=row['summary']['budget_exhausted'],used=row['used'],
                  source_characters_scanned=row['source_characters_scanned'],V1_retained_evidence_records=row['V1_retained_evidence_records'])
                scored.append(entry)
                if view=='primary' and not metrics['complete_evidence']:
                    causes=[];av=entry['availability']
                    if av!='EVIDENCE_AVAILABLE':causes.append({'cause':'SOURCE_AVAILABILITY','status':av})
                    if metrics['citation_valid']<metrics['citation_total']:causes.append({'cause':'INCORRECT_OR_UNVERIFIABLE_CITATION'})
                    if kind=='skill':
                        if row['summary']['budget_exhausted']:causes.append({'cause':'BUDGET_EXHAUSTED','attribution':'Observed stopping event; not proof it is the sole cause.'})
                        if any(t['truncations'] for t in row['trace']):causes.append({'cause':'TRUNCATED_RETURN','attribution':'Observed; not an ablation-proven sole cause.'})
                        if any(t['status']=='ERROR' for t in row['trace']):causes.append({'cause':'TOOL_ERROR','events':[t for t in row['trace'] if t['status']=='ERROR']})
                        causes.append({'cause':'REQUIRED_SOURCE_ANCHORS_NOT_RETURNED','attribution':'Missed section/symbol/reference or lexical selection; no single root cause asserted without a control.'})
                    elif av=='EVIDENCE_AVAILABLE':
                        top10=score(q,[x['record'] for x in row['top_10']],'bm25',sources)
                        causes.append({'cause':'TOP5_SELECTION_MISS','complete_at_10':top10['complete_evidence'],'attribution':'Available frozen source evidence absent from returned Top5; ranking/dedup attribution not independently ablated.'})
                    failures.append({'id':q['id'],'condition':cond,'components':metrics['components'],'causes':causes})
            if cond in cfg['principal_answer_conditions']:
                answer_inputs.append({'id':q['id'],'condition':cond,'question':q['question'],'status':cfg['answer_status'],
                   'source_evidence':primary,'actual_llm_answer':None,'api_requests':0,'not_an_executed_model_prompt':True})
    write_json(root/'per-question-scores.json',scored);write_json(root/'failure-analysis.json',failures)
    write_json(root/'retrieval-runtime.json',runtime);write_json(root/'session-index.json',sessions)
    write_json(root/'unexecuted-answer-inputs.json',answer_inputs)
    aggregate1=aggregate(scored,matrix);write_json(root/'aggregate-metrics.json',aggregate1)
    categories={cat:aggregate([r for r in scored if r['category']==cat],[r for r in matrix if next(q['category'] for q in benchmark['questions'] if q['id']==r['id'])==cat]) for cat in benchmark['categories']}
    write_json(root/'category-metrics.json',categories)
    paired=[]
    for left,right in [('A0','A1'),('A0','B-D'),('A1','B-D'),('B-D','C-D'),('A0','C-D')]:
        mapping={(r['condition'],r['id']):r for r in scored if r['view']=='primary'}
        groups=defaultdict(list)
        for q in benchmark['questions']:
            a=mapping[left,q['id']]['metrics']['complete_evidence'];b=mapping[right,q['id']]['metrics']['complete_evidence']
            groups['both' if a and b else 'left_only' if a else 'right_only' if b else 'neither'].append(q['id'])
        paired.append({'left':left,'right':right,'evidence_completeness':dict(groups),
           'answer_correctness_comparison':cfg['answer_status']})
    write_json(root/'paired-comparisons.json',paired)
    # Re-score all second-process raw outputs; equality is substantive, not inferred from logs.
    second=[]
    for cond in ['A0','A1','B-D','C-D']:
        rows=read(root/'raw/run2'/cond/'results.json')
        for q,row in zip(benchmark['questions'],rows):
            kind='bm25' if cond.startswith('A') else 'skill'
            records=[x['record'] for x in row['top_10'][:5]] if kind=='bm25' else row['evidence']
            metrics=score(q,records,kind,sources,None if kind=='bm25' else row['returned_source_stream'])
            first=next(r['metrics'] for r in scored if r['condition']==cond and r['id']==q['id'] and r['view']=='primary')
            assert metrics==first,(cond,q['id']);second.append({'id':q['id'],'condition':cond,'scoring_identical':True})
    write_json(root/'reproducibility.json',{'independent_process_conditions':reproducibility,'second_run_primary_scoring':second,'all_identical':True})
    verify_freeze(root);unchanged(root)
    print(json.dumps(aggregate1,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ['prepare','execute']:
        p=sub.add_parser(name);p.add_argument('--experiment',required=True,type=Path)
    p=sub.add_parser('worker')
    for name in ['config','questions','output']:p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--condition',choices=['A0','A1','B-D','C-D'],required=True);p.add_argument('--replicate',choices=['run1','run2'],required=True)
    a=parser.parse_args()
    if a.command=='prepare':prepare(a.experiment)
    elif a.command=='execute':execute(a.experiment)
    else:worker(a.config,a.questions,a.condition,a.replicate,a.output)


if __name__=='__main__':main()
