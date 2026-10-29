"""Manual schema-2 orchestration; candidate generation never approves a baseline."""
from pathlib import Path
import os
import shutil
import subprocess

from . import ci_v2_research as c, trusted_snapshot as trust
from .corpus_snapshot import checked_file, digest, file_digest, read_json, read_records, write_json

FILES = ('src/schema2_workflow.py', 'src/trusted_snapshot.py', 'src/regression_attribution.py',
         'scripts/ci_v2_schema2_research.py', 'configs/ci_v2_schema2_trusted_baselines.json',
         'docs/ci_v2_p5a_2_design.md')


def plan(project, registry, key, revisions):
    """Stdlib-only preflight before parser environment provisioning."""
    c.validate_request('formal', key, revisions)
    entry = trust.registry_reference(registry, key)
    ref = entry['reference']
    policy = read_json(checked_file(project, entry['build_policy_path']))
    c.need(file_digest(checked_file(project, entry['build_policy_path'])) == entry['build_policy_sha256'],
           'AUTHORIZATION_FAILED', 'Reviewed build policy checksum mismatch')
    c.need(policy['scope']['target_upstreams'] == revisions and policy['provenance_type'] == 'GENUINE_UPSTREAM',
           'AUTHORIZATION_FAILED', 'Target pair is outside explicit genuine build authorization')
    c.need(ref['package_format'] == 'p2-snapshot-v1', 'PARENT_RETRIEVAL_ANCHOR_NOT_QUALIFIED',
           'Schema-2 research-parent R2 evidence anchor requires separate qualification')
    return {'schema_version': 2, 'key': key, 'upstreams': revisions,
            'registry_entry_sha256': digest(entry), 'status': 'SCHEMA2_MANUAL_PLAN', **c.RESTRICTIONS}


def _verification(project, candidate, full, directory, quality, lock, output):
    from . import upstream_provenance as u
    from .approval_evidence import frozen_implementation, rescore, same, replay, BENCHMARK
    u.compare_full(project, candidate, full, digest(lock['P2']))
    spec = frozen_implementation(project, Path(project) / BENCHMARK, lock['quality_policy'])
    rows = {}
    output.mkdir(exist_ok=False)
    for label, root in [('incremental', candidate), ('full', full)]:
        raw = directory / (label + '-raw-results.json')
        rows[label] = read_json(raw)
        metrics = rescore(rows[label], spec, read_records(root / 'corpus.jsonl'),
                          read_records(root / 'components/documentation.jsonl'))
        c.need(same(metrics, quality['metrics']), 'RETRIEVAL_MISMATCH', 'Frozen aggregates mismatch')
        actual = replay(spec, root / 'corpus.jsonl', root / 'components/documentation.jsonl')
        c.need(same(rows[label], actual), 'RETRIEVAL_EXECUTION_FAILED', 'P4.1 independent R2 replay mismatch')
        write_json(output / (label + '-replay.json'), actual)
    c.need(same(rows['incremental'], rows['full']) and file_digest(directory / 'incremental-raw-results.json') == quality['raw_results_sha256'],
           'RETRIEVAL_MISMATCH', 'Candidate/full original rows mismatch')
    write_json(output / 'verification.json', {'status': 'VERIFIED', 'layers': ['A', 'B', 'C'],
        'original_combinations': 256, 'independent_replay_combinations': 256,
        'candidate_corpus_sha256': file_digest(candidate / 'corpus.jsonl'),
        'full_corpus_sha256': file_digest(full / 'corpus.jsonl'), **c.RESTRICTIONS})
    return spec, rows


def diagnostics(project, output, spec, parent_rows, target_rows, bindings, *, q1_inputs=None, q1_sha=None):
    """Diagnostic failures cannot override mandatory execution/scoring checks."""
    from . import regression_attribution as attribution, version_aware_evidence as q
    report = None
    status = {'status': 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR'}
    if q1_inputs is not None:
        try:
            possible = q.generate(project, q1_inputs, q1_sha)
            p = possible['base']; t = possible['target']
            required = {'base_source_state': p['source_state_id'], 'target_source_state': t['source_state_id'],
                'parent_id': p['package_id'], 'candidate_id': t['package_id'],
                'base_corpus_sha256': p['corpus_sha256'], 'target_corpus_sha256': t['corpus_sha256']}
            c.need(all(bindings.get(k) == v for k, v in required.items()), 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR', 'Q1 pair does not bind this execution')
            c.need(possible['archived_rows_digests'] == {'base': digest(parent_rows), 'target': digest(target_rows)},
                   'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR', 'Q1 archived retrieval rows differ from this run')
            report = possible
            # Preserve a relocatable Q1 reproduction descriptor using this run's
            # actual verified packages and original result bytes.
            original = read_json(q1_inputs)
            descriptor = {**original, 'paths': {'base': 'parent', 'target': 'candidate',
                'base_rows': 'parent-raw-results.json', 'target_rows': 'quality/incremental-raw-results.json',
                'benchmark': 'benchmark.json'}}
            write_json(output / 'q1-inputs.json', descriptor)
            regenerated = q.generate(project, output / 'q1-inputs.json', file_digest(output / 'q1-inputs.json'))
            c.need(digest(regenerated) == digest(report), 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR', 'Current-package Q1 regeneration differs')
            q.write_report(output / 'q1', report)
            status = {'status': 'QUALIFIED', 'report_id': report['report_id']}
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            report = None
            status = {'status': 'Q1_NOT_QUALIFIED_FOR_VERSION_PAIR', 'reason': str(error)}
    write_json(output / 'q1-status.json', status)
    result = attribution.summarize(spec, parent_rows, target_rows, report, bindings=bindings)
    write_json(output / 'regression-attribution.json', result)
    write_json(output / 'review-summary.json', {'report_id': result['report_id'], 'label_counts': result['label_counts'],
        'review_counts': result['review_counts'], 'failures': [{'id': r['id'], 'mode': r['mode'],
        'recommendations': r['review_recommendations'], 'human_decision': 'PENDING'} for r in result['failures']], **attribution.LIMITS})
    return status, result


def run_local(project, archive, base_source, target_source, output, ref, expected_ref_sha, policy_path,
              expected_policy_sha, parent_rows_path, *, q1_inputs=None, q1_sha=None):
    """Qualified genuine P2 bootstrap path. Research parents fail closed for now."""
    from . import upstream_provenance as u
    from .approval_evidence import frozen_implementation, rescore, replay, same, BENCHMARK
    from .research_quality import evaluate_quality
    project, archive, base_source, target_source, output, policy_path, parent_rows_path = map(Path,
        (project, archive, base_source, target_source, output, policy_path, parent_rows_path))
    lock = read_json(c.LOCK)
    runtime = c.frozen_runtime(project, lock)
    trust.validate_reference(ref, expected_ref_sha)
    c.need(ref['build_fingerprint'] == digest(lock['P2']), 'IMPLEMENTATION_INCOMPATIBLE', 'Trusted build mismatch')
    policy = u._policy(policy_path, expected_policy_sha)
    c.need(policy['provenance_type'] == 'GENUINE_UPSTREAM' and not policy.get('controlled_chain_fixture'),
           'AUTHORIZATION_FAILED', 'Workflow accepts genuine authorized upstream transitions only')
    c.need(ref['package_format'] == 'p2-snapshot-v1', 'PARENT_RETRIEVAL_ANCHOR_NOT_QUALIFIED',
           'Approved schema-2 parents need separately qualified parent R2 anchors')
    c.need(file_digest(parent_rows_path) == ref['parent_rows_sha256'], 'AUTHORIZATION_FAILED', 'Parent R2 anchor mismatch')
    protected = (project, archive, base_source, target_source, policy_path, parent_rows_path)
    if q1_inputs is not None: protected += (Path(q1_inputs).parent,)
    u._new_output(output, protected)
    output.mkdir()
    write_json(output / 'trusted-reference.json', ref)
    shutil.copyfile(policy_path, output / 'build-policy.json')
    shutil.copyfile(parent_rows_path, output / 'parent-raw-results.json')
    shutil.copyfile(project / BENCHMARK, output / 'benchmark.json')
    info = trust.restore(project, archive, output / 'parent', ref, expected_ref_sha)
    write_json(output / 'parent-verification.json', info)
    spec = frozen_implementation(project, project / BENCHMARK, lock['quality_policy'])
    parent = output / 'parent'; fp = ref['build_fingerprint']
    parent_rows = read_json(parent_rows_path)
    parent_metrics = rescore(parent_rows, spec, read_records(parent / 'corpus.jsonl'), read_records(parent / 'components/documentation.jsonl'))
    c.need(same(replay(spec, parent / 'corpus.jsonl', parent / 'components/documentation.jsonl'), parent_rows),
           'RETRIEVAL_EXECUTION_FAILED', 'Pinned parent R2 anchor replay differs')
    # u.build verifies exact parent source bytes, clean Git roots, commit/tree/
    # selected blobs, public receipts and same/fast-forward relationship proofs.
    built = u.build(project, parent, base_source, target_source, output / 'candidate', policy_path=policy_path,
                    expected_policy_sha256=expected_policy_sha, expected_fingerprint=fp)
    full = u.full_reference(project, target_source, output / 'full', policy['scope']['target_upstreams'], fp)
    parity = u.compare_full(project, output / 'candidate', output / 'full', fp)
    write_json(output / 'parity.json', parity)
    quality = evaluate_quality(project, output / 'candidate', output / 'full', output / 'quality',
        benchmark=project / BENCHMARK, policy=lock['quality_policy'], expected_fingerprint=fp,
        historical_rows=parent_rows, parent_rows=parent_rows, parent_corpus=parent / 'corpus.jsonl', parent_sources=base_source)
    spec, rows = _verification(project, output / 'candidate', output / 'full', output / 'quality', quality, lock, output / 'execution-verification')
    bindings = {'parent_id': info['node_id'], 'candidate_id': built['candidate_id'],
        'base_source_state': ref['source_state'], 'target_source_state': built['target_source_state'],
        'base_corpus_sha256': ref['corpus_sha256'], 'target_corpus_sha256': file_digest(output / 'candidate/corpus.jsonl'),
        'base_upstreams': ref['upstreams'], 'target_upstreams': built['upstreams'],
        'build_fingerprint': fp, 'benchmark_sha256': lock['quality_policy']['benchmark_sha256'],
        'base_rows_sha256': file_digest(parent_rows_path), 'target_rows_sha256': quality['raw_results_sha256'],
        'four_artifact_parity': parity, 'P4_1_original_evidence': 'VERIFIED_LAYERS_A_B_C'}
    qstatus, attributed = diagnostics(project, output, spec, parent_rows, rows['incremental'], bindings,
                                     q1_inputs=q1_inputs, q1_sha=q1_sha)
    # Validate mandatory inputs again after optional diagnostics; never let a
    # diagnostic implementation weaken or replace these gates.
    u.verify(project, output / 'candidate', fp)
    u.compare_full(project, output / 'candidate', output / 'full', fp)
    trust.verify_package(project, parent, ref)
    u._parent(project, parent, fp, policy, base_source)
    u.verify_sources(project, target_source, policy['scope']['target_upstreams'])
    c.need(file_digest(policy_path) == expected_policy_sha and file_digest(parent_rows_path) == ref['parent_rows_sha256'],
           'AUTHORIZATION_FAILED', 'Inputs changed during run')
    result = {'schema_version': 2, 'local_integration': 'LOCAL_VALIDATED', 'STRUCTURAL_PARITY': 'PASS',
        'RETRIEVAL_QUALITY_ACCEPTED': False, 'RESEARCH_BASELINE_APPROVED': False,
        'hosted_validation': 'PENDING_HOSTED_REVIEW' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'HOSTED_NOT_RUN',
        'remote_baseline_selection': ('PINNED_REFERENCE_VERIFIED' if ref['storage']['kind'] == 'GITHUB_RELEASE_ASSET'
                                      else 'CONFIGURATION_REQUIRED'), 'baseline_id': info['node_id'],
        'candidate_id': built['candidate_id'], 'target_source_state': built['target_source_state'],
        'independent_full_snapshot_id': full['snapshot_id'], 'runtime': runtime,
        'candidate_evaluation_combinations': 128, 'full_evaluation_combinations': 128,
        'independent_replay_combinations': 256, 'parent_metrics': parent_metrics, 'quality_metrics': quality['metrics'],
        'reuse': read_json(output / 'candidate/reuse-summary.json'), 'Q1': qstatus,
        'attribution_report_id': attributed['report_id'], 'bindings': bindings,
        'implementation': {n: file_digest(project / n) for n in FILES}, **c.RESTRICTIONS}
    write_json(output / 'result.json', result)
    u._seal(output)
    return result


def verify_run(project, root):
    """Portable offline reinspection; mandatory P4.1 replay remains executable."""
    from . import upstream_provenance as u
    from .approval_evidence import rescore, frozen_implementation, replay, same, BENCHMARK
    root = Path(root); u._payload(root)
    result = read_json(root / 'result.json'); ref = read_json(root / 'trusted-reference.json')
    c.need(result['automatic_research_approval'] is False and result['research_baseline_created'] is False
        and result['RESEARCH_BASELINE_APPROVED'] is False and result['RETRIEVAL_QUALITY_ACCEPTED'] is False
        and result['production_promotion'] == 'NOT_AUTHORIZED', 'INVALID_CANDIDATE', 'Archive cannot grant approval')
    trust.validate_reference(ref, digest(ref))  # Integrity only; external transport pin is the trust anchor.
    info = trust.verify_package(project, root / 'parent', ref)
    built = u.verify(project, root / 'candidate', ref['build_fingerprint'])
    parity = u.compare_full(project, root / 'candidate', root / 'full', ref['build_fingerprint'])
    c.need(info['node_id'] == result['baseline_id'] and built['candidate_id'] == result['candidate_id']
        and same(parity, read_json(root / 'parity.json')), 'INVALID_CANDIDATE', 'Archived execution binding mismatch')
    spec = frozen_implementation(project, Path(project) / BENCHMARK, read_json(c.LOCK)['quality_policy'])
    parent_rows = read_json(root / 'parent-raw-results.json')
    rescore(parent_rows, spec, read_records(root / 'parent/corpus.jsonl'), read_records(root / 'parent/components/documentation.jsonl'))
    c.need(file_digest(root / 'parent-raw-results.json') == ref['parent_rows_sha256'], 'INVALID_CANDIDATE', 'Restored parent rows mismatch')
    c.need(same(parent_rows, replay(spec, root / 'parent/corpus.jsonl', root / 'parent/components/documentation.jsonl')),
           'RETRIEVAL_EXECUTION_FAILED', 'Restored parent R2 anchor mismatch')
    for label, folder in [('incremental', root / 'candidate'), ('full', root / 'full')]:
        rows = read_json(root / 'quality' / (label + '-raw-results.json'))
        metrics = rescore(rows, spec, read_records(folder / 'corpus.jsonl'), read_records(folder / 'components/documentation.jsonl'))
        c.need(same(metrics, result['quality_metrics']) and same(rows, replay(spec, folder / 'corpus.jsonl', folder / 'components/documentation.jsonl')),
               'RETRIEVAL_EXECUTION_FAILED', 'Restored frozen result replay mismatch')
    from . import regression_attribution as attribution, version_aware_evidence as q
    qreport = None
    if read_json(root / 'q1-status.json')['status'] == 'QUALIFIED':
        qreport = q.generate(project, root / 'q1-inputs.json', file_digest(root / 'q1-inputs.json'))
        c.need(same(qreport, read_json(root / 'q1/version_aware_evidence_report.json')), 'INVALID_CANDIDATE', 'Restored Q1 differs')
    regenerated = attribution.summarize(spec, parent_rows, read_json(root / 'quality/incremental-raw-results.json'),
                                       qreport, bindings=result['bindings'])
    c.need(same(regenerated, read_json(root / 'regression-attribution.json')), 'INVALID_CANDIDATE', 'Restored attribution differs')
    return {'status': 'VERIFIED', 'candidate_id': built['candidate_id'], 'restored_combinations_replayed': 256, **c.RESTRICTIONS}


def execute_plan(project, plan_path, work, output):
    """Hosted-capable wiring, intentionally not executed during P5A.2."""
    project, work, output = Path(project), Path(work), Path(output)
    planned = read_json(plan_path); registry = read_json(trust.REGISTRY)
    current = plan(project, registry, planned['key'], planned['upstreams'])
    c.need(current == planned, 'INVALID_REFERENCE', 'Registry/authorization changed after planning')
    c.frozen_runtime(project)
    c.need(shutil.disk_usage(work.parent).free >= 8 * 1024**3, 'RESOURCE_LIMIT', 'At least 8 GiB free required')
    work.mkdir(exist_ok=False)
    entry = registry['baselines'][planned['key']]; ref = entry['reference']
    trust.download(ref['storage'], work / 'parent.tar.gz', ref['transport_sha256'])
    trust.download(ref['parent_rows_storage'], work / 'parent-rows.json', ref['parent_rows_sha256'])
    # New isolated repositories only. Full history is required for ancestry;
    # never fetch/checkout inside protected or operator-provided repositories.
    for label, revisions in [('base-source', ref['upstreams']), ('target-source', planned['upstreams'])]:
        for repo, url in c.SOURCES.items():
            dest = work / label / 'repos' / repo; dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', '-c', 'credential.helper=', 'clone', '--no-checkout', url, str(dest)], check=True)
            subprocess.run(['git', '-C', str(dest), 'checkout', '--detach', revisions[repo]], check=True)
    result = run_local(project, work / 'parent.tar.gz', work / 'base-source', work / 'target-source', output,
        ref, entry['expected_reference_sha256'], checked_file(project, entry['build_policy_path']),
        entry['build_policy_sha256'], work / 'parent-rows.json')
    c.package_candidate(output, output.parent / 'candidate.tar.gz')
    return result
