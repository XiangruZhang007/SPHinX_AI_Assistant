"""P4 orchestration of the unchanged R2 and frozen evidence evaluator.

Variable target corpus counts are intentional; historical run_condition remains
locked to its original 11370/354 experiment. No oracle/scorer/ranking is changed.
"""
from __future__ import annotations
import os
from pathlib import Path

from .corpus_snapshot import SnapshotError, digest, file_digest, read_json, read_records, write_json
from .full_retrieval_regression import (condition_factory, indexed_results,
                                       requirement_ranks, transition)
from .remediation_evaluation import current_availability, evaluate
from .retrieval_evaluation_io import validate_structure
from .retrieval_evaluation_criteria import matches
from .retrieval_evaluation_reports import aggregate_metrics
from .retrieval_identity_diagnostics import retrieval_fingerprint, record_scores, rank_trace

EVALUATOR_FILES = ('src/full_retrieval_regression.py', 'src/remediation_evaluation.py',
                   'src/retrieval_evaluation_criteria.py', 'src/retrieval_evaluation_reports.py',
                   'src/retrieval_evaluation_io.py', 'src/retrieval_identity_diagnostics.py')


def frozen_policy(benchmark_sha, r2_fingerprint):
    return {'schema_version': 1, 'benchmark_sha256': benchmark_sha,
            'R2_fingerprint': r2_fingerprint, 'historical_quality': 'QUALITY_REVIEW_REQUIRED',
            'evaluation_implementation': {n: file_digest(Path(__file__).resolve().parents[1] / n)
                                          for n in EVALUATOR_FILES},
            'acceptance': 'Historical REVIEW_REQUIRED persists even with equal or improved scores.',
            'new_losses': 'Report every Hit5/Hit10 transition separately; no gain offsets a loss.',
            'applicability': 'Keep frozen results unchanged. Source removal/range changes are separate diagnostics, not automatically ranking regressions.',
            'research_reuse': 'Explicit candidate+parent+quality-bound authorization may allow isolated REVIEW_REQUIRED chains; never integrity, compatibility or parity bypass.',
            'unassessed': 'Non-designated fixtures remain recorded REVIEW_REQUIRED, never accepted quality.',
            'automatic_reuse': False, 'production_promotion': 'NOT_AUTHORIZED'}


def review_not_evaluated(candidate, fingerprint, policy):
    candidate = Path(candidate)
    return {'schema_version': 1, 'status': 'QUALITY_REVIEW_REQUIRED',
            'assessment': 'RECORDED_REVIEW_NOT_EVALUATED',
            'candidate_id': read_json(candidate / 'candidate.json')['candidate_id'],
            'corpus_sha256': file_digest(candidate / 'corpus.jsonl'),
            'build_fingerprint': fingerprint, 'policy': policy, 'policy_sha256': digest(policy),
            'reason': 'Predeclared structural/recovery fixture; no demonstrated retrieval quality acceptance.'}


def compare_outcomes(before, after, spec):
    old, new = indexed_results(before, spec), indexed_results(after, spec)
    return [{'mode': mode, 'id': qid,
             'hit5': transition(old[mode, qid]['strict']['hit_at_5'], new[mode, qid]['strict']['hit_at_5']),
             'hit10': transition(old[mode, qid]['strict']['hit_at_10'], new[mode, qid]['strict']['hit_at_10']),
             'previous_available': old[mode, qid]['evidence_available'],
             'target_available': new[mode, qid]['evidence_available']}
            for mode, qid in sorted(new)]


def oracle_applicability(spec, records, parent_records, target_sources, parent_sources):
    """Conservative source checks; ranges/passage criteria are never rewritten."""
    findings = []
    target_sources, parent_sources = Path(target_sources), Path(parent_sources)
    for question in spec['questions']:
        for index, criterion in enumerate(question['requirements']):
            old = [r for r in parent_records if matches(r, criterion)]
            new = [r for r in records if matches(r, criterion)]
            target, parent = target_sources / criterion['path'], parent_sources / criterion['path']
            raw = target.read_bytes() if target.is_file() else None
            prior = parent.read_bytes() if parent.is_file() else None
            changed = raw != prior
            if new:
                status = 'STRICT_EVIDENCE_AVAILABLE'
            elif not old:
                status = 'PREEXISTING_UNAVAILABLE_OR_SINGLE_CHUNK_LIMITATION'
            elif raw is None:
                status = 'SOURCE_EVIDENCE_REMOVED'
            elif changed:
                relaxed = {k: v for k, v in criterion.items() if k != 'range'}
                if 'range' in criterion and any(matches(r, relaxed) for r in records):
                    status = 'RANGE_CHANGED_OR_ORACLE_STALE_REVIEW'
                elif criterion.get('value') and criterion['value'].casefold() not in raw.decode('utf-8', errors='replace').casefold():
                    status = 'SOURCE_PASSAGE_REMOVED_OR_CHANGED'
                else:
                    status = 'CHANGED_SOURCE_OR_FRAGMENTATION_UNRESOLVED'
            else:
                status = 'UNCHANGED_SOURCE_EXTRACTION_OR_ORACLE_UNRESOLVED'
            findings.append({'id': question['id'], 'requirement_index': index, 'criterion': criterion,
                             'previous_available': bool(old), 'target_available': bool(new),
                             'source_bytes_changed': changed, 'classification': status,
                             'frozen_criterion_modified': False})
    return findings


def evaluate_quality(project, candidate, full, output, *, benchmark, policy,
                     expected_fingerprint, historical_rows, parent_rows,
                     parent_corpus, parent_sources):
    """Evaluate both corpora independently, exact frozen rows, 128 each."""
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SnapshotError('Launch Python with PYTHONHASHSEED=0')
    project, candidate, full, output, benchmark = map(Path, (project, candidate, full, output, benchmark))
    if output.exists() or output.is_symlink():
        raise SnapshotError('Quality output already exists')
    spec = read_json(benchmark)
    validate_structure(spec)
    if file_digest(benchmark) != policy['benchmark_sha256']:
        raise SnapshotError('Frozen benchmark checksum mismatch')
    expected_r2 = policy['R2_fingerprint']
    actual = retrieval_fingerprint(project, expected_r2['inputs']['exclusion_contract'],
                                    spec['config']['k1'], spec['config']['b'])
    if actual != expected_r2:
        raise SnapshotError('Frozen R2 fingerprint mismatch')
    for name, expected in policy['evaluation_implementation'].items():
        if file_digest(project / name) != expected:
            raise SnapshotError('Frozen evaluator implementation mismatch: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    rows = {}
    corpus = {}
    for label, root in [('incremental', candidate), ('full', full)]:
        combined = root / 'corpus.jsonl'
        docs = root / 'components/documentation.jsonl'
        records = read_records(combined)
        documentation = read_records(docs)
        if [r for r in records if r['source_role'] == 'documentation'] != documentation:
            raise SnapshotError('Documentation component mismatch')
        corpus[label] = records
        availability = [*current_availability(spec, documentation, 'documentation_only'),
                        *current_availability(spec, records, 'combined')]
        rows[label] = evaluate(spec, combined, docs, condition_factory('D'),
                               {'combined': len(records), 'documentation_only': len(documentation)},
                               {(r['mode'], r['id']): r for r in availability})
        indexed_results(rows[label], spec)
        write_json(output / (label + '-raw-results.json'), rows[label])
        # Historical aggregate annotation fields are kept as original outputs;
        # actual P4 oracle applicability is recorded separately, never inferred
        # from the historical hardcoded stale_oracle_count annotation.
        write_json(output / (label + '-frozen-metrics.json'), aggregate_metrics(rows[label]))
        write_json(output / (label + '-availability.json'), availability)
    if rows['incremental'] != rows['full']:
        raise SnapshotError('Identical full/incremental corpus produced unequal retrieval rows')
    transitions = {'historical': compare_outcomes(historical_rows, rows['incremental'], spec),
                   'immediate_parent': compare_outcomes(parent_rows, rows['incremental'], spec)}
    applicability = oracle_applicability(spec, corpus['incremental'], read_records(parent_corpus),
                                        candidate / 'input', parent_sources)
    write_json(output / 'transitions.json', transitions)
    write_json(output / 'oracle-applicability.json', applicability)
    changed_ids = {t['id'] for group in transitions.values() for t in group
                   if t['hit5'] in ('PASS→FAIL', 'FAIL→PASS') or t['hit10'] in ('PASS→FAIL', 'FAIL→PASS') or
                   t['previous_available'] != t['target_available']} | {'B-02'}
    retrievers = {mode: condition_factory('D')(candidate / path, k1=spec['config']['k1'], b=spec['config']['b'])
                  for mode, path in [('combined', 'corpus.jsonl'), ('documentation_only', 'components/documentation.jsonl')]}
    ranks = []
    for q in spec['questions']:
        if q['id'] not in changed_ids:
            continue
        for mode in ('combined', 'documentation_only') if q['group'] == 'A' else ('combined',):
            retriever = retrievers[mode]
            scores = record_scores(retriever, q['question'])
            trace = rank_trace(retriever, q['question'], scores)
            ranks.append({'mode': mode, 'id': q['id'], 'requirements': requirement_ranks(
                retriever.records, q['requirements'], scores, trace),
                'actual_top15_rechecked': True})
    write_json(output / 'verified-target-ranks.json', ranks)
    report = {**review_not_evaluated(candidate, expected_fingerprint, policy),
              'assessment': 'FROZEN_128_EVALUATED', 'combinations_each': 128,
              'independent_candidate_full_rows_equal': True,
              'raw_results_sha256': file_digest(output / 'incremental-raw-results.json'),
              'metrics': aggregate_metrics(rows['incremental']),
              'new_losses': {source: [t for t in ts if t['hit5'] == 'PASS→FAIL' or t['hit10'] == 'PASS→FAIL']
                             for source, ts in transitions.items()},
              'new_gains': {source: [t for t in ts if t['hit5'] == 'FAIL→PASS' or t['hit10'] == 'FAIL→PASS']
                             for source, ts in transitions.items()},
              'applicability_changes': [a for a in applicability if a['previous_available'] != a['target_available']],
              'historical_limitations_preserved': True,
              'aggregate_annotation_limit': 'Frozen metric helper includes historical fixed stale-oracle notes; P4 applicability file is authoritative for this experiment.'}
    write_json(output / 'quality-report.json', report)
    for name, expected in policy['evaluation_implementation'].items():
        if file_digest(project / name) != expected:
            raise SnapshotError('Evaluator changed during quality evaluation: ' + name)
    write_json(output / 'checksums.json', {p.name: file_digest(p) for p in sorted(output.iterdir()) if p.is_file()})
    return report
