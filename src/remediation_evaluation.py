"""Controlled Step 1.7 experiments; frozen criteria imported without modification.

Historical exact-reproduction entry points and manifests remain separate.
Current evidence availability is computed before ranking, never written to oracles.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sys
from .retrieval_evaluation_criteria import matches, outcome, union_covers
from .retrieval_evaluation_io import (file_sha256, canonical_sha256, check_checksum,
    read_json, validate_structure, expected_inventory)
from .retrieval_evaluation_reports import aggregate_metrics


def current_availability(spec, records, mode):
    report = []
    for q in spec['questions']:
        if mode == 'documentation_only' and q['group'] != 'A':
            continue
        evidence = []
        for c in q['requirements']:
            exact = [r for r in records if matches(r, c)]
            partial = [r for r in records if r['source_path'] == c['path']
                       and c.get('range') and r['start_line'] <= c['range'][1]
                       and r['end_line'] >= c['range'][0]]
            evidence.append({'criterion': c, 'matching_chunk_ids': [r['chunk_id'] for r in exact],
                'matching_ranges': [[r['start_line'], r['end_line']] for r in exact],
                'overlapping_chunk_ids_diagnostic_only': [r['chunk_id'] for r in partial]})
        complete = all(e['matching_chunk_ids'] for e in evidence)
        status = ('COMPLETE_EVIDENCE' if complete else 'PARTIAL_EVIDENCE' if any(
            e['matching_chunk_ids'] or e['overlapping_chunk_ids_diagnostic_only'] for e in evidence)
            else 'COVERAGE_GAP')
        report.append({'id': q['id'], 'mode': mode, 'group': q['group'],
            'historical_evidence_available': q['exact_evidence_available'],
            'historical_pre_ranking_coverage': q.get('pre_ranking_coverage'),
            'current_evidence_available': complete, 'current_pre_ranking_coverage': status,
            'included_in_primary_denominator': q['group'] != 'D', 'requirements': evidence})
    return report


def validate_manifest(benchmark, corpus, documentation, manifest_file, output, project, retriever_file):
    if output.exists() or output.is_symlink():
        raise FileExistsError(f'Output directory exists: {output}')
    manifest = read_json(manifest_file)
    if manifest.get('schema_version') != 1 or not manifest.get('experiment_id'):
        raise ValueError('Expected versioned experiment manifest and identity')
    check_checksum(benchmark, manifest['benchmark_sha256'])
    if manifest['benchmark_sha256'] != 'bac0b31b74410eaff00246e628187ee07c4aaefb8747ad076f090393b71d1ab7':
        raise ValueError('Step1.6 frozen benchmark must remain byte-identical')
    spec = read_json(benchmark); validate_structure(spec)
    if canonical_sha256(spec['config']) != manifest['configuration_sha256']:
        raise ValueError('Frozen retrieval configuration changed')
    check_checksum(retriever_file, manifest['retriever_sha256'])
    required = {'src/retrieval_evaluation_criteria.py', 'src/retrieval_evaluation_io.py',
                'src/retrieval_evaluation_reports.py', 'src/remediation_evaluation.py',
                'scripts/evaluate_remediation_step_1_7.py'}
    if not required <= manifest['implementation_files'].keys():
        raise ValueError('Incomplete evaluation dependency lock')
    if manifest['implementation_files']['src/retrieval_evaluation_criteria.py'] != 'd9a126dbe4228fb3b9d6740c48d15ded9a086eef4d07161b67229ff08e287364':
        raise ValueError('Frozen scoring criteria changed')
    for rel, h in manifest['implementation_files'].items():
        if Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError('Invalid implementation path')
        check_checksum(project / rel, h)
    components = {}
    for role, path in [('combined', corpus), ('documentation_only', documentation)]:
        check_checksum(path, manifest['corpora'][role]['sha256'])
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        if not records or len(records) != manifest['corpora'][role]['record_count']:
            raise ValueError('Corpus count differs from experiment manifest')
        if len({r['chunk_id'] for r in records}) != len(records):
            raise ValueError('Duplicate chunk IDs')
        for r in records:
            parts = Path(r['source_path']).parts
            if (len(parts) < 3 or parts[:2] != ('repos', r['repository'])
                    or r['repository'] not in ('SPHinXsys', 'SPHinXsim') or '..' in parts
                    or not isinstance(r['content'], str) or not 1 <= r['start_line'] <= r['end_line']):
                raise ValueError('Invalid record provenance')
        components[role] = records
    if [r for r in components['combined'] if r['source_role'] == 'documentation'] != components['documentation_only']:
        raise ValueError('Documentation identity/content/order differ from combined component')
    return spec, manifest, components


def load_retriever(path):
    # The supplied implementation is hash-checked before execution; both R0/R1
    # have stdlib-only imports. No active-worktree import can shadow R0.
    name = '_step17_retriever_' + file_sha256(path)[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.RetrievalV1

def evaluate(specification, corpus, documentation_corpus, retriever_factory, counts, availability, progress=None):
    S = specification
    results = []
    for mode,path,questions in [('documentation_only',str(documentation_corpus),[q for q in S['questions'] if q['group']=='A']),('combined',str(corpus),S['questions'])]:
     retriever=retriever_factory(Path(path),k1=S['config']['k1'],b=S['config']['b']);assert len(retriever.records)==counts[mode]
     for q in questions:
      a=availability[(mode,q['id'])]
      q={**q,'exact_evidence_available':a['current_evidence_available'],'pre_ranking_coverage':a['current_pre_ranking_coverage']}
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
