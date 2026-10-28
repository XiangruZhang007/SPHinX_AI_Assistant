#!/usr/bin/env python3
"""Run a locked remediation experiment, allowing measured differences from B0."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.remediation_evaluation import (validate_manifest, current_availability, load_retriever, evaluate)
from src.retrieval_evaluation_io import (read_json, file_sha256, compare_results, expected_inventory, check_checksum)
from src.retrieval_evaluation_reports import aggregate_metrics


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('benchmark', 'corpus', 'documentation-corpus', 'output-dir', 'manifest'):
        p.add_argument('--' + arg, type=Path, required=True)
    p.add_argument('--retriever', type=Path, default=PROJECT / 'src/retrieval_v1.py')
    return p


def write(path, value, sort=False):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=sort); f.write('\n')


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        spec, manifest, components = validate_manifest(a.benchmark, a.corpus, a.documentation_corpus,
            a.manifest, a.output_dir, PROJECT, a.retriever)
        if os.environ.get('PYTHONHASHSEED') != '0':
            raise ValueError('Start Python with PYTHONHASHSEED=0')
        reference_path = a.benchmark.parent / 'step_1_6_frozen_v2_support/reference-results.json'
        check_checksum(reference_path, manifest['reference_results_sha256'])
        factory = load_retriever(a.retriever)
        report = [*current_availability(spec, components['documentation_only'], 'documentation_only'),
                  *current_availability(spec, components['combined'], 'combined')]
        a.output_dir.mkdir(parents=True, exist_ok=False)
        write(a.output_dir / 'pre-ranking-availability.json', report)
        write(a.output_dir / 'effective-inputs.json', {
            'experiment_id': manifest['experiment_id'], 'manifest': manifest,
            'manifest_sha256': file_sha256(a.manifest),
            'paths': {name: str(getattr(a, name).resolve()) for name in
                ('benchmark', 'corpus', 'documentation_corpus', 'retriever', 'output_dir', 'manifest')},
            'actual_retriever_module': sys.modules[factory.__module__].__file__,
            'python': sys.executable, 'python_version': sys.version, 'hash_seed': '0',
            'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
            'configuration': spec['config'], 'argv': sys.argv if argv is None else argv})
        rows = evaluate(spec, a.corpus, a.documentation_corpus, factory,
            {mode: len(records) for mode, records in components.items()},
            {(r['mode'], r['id']): r for r in report},
            progress=lambda r: print(json.dumps(r, sort_keys=True), flush=True))
        if len(rows) != 128 or {(r['mode'], r['id']) for r in rows} != expected_inventory(spec):
            raise ValueError('Expected all 128 question/corpus combinations')
        write(a.output_dir / 'results.json', rows, True)
        metrics = aggregate_metrics(rows)  # Original numeric aggregation and fixed denominators.
        metrics['notes']['A_denominator'] = 'All40 unchanged; current-availability-conditioned metrics are supplementary.'
        metrics['notes']['availability'] = 'evidence_available derives from current corpus before ranking; frozen benchmark untouched.'
        write(a.output_dir / 'metrics.json', metrics)
        comparisons = compare_results(rows, read_json(reference_path), spec)
        for c in comparisons:
            c['historical_corpus_sha256'] = c['corpus_sha256']
            c['corpus_sha256'] = manifest['corpora'][c['mode']]['sha256']
            c['cause'] = None if c['exact_match'] else 'Controlled experiment difference; inspect current availability, criteria and ranked evidence. No tolerance applied.'
        write(a.output_dir / 'comparison-to-B0.json', comparisons)
        audit = {(r['mode'], r['id']): r for r in report}
        diagnostics = [{'id': r['id'], 'historical_and_current_coverage': audit[(r['mode'], r['id'])],
            'current_retrieval_outcome': r['diagnostic_outcome'], 'strict': r['strict'],
            'included_in_primary_denominator': False} for r in rows if r['group'] == 'D']
        write(a.output_dir / 'p1-diagnostics.json', diagnostics)
        # No assertions about historical alternatives staying in current Top5.
        historical = read_json(reference_path)
        turnover = []
        historical_by = {(r['mode'], r['id']): r for r in historical}
        for row in rows:
            old = historical_by[row['mode'], row['id']]
            if row['strict']['hit_at_5'] and old['strict']['hit_at_5'] and row['top_10_ids'] == old['top_10_ids']:
                continue
            q = next(q for q in spec['questions'] if q['id'] == row['id'])
            turnover.append({'id': row['id'], 'mode': row['mode'],
                'availability': audit[(row['mode'], row['id'])],
                'historical_strict': old['strict'], 'current_strict': row['strict'],
                'historical_top10_ids': old['top_10_ids'], 'current_top10_ids': row['top_10_ids'],
                'legacy_target_id_current_ranks': {i: (row['top_10_ids'].index(i) + 1 if i in row['top_10_ids'] else None)
                    for req in q['availability'] for i in req['matching_chunk_ids']},
                'current_semantic_anchor': row['semantic_anchor'],
                'cause_limit': 'Coverage is directly measured; ranking causality requires separately recorded stage traces/controls.'})
        write(a.output_dir / 'failure-and-change-review.json', turnover)
        summary = {'status': 'EVALUATED', 'experiment_id': manifest['experiment_id'], 'combinations': len(rows),
            'B0_exact_row_matches': sum(c['exact_match'] for c in comparisons),
            'results_sha256': file_sha256(a.output_dir / 'results.json'),
            'strict_scoring_unchanged': True, 'score_tolerance_used': False,
            'primary_denominators': {k: v['n'] for k, v in metrics['group'].items()},
            'B0_differences_are_execution_failures': False}
        write(a.output_dir / 'summary.json', summary)
        write(a.output_dir / 'checksums.json', {p.name: file_sha256(p) for p in sorted(a.output_dir.iterdir()) if p.is_file()})
        print(json.dumps(summary, sort_keys=True), flush=True)
        return 0
    except (OSError, ValueError, KeyError, AssertionError) as e:
        print(f'Experiment failed: {e}', file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
