"""P4.1 local research approval evidence. No new ranking or scoring rules.

The operator supplies a policy SHA independently of the report. It is a local
trust anchor, not a signature. Every evaluated approval replays unchanged R2.
Later package inspection checks a sealed replay receipt; fresh replay is explicit.
"""
from __future__ import annotations
import math
import os
from pathlib import Path
import shutil

from .corpus_snapshot import (SnapshotError, checked_file, digest, file_digest,
                             read_json, read_records, write_json)
from .full_retrieval_regression import condition_factory, indexed_results
from .remediation_evaluation import current_availability, evaluate
from .retrieval_evaluation_criteria import outcome
from .retrieval_evaluation_io import validate_structure
from .retrieval_evaluation_reports import aggregate_metrics
from .retrieval_identity_diagnostics import retrieval_fingerprint
from .research_quality import EVALUATOR_FILES, compare_outcomes, oracle_applicability

BENCHMARK = 'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json'
BENCHMARK_SHA = 'bac0b31b74410eaff00246e628187ee07c4aaefb8747ad076f090393b71d1ab7'
KIND = 'p4.1-approval-evidence'
RAW_FILES = ('incremental-raw-results.json', 'full-raw-results.json',
             'historical-raw-results.json', 'parent-raw-results.json')


def require(condition, message):
    if not condition:
        raise SnapshotError(message)


def same(left, right):
    """JSON identity includes types; Python equality conflates True, 1 and 1.0."""
    return digest(left) == digest(right)


def external_policy(path, expected_sha, *, candidate, parent, quality, authorization):
    require(path is not None and expected_sha, 'Missing external frozen approval policy/expected SHA256')
    path = Path(path)
    require(not path.resolve().is_relative_to(Path(candidate).resolve()),
            'Approval policy must be independent of candidate/report package')
    require(not path.is_symlink() and file_digest(path) == expected_sha, 'External approval policy checksum mismatch')
    policy = read_json(path)
    identity = read_json(Path(candidate) / 'candidate.json')['identity']
    required = {'experiment_id': authorization['experiment_id'],
                'parent_baseline_id': parent['baseline_id'],
                'target_source_state': identity['target_fixture_id'],
                'build_fingerprint': quality['build_fingerprint'],
                'quality_policy_sha256': digest(quality['policy'])}
    require(policy.get('schema_version') == 1 and policy.get('scope') == required,
            'Frozen external policy scope mismatch')
    require(policy.get('mode') in ('RETRIEVAL_EVALUATED', 'STRUCTURAL_ONLY'), 'Unknown approval mode')
    require(policy.get('quality_status') == 'QUALITY_REVIEW_REQUIRED' and
            policy.get('automatic_reuse') is False and policy.get('production_promotion') == 'NOT_AUTHORIZED',
            'Frozen policy must preserve research quality/production restrictions')
    if parent.get('generation', 0) and parent.get('P4_1_verification') != 'P4_1_VERIFIED':
        require(policy.get('legacy_parent_receipt') == {k: parent[k] for k in ('baseline_id', 'package_checksum')},
                'Legacy parent requires an exact externally frozen research exception')
    expected = 'FROZEN_128_EVALUATED' if policy['mode'] == 'RETRIEVAL_EVALUATED' else 'RECORDED_REVIEW_NOT_EVALUATED'
    require(quality.get('assessment') == expected, 'Assessment differs from externally designated mode; no fallback')
    require(quality['status'] == policy['quality_status'], 'Quality decision differs from frozen policy')
    return policy


def frozen_implementation(project, benchmark, policy):
    spec = read_json(benchmark)
    validate_structure(spec)  # unchanged ALL_OF-only contract
    require(file_digest(benchmark) == BENCHMARK_SHA == policy['benchmark_sha256'], 'Frozen benchmark mismatch')
    require(set(policy['evaluation_implementation']) == set(EVALUATOR_FILES), 'Incomplete evaluator identity')
    for name, expected in policy['evaluation_implementation'].items():
        require(file_digest(Path(project) / name) == expected, 'Frozen evaluator mismatch: ' + name)
    require(os.environ.get('PYTHONHASHSEED') == '0', 'R2 replay requires PYTHONHASHSEED=0')
    expected = policy['R2_fingerprint']
    actual = retrieval_fingerprint(project, expected['inputs']['exclusion_contract'],
                                   spec['config']['k1'], spec['config']['b'])
    require(actual == expected, 'Frozen R2 implementation/runtime/config mismatch')
    return spec


def bindings(candidate, full, quality, policy_sha):
    c = read_json(Path(candidate) / 'candidate.json')
    return {'candidate_id': c['candidate_id'], 'parent_baseline_id': c['identity']['base_snapshot_id'],
            'target_source_state': c['identity']['target_fixture_id'],
            'candidate_corpus_sha256': file_digest(Path(candidate) / 'corpus.jsonl'),
            'full_corpus_sha256': file_digest(Path(full) / 'corpus.jsonl'),
            'full_snapshot_id': read_json(Path(full) / 'manifest.json')['snapshot_id'],
            'benchmark_sha256': quality['policy']['benchmark_sha256'],
            'R2_fingerprint': quality['policy']['R2_fingerprint'],
            'evaluation_implementation': quality['policy']['evaluation_implementation'],
            'approval_policy_sha256': policy_sha}


def input_manifest(candidate, full, quality, policy_sha, directory):
    """Descriptor for caller's original raw files; not execution attestation."""
    root = Path(directory)
    return {'schema_version': 1, 'bindings': bindings(candidate, full, quality, policy_sha),
            'files': {name: file_digest(root / name) for name in RAW_FILES}}


def rescore(rows, spec, records, documentation):
    """Layer A and B; original matcher/outcome/aggregate functions only."""
    indexed_results(rows, spec)
    maps = {mode: {r['chunk_id']: r for r in corpus}
            for mode, corpus in [('combined', records), ('documentation_only', documentation)]}
    availability = {(a['mode'], a['id']): a for mode, corpus in
                    [('combined', records), ('documentation_only', documentation)]
                    for a in current_availability(spec, corpus, mode)}
    questions = {q['id']: q for q in spec['questions']}
    for row in rows:
        q = questions[row['id']]
        for name, expected in [('question', q['question']), ('group', q['group']),
                               ('category', q['category']), ('primary_score_eligible', q['primary_score_eligible']),
                               ('multi_evidence', len(q['requirements']) > 1)]:
            require(row[name] == expected, 'Result question/scoring metadata mismatch: ' + name)
        require(row['evidence_available'] == availability[row['mode'], row['id']]['current_evidence_available'],
                'Incorrect evidence availability')
        found = row['top_10']
        require(isinstance(found, list) and len(found) <= 10, 'Invalid Top10')
        ids = [x['chunk_id'] for x in found]
        require(len(set(ids)) == len(ids), 'Duplicate Top10 record')
        require(row['top_10_ids'] == ids and row['top_5_ids'] == ids[:5], 'Top5/Top10 identity/prefix mismatch')
        for rank, item in enumerate(found, 1):
            require(item['rank'] == rank, 'Invalid retrieval rank')
            canonical = maps[row['mode']].get(item['chunk_id'])
            require(canonical is not None and same(item['record'], canonical), 'Top10 canonical corpus record mismatch')
            metadata = item['metadata']
            for name in ('repository', 'source_role', 'source_path', 'chunk_type', 'start_line', 'end_line'):
                require(same(metadata.get(name), canonical.get(name)), 'Retrieved metadata mismatch: ' + name)
            require(metadata.get('symbol_or_section_title') == (canonical.get('symbol') or canonical.get('section_title')),
                    'Retrieved symbol/section metadata mismatch')
            score = item['score']
            require(type(score) is float and math.isfinite(score) and score > 0 and score.hex() == item['score_hex'],
                    'Invalid exact score representation')
        retrieved = [x['record'] for x in found]
        require(same(row['strict'], outcome(retrieved, q['requirements'])), 'Frozen strict outcome mismatch')
        criteria = spec['semantic_supplement']['questions'].get(q['id'], q['requirements'])
        require(same(row['semantic_anchor'], outcome(retrieved, criteria)), 'Frozen semantic outcome mismatch')
    metrics = aggregate_metrics(rows)
    require(metrics['combined_primary_all']['n'] == 84 and
            metrics['group']['documentation_only:A']['n'] == 40 and
            len([r for r in rows if r['group'] == 'D']) == 4, 'Incorrect frozen denominators')
    return metrics


def replay(spec, corpus, documentation):
    records, docs = read_records(corpus), read_records(documentation)
    availability = {(r['mode'], r['id']): r for mode, items in
                    [('combined', records), ('documentation_only', docs)]
                    for r in current_availability(spec, items, mode)}
    return evaluate(spec, corpus, documentation, condition_factory('D'),
                    {'combined': len(records), 'documentation_only': len(docs)}, availability)


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _source(root, relative):
    root = Path(root)
    if (root / 'input').is_dir():
        return root / 'input' / relative
    return root / 'sources' / relative.removeprefix('repos/')


def seal(root):
    write_json(Path(root) / 'checksums.json', {p.relative_to(root).as_posix(): file_digest(p)
               for p in sorted(Path(root).rglob('*')) if p.is_file() and p.name != 'checksums.json'})


def create_evidence(project, candidate, parent, full, output, quality, authorization,
                    policy_path, expected_policy_sha, *, parent_report, evidence=None, legacy_subject=None):
    """Only called after actual candidate/parent/full verification and parity."""
    candidate, parent, full, output = map(Path, (candidate, parent, full, output))
    policy = external_policy(policy_path, expected_policy_sha, candidate=candidate,
                             parent=parent_report, quality=quality, authorization=authorization)
    output.mkdir(exist_ok=False)
    for name, value in [('quality.json', quality), ('authorization.json', authorization),
                        ('external-policy.json', policy), ('subject.json', legacy_subject)]:
        write_json(output / name, value)
    # Preserve original file bytes: the expected policy hash is a file SHA, not canonical JSON.
    _copy(Path(policy_path), output / 'external-policy.json')
    base = {'schema_version': 1, 'kind': KIND, 'status': 'P4_1_VERIFIED',
            'mode': policy['mode'], 'policy_sha256': expected_policy_sha,
            'candidate_id': read_json(candidate / 'candidate.json')['candidate_id'],
            'parent_baseline_id': parent_report['baseline_id'],
            'source_state': read_json(candidate / 'candidate.json')['identity']['target_fixture_id'],
            'candidate_corpus_sha256': file_digest(candidate / 'corpus.jsonl'),
            'full_corpus_sha256': file_digest(full / 'corpus.jsonl'),
            'quality_status': 'QUALITY_REVIEW_REQUIRED', 'automatic_reuse': False,
            'production_promotion': 'NOT_AUTHORIZED', 'execution': 'NOT_EVALUATED'}
    if policy['mode'] == 'RETRIEVAL_EVALUATED':
        require(evidence is not None, 'Missing original raw retrieval evidence')
        evidence = Path(evidence)
        manifest = read_json(checked_file(evidence, 'inputs.json'))
        require(manifest == input_manifest(candidate, full, quality, expected_policy_sha, evidence),
                'Raw evidence identity/checksum manifest mismatch')
        require(policy.get('reference_rows_sha256') == {n: manifest['files'][n] for n in RAW_FILES[2:]},
                'Historical/parent result trust anchor mismatch')
        _copy(evidence / 'inputs.json', output / 'inputs.json')
        _copy(Path(project) / BENCHMARK, output / 'benchmark.json')
        spec = frozen_implementation(project, output / 'benchmark.json', quality['policy'])
        rows = {}
        metrics = {}
        for label, root, raw in [('candidate', candidate, RAW_FILES[0]), ('full', full, RAW_FILES[1])]:
            for rel in ('corpus.jsonl', 'components/documentation.jsonl'):
                _copy(root / rel, output / label / rel)
            _copy(evidence / raw, output / raw)
            rows[label] = read_json(output / raw)
            corpus = read_records(output / label / 'corpus.jsonl')
            docs = read_records(output / label / 'components/documentation.jsonl')
            require([r for r in corpus if r['source_role'] == 'documentation'] == docs, 'Documentation subset mismatch')
            metrics[label] = rescore(rows[label], spec, corpus, docs)
        require(same(rows['candidate'], rows['full']), 'Independent full/candidate raw rows differ')
        require(quality['raw_results_sha256'] == file_digest(output / RAW_FILES[0]) and
                same(quality['metrics'], metrics['candidate']), 'Claimed raw checksum/aggregate metrics mismatch')
        transitions = {}
        for label, raw in [('historical', RAW_FILES[2]), ('immediate_parent', RAW_FILES[3])]:
            _copy(evidence / raw, output / raw)
            previous = read_json(output / raw)
            indexed_results(previous, spec)
            transitions[label] = compare_outcomes(previous, rows['candidate'], spec)
        for name, direction in [('new_losses', 'PASS→FAIL'), ('new_gains', 'FAIL→PASS')]:
            actual = {k: [t for t in ts if t['hit5'] == direction or t['hit10'] == direction]
                      for k, ts in transitions.items()}
            require(quality[name] == actual, 'Claimed quality transition mismatch: ' + name)
        _copy(parent / 'corpus.jsonl', output / 'parent/corpus.jsonl')
        for q in spec['questions']:
            for criterion in q['requirements']:
                rel = criterion['path']
                for label, root in [('candidate', candidate), ('parent', parent)]:
                    source = _source(root, rel)
                    if source.is_file():
                        _copy(source, output / label / 'input' / rel)
        applicability = oracle_applicability(spec, read_records(candidate / 'corpus.jsonl'),
                    read_records(parent / 'corpus.jsonl'), output / 'candidate/input', output / 'parent/input')
        require(quality['applicability_changes'] == [a for a in applicability if a['previous_available'] != a['target_available']],
                'Claimed applicability diagnostics mismatch')
        write_json(output / 'recomputed.json', {'metrics': metrics, 'transitions': transitions,
                   'applicability': applicability, 'strict': {k: [{'mode': r['mode'], 'id': r['id'], 'strict': r['strict']}
                                                               for r in v] for k, v in rows.items()}})
        # No caller-supplied COMPLETE flag or checksum can replace these executions.
        for label in ('candidate', 'full'):
            actual = replay(spec, output / label / 'corpus.jsonl', output / label / 'components/documentation.jsonl')
            require(same(actual, rows[label]), 'Independent R2 replay mismatch: ' + label)
            write_json(output / (label + '-replay.json'), actual)
        frozen_implementation(project, output / 'benchmark.json', quality['policy'])
        base.update(execution='INDEPENDENT_R2_REPLAY_VERIFIED', layers=['A', 'B', 'C'],
                    bindings=manifest['bindings'], combinations_each=128,
                    primary_denominator=84, diagnostic_count=4)
    else:
        require(evidence is None, 'Structural-only policy cannot accept attempted evaluation evidence')
    write_json(output / 'verification.json', base)
    seal(output)
    return base


def verify_bundle(project, root, *, expected_manifest_sha=None, fresh_replay=False):
    """Portable offline inspection; expected hash must come from outside bundle.

    Without fresh_replay this validates a recorded local execution receipt, not
    cryptographic authenticity or a new R2 execution. Approval always replays.
    """
    root = Path(root)
    if expected_manifest_sha is not None:
        require(file_digest(root / 'checksums.json') == expected_manifest_sha, 'External bundle checksum mismatch')
    files = read_json(root / 'checksums.json')
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'checksums.json'}
    require(actual_files == set(files), 'Evidence package member mismatch')
    for name, expected in files.items():
        require(file_digest(checked_file(root, name)) == expected, 'Evidence checksum mismatch: ' + name)
    v, q = read_json(root / 'verification.json'), read_json(root / 'quality.json')
    require(v['kind'] == KIND and v['schema_version'] == 1 and v['status'] == 'P4_1_VERIFIED', 'Invalid evidence version/status')
    require(file_digest(root / 'external-policy.json') == v['policy_sha256'], 'Evidence policy hash mismatch')
    policy = read_json(root / 'external-policy.json')
    require(policy['mode'] == v['mode'] and policy['scope']['parent_baseline_id'] == v['parent_baseline_id'] and
            policy['scope']['target_source_state'] == v['source_state'] and
            policy['scope']['quality_policy_sha256'] == digest(q['policy']) and
            q['candidate_id'] == v['candidate_id'] and q['corpus_sha256'] == v['candidate_corpus_sha256'], 'Evidence binding mismatch')
    require(v['automatic_reuse'] is False and v['production_promotion'] == 'NOT_AUTHORIZED' and
            v['quality_status'] == policy['quality_status'] == q['status'] == 'QUALITY_REVIEW_REQUIRED',
            'Evidence must preserve research/production restrictions')
    if v['mode'] == 'RETRIEVAL_EVALUATED':
        spec = frozen_implementation(project, root / 'benchmark.json', q['policy'])
        manifest = read_json(root / 'inputs.json')
        require(manifest['bindings'] == v['bindings'], 'Stored evidence bindings differ')
        for name, value in [('candidate_id', v['candidate_id']), ('parent_baseline_id', v['parent_baseline_id']),
                            ('target_source_state', v['source_state']), ('candidate_corpus_sha256', v['candidate_corpus_sha256']),
                            ('full_corpus_sha256', v['full_corpus_sha256']), ('benchmark_sha256', BENCHMARK_SHA),
                            ('approval_policy_sha256', v['policy_sha256']), ('R2_fingerprint', q['policy']['R2_fingerprint']),
                            ('evaluation_implementation', q['policy']['evaluation_implementation'])]:
            require(manifest['bindings'][name] == value, 'Stored binding mismatch: ' + name)
        for name, h in manifest['files'].items():
            require(file_digest(checked_file(root, name)) == h, 'Original raw checksum mismatch')
        require(policy['reference_rows_sha256'] == {n: manifest['files'][n] for n in RAW_FILES[2:]}, 'Reference anchor mismatch')
        recomputed = read_json(root / 'recomputed.json')
        results = {}
        for label, raw in [('candidate', RAW_FILES[0]), ('full', RAW_FILES[1])]:
            rows = read_json(root / raw)
            results[label] = rows
            require(file_digest(root / label / 'corpus.jsonl') == v[label + '_corpus_sha256'], 'Corpus identity mismatch')
            metrics = rescore(rows, spec, read_records(root / label / 'corpus.jsonl'),
                              read_records(root / label / 'components/documentation.jsonl'))
            require(same(metrics, recomputed['metrics'][label]) and same(metrics, q['metrics']), 'Stored metrics mismatch')
            require(recomputed['strict'][label] == [{'mode': r['mode'], 'id': r['id'], 'strict': r['strict']} for r in rows],
                    'Stored strict rescoring mismatch')
            actual = replay(spec, root / label / 'corpus.jsonl', root / label / 'components/documentation.jsonl') if fresh_replay else read_json(root / (label + '-replay.json'))
            require(same(rows, actual), 'Stored/fresh R2 replay mismatch')
        require(results['candidate'] == results['full'], 'Stored candidate/full outcomes differ')
        transitions = {label: compare_outcomes(read_json(root / raw), results['candidate'], spec)
                       for label, raw in [('historical', RAW_FILES[2]), ('immediate_parent', RAW_FILES[3])]}
        require(transitions == recomputed['transitions'], 'Stored transitions mismatch')
        for name, direction in [('new_losses', 'PASS→FAIL'), ('new_gains', 'FAIL→PASS')]:
            require(q[name] == {k: [t for t in ts if t['hit5'] == direction or t['hit10'] == direction]
                               for k, ts in transitions.items()}, 'Stored quality transitions mismatch')
        applicability = oracle_applicability(spec, read_records(root / 'candidate/corpus.jsonl'),
                    read_records(root / 'parent/corpus.jsonl'), root / 'candidate/input', root / 'parent/input')
        require(applicability == recomputed['applicability'] and q['applicability_changes'] ==
                [a for a in applicability if a['previous_available'] != a['target_available']], 'Stored applicability mismatch')
        require(v['execution'] == 'INDEPENDENT_R2_REPLAY_VERIFIED', 'Missing execution receipt')
    else:
        require(v['mode'] == 'STRUCTURAL_ONLY' and v['execution'] == 'NOT_EVALUATED' and
                q['assessment'] == 'RECORDED_REVIEW_NOT_EVALUATED', 'Invalid structural authorization')
    return {**v, 'inspection': 'FRESH_REPLAY' if fresh_replay and v['mode'] == 'RETRIEVAL_EVALUATED' else 'SEALED_LOCAL_RECEIPT',
            'manifest_sha256': file_digest(root / 'checksums.json')}
