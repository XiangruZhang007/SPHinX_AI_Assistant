"""Small nonblocking diagnostic summary; frozen scores and approval stay separate.

Callers must authenticate/rescore raw rows and regenerate Q1 before calling this
pure formatter. These inputs are diagnostic evidence, never an approval token.
"""
from collections import Counter
from .corpus_snapshot import digest
from .retrieval_evaluation_criteria import matches

VERSION = 'regression-attribution-v1'
LIMITS = {'diagnostic_only': True, 'blocking': False, 'official_score_adjustment': False,
          'automatic_research_approval': False, 'research_baseline_created': False,
          'production_promotion': 'NOT_AUTHORIZED', 'human_decision': 'PENDING'}


def _rank(row, criterion):
    """None means not observed in the verified returned Top10, never rank 11."""
    if row is None or 'top_10' not in row:
        return {'status': 'MISSING', 'rank': None}
    found = [x['rank'] for x in row['top_10'] if matches(x['record'], criterion)]
    return {'status': 'OBSERVED' if found else 'NOT_RETURNED_IN_TOP10',
            'rank': min(found) if found else None}


def _within(rank, k):
    return rank['rank'] is not None and rank['rank'] <= k


def summarize(spec, base_rows, target_rows, q1_report, *, bindings):
    """ALL_OF per requirement/K; explanations may coexist and never rescore."""
    old = {(r['mode'], r['id']): r for r in base_rows}
    new = {(r['mode'], r['id']): r for r in target_rows}
    maps = {} if q1_report is None else {
        (r['mode'], r['question_id'], r['requirement_index']): r
        for r in q1_report['requirements']}
    questions = {q['id']: q for q in spec['questions']}
    findings = []
    for key, after in sorted(new.items()):
        before = old.get(key); question = questions[key[1]]
        if question['evidence_mode'] != 'ALL_OF':
            raise ValueError('Attribution supports only the frozen ALL_OF operator')
        # Passing rows remain covered in the inventory but need no failure entry.
        if after['strict']['hit_at_5'] and after['strict']['hit_at_10']:
            continue
        details = []
        for index, criterion in enumerate(question['requirements']):
            mapping = maps.get((*key, index))
            ranks = {'base': _rank(before, criterion), 'target': _rank(after, criterion)}
            labels = set(); proof = []; stages = {}
            def add(label, reason):
                labels.add(label); proof.append({'label': label, 'reason': reason})
            if mapping is None:
                add('ATTRIBUTION_UNCERTAIN', 'Qualified Q1 correspondence is unavailable')
            else:
                unique = mapping['mapping_uniqueness'] == 'UNIQUE'
                content = mapping['source_content_state']
                coverage = mapping['target_corpus_coverage']['state']
                if unique and content == 'UNCHANGED' and mapping['frozen_locator_state'] == 'STALE':
                    add('BENCHMARK_LOCATOR_INVALIDATED', 'Unique unchanged source passage; frozen locator is stale')
                if unique and content == 'MODIFIED':
                    add('SOURCE_CONTENT_CHANGED', 'Verified contextual replacement; semantics require review')
                if unique and content == 'REMOVED':
                    add('SOURCE_EVIDENCE_REMOVED', 'Verified Git absence or exact two-sided context removal')
                if unique and coverage == 'SPLIT':
                    add('EVIDENCE_FRAGMENTATION', 'Exact passage fully covered by multiple chunks')
                    stages['EVIDENCE_FRAGMENTATION'] = ('PREEXISTING' if mapping['base_corpus_coverage']['state'] == 'SPLIT'
                                                        else 'NEWLY_INTRODUCED')
                if unique and mapping.get('target_passage') and coverage == 'ABSENT':
                    add('CORPUS_COVERAGE_GAP', 'Mapped source passage exists; no eligible chunk intersects it')
                # Partial literal bytes can be punctuation/whitespace while the
                # frozen metadata predicate still matches. Do not invent a gap cause.
                if not unique or coverage in ('PARTIAL', 'NOT_ASSESSED') or content == 'UNKNOWN':
                    add('ATTRIBUTION_UNCERTAIN', 'Ambiguous/unresolved correspondence or partial literal byte coverage')
                if mapping['frozen_criterion_applicability'] == 'REVIEW_REQUIRED':
                    add('ATTRIBUTION_UNCERTAIN', 'Frozen criterion applicability requires review')
            at_k = {}
            for k in (5, 10):
                failed = not _within(ranks['target'], k)
                valid = (mapping is not None and mapping['mapping_uniqueness'] == 'UNIQUE'
                         and mapping['source_content_state'] == 'UNCHANGED'
                         and mapping['frozen_criterion_applicability'] == 'APPLICABLE'
                         and mapping['base_corpus_coverage']['state'] == 'SINGLE_CHUNK'
                         and bool(mapping['original_chunk_associations'])
                         and mapping['target_corpus_coverage']['state'] == 'SINGLE_CHUNK')
                rank_known = all(r['status'] != 'MISSING' for r in ranks.values())
                reasons = []
                if failed and valid and rank_known and _within(ranks['base'], k):
                    add('RANKING_REGRESSION', f'Applicable unchanged requirement evidence crossed Top{k}; question outcome recorded separately')
                    reasons.append('RANKING_REGRESSION')
                elif failed and valid and rank_known and not _within(ranks['base'], k):
                    add('PREEXISTING_RANKING_FAILURE', f'Applicable evidence absent from both returned Top{k} sets')
                    reasons.append('PREEXISTING_RANKING_FAILURE')
                if failed and not rank_known:
                    add('ATTRIBUTION_UNCERTAIN', 'Missing parent or target rank evidence')
                mapped_ranks = None if mapping is None else mapping.get('diagnostic_mapped_single_chunk_top10_ranks')
                locator_explained = (failed and 'BENCHMARK_LOCATOR_INVALIDATED' in labels
                    and mapping['target_corpus_coverage']['state'] == 'SINGLE_CHUNK'
                    and mapped_ranks is not None and any(r <= k for r in mapped_ranks))
                at_k[str(k)] = {'frozen_requirement_retrieved': not failed,
                    'locator_mismatch_explained_at_k': locator_explained, 'rank_causes': reasons}
                if failed and 'BENCHMARK_LOCATOR_INVALIDATED' in labels and not locator_explained:
                    add('ATTRIBUTION_UNCERTAIN', f'Stale locator, but mapped evidence retrieval at Top{k} is unproved')
            details.append({'requirement_index': index, 'frozen_requirement': criterion,
                'labels': sorted(labels), 'label_evidence': proof, 'change_stage': stages,
                'ranks': ranks, 'at_k': at_k, 'Q1_evidence': mapping})
        all_labels = sorted({label for d in details for label in d['labels']})
        conclusions = set(); review_at_k = {}
        for k in (5, 10):
            if after['strict']['hit_at_' + str(k)]:
                continue
            failed = [d for d in details if not d['at_k'][str(k)]['frozen_requirement_retrieved']]
            explained = bool(failed) and all(d['at_k'][str(k)]['locator_mismatch_explained_at_k'] for d in failed)
            review_at_k[str(k)] = 'EXPLAINED_BENCHMARK_APPLICABILITY_CHANGE' if explained else 'REVIEW_REMAINING_CAUSES'
            if explained:
                conclusions.add('EXPLAINED_BENCHMARK_APPLICABILITY_CHANGE')
        if set(all_labels) & {'RANKING_REGRESSION', 'PREEXISTING_RANKING_FAILURE', 'EVIDENCE_FRAGMENTATION', 'CORPUS_COVERAGE_GAP'}:
            conclusions.add('RETRIEVAL_IMPROVEMENT_REQUIRED')
        if set(all_labels) & {'SOURCE_CONTENT_CHANGED', 'SOURCE_EVIDENCE_REMOVED'}:
            conclusions.add('SOURCE_OR_BENCHMARK_REVIEW_REQUIRED')
        if 'ATTRIBUTION_UNCERTAIN' in all_labels:
            conclusions.add('MANUAL_REVIEW_REQUIRED')
        lost = before is not None and any(before['strict']['hit_at_' + str(k)] and not after['strict']['hit_at_' + str(k)] for k in (5, 10))
        if not lost and 'RANKING_REGRESSION' not in all_labels:
            conclusions.add('NO_NEW_REGRESSION_IDENTIFIED')
        if not conclusions:
            conclusions.add('MANUAL_REVIEW_REQUIRED')
        findings.append({'mode': key[0], 'id': key[1], 'observed_facts': {
                'base_strict': None if before is None else before['strict'], 'target_strict': after['strict'],
                'pass_to_fail_observed': lost}, 'supported_attribution': all_labels,
                'ranking_regression_demonstrated': 'RANKING_REGRESSION' in all_labels,
                'requirements': details, 'review_at_k': review_at_k,
                'review_recommendations': sorted(conclusions), 'final_human_decision': 'PENDING'})
    report = {'schema_version': 1, 'version': VERSION, 'bindings': bindings,
        'Q1_status': 'QUALIFIED' if q1_report is not None else 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR',
        'inventory': {'question_mode_combinations': len(new), 'failed_question_modes': len(findings)},
        'label_counts': dict(sorted(Counter(label for r in findings for label in r['supported_attribution']).items())),
        'review_counts': dict(sorted(Counter(label for r in findings for label in r['review_recommendations']).items())),
        'failures': findings, **LIMITS}
    return {'report_id': 'sha256-' + digest(report), **report}
