"""P5A manual candidate orchestration; frozen P2--P4.1 code is reused.

Real upstream evolution is deliberately blocked. Pinned-byte experiments can
validate the entire construction/retrieval path, but never approve a baseline.
Trust comes from reviewed external references/hashes, not downloaded metadata.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'configs/ci_v2_runtime_lock.json'
REGISTRY = ROOT / 'configs/ci_v2_trusted_baselines.json'
SOURCES = {'SPHinXsys': 'https://github.com/Xiangyu-Hu/SPHinXsys.git',
           'SPHinXsim': 'https://github.com/Xiangyu-Hu/SPHinXsim.git'}
RESTRICTIONS = {'retrieval_quality': 'QUALITY_REVIEW_REQUIRED', 'automatic_reuse': False,
                'automatic_research_approval': False, 'production_promotion': 'NOT_AUTHORIZED',
                'scheduled_execution': False, 'research_baseline_created': False}


class GateError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def need(condition, status, message):
    if not condition:
        raise GateError(status, message)


def read(path):
    from .corpus_snapshot import read_json
    return read_json(Path(path))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write(path, value):
    from .corpus_snapshot import write_json
    write_json(Path(path), value)


def validate_request(profile, key, revisions):
    need(profile in ('formal', 'pinned-research', 'diagnostic'), 'UNSAFE_INPUT', 'Unknown profile')
    need(isinstance(key, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', key), 'UNSAFE_INPUT', 'Invalid trusted baseline key')
    need(set(revisions) == set(SOURCES) and all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{40}', v)
         for v in revisions.values()), 'UNSAFE_INPUT', 'Exact two-repository lowercase commit SHAs required')


def frozen_runtime(project=ROOT, lock=None):
    from .incremental_corpus import current_contract, orchestration_contract
    from .research_baseline import lifecycle_contract
    from .retrieval_identity_diagnostics import retrieval_fingerprint
    lock = lock or read(LOCK)
    expected = lock['quality_policy']['R2_fingerprint']
    need(platform.machine() == expected['inputs']['runtime']['machine'] and
         platform.python_version() == expected['inputs']['runtime']['python'] and
         os.environ.get('PYTHONHASHSEED') == '0', 'RUNNER_INCOMPATIBLE', 'Frozen Python/architecture/hash seed mismatch')
    need(sys.flags.hash_randomization == 0 and sys.flags.ignore_environment == 0,
         'RUNNER_INCOMPATIBLE', 'Actual hash seed is randomized or PYTHONHASHSEED was ignored (-I/-E/-R)')
    for label, actual in [('P2', current_contract(project)), ('P3', orchestration_contract(project)),
                          ('P4_1', lifecycle_contract(project))]:
        need(canonical_sha(actual) == canonical_sha(lock[label]), 'IMPLEMENTATION_INCOMPATIBLE', label + ' fingerprint mismatch')
    actual = retrieval_fingerprint(project, expected['inputs']['exclusion_contract'], 1.5, .75)
    need(actual == expected, 'RUNNER_INCOMPATIBLE', 'Frozen R2 fingerprint mismatch')
    return {'local_runtime': 'VERIFIED', 'python': platform.python_version(), 'machine': platform.machine(),
            'P2_fingerprint': canonical_sha(lock['P2']), 'R2_fingerprint': actual['fingerprint'],
            'hosted_validation': 'HOSTED_NOT_RUN' if os.environ.get('GITHUB_ACTIONS') != 'true' else 'PENDING_HOSTED_EVIDENCE'}


def initial_checkpoint(actual_head, actual_branch, status, lock):
    need(actual_head == lock['starting_checkpoint'] and actual_branch == 'ci-upstream-refresh' and not status,
         'CHECKPOINT_FAILED', 'Unexpected starting checkpoint or dirty worktree')


def reference(registry, key):
    need(registry.get('schema_version') == 1 and isinstance(registry.get('baselines'), dict), 'INVALID_REFERENCE', 'Invalid registry')
    value = registry['baselines'].get(key)
    need(value is not None, 'BOOTSTRAP_REQUIRED', 'No reviewed trusted baseline: ' + key)
    required = {'baseline_kind', 'baseline_id', 'package_checksum', 'generation', 'parent', 'source_state',
                'upstreams', 'build_fingerprint', 'P3_fingerprint', 'P4_1_fingerprint', 'approval_identity',
                'P4_1_verification', 'storage_locator', 'transport_sha256', 'policy_sha256', 'source_provenance'}
    need(isinstance(value, dict) and required <= value.keys(), 'INVALID_REFERENCE', 'Incomplete trusted baseline reference')
    return value


def provenance_gate(profile, revisions, ref, lock):
    need(ref['upstreams'] == revisions, 'UPSTREAM_PROVENANCE_BLOCKED', 'Requested target differs from this anchor')
    need(revisions == lock['upstreams'], 'UPSTREAM_PROVENANCE_BLOCKED', 'Real changed commits need a versioned target-provenance contract')
    need(profile != 'formal', 'UPSTREAM_PROVENANCE_BLOCKED', 'P3 marks targets as derived fixtures; real-upstream formal validation is not implemented')
    need(ref['source_provenance'] in ('PINNED_BYTE_COPY', 'EXPERIMENTAL'), 'INVALID_REFERENCE', 'Pinned experiments must explicitly retain their experimental provenance')


def authorize(ref, policy_path, expected_reference_sha, profile, lock):
    need(canonical_sha(ref) == expected_reference_sha, 'INVALID_REFERENCE', 'External reference pin mismatch')
    need(sha(policy_path) == ref['policy_sha256'], 'AUTHORIZATION_FAILED', 'External bootstrap policy checksum mismatch')
    policy = read(policy_path)
    expected = {k: ref[k] for k in ('baseline_id', 'package_checksum', 'source_state', 'upstreams', 'build_fingerprint')}
    need(policy.get('scope') == expected and policy.get('profile') == profile and policy.get('experiment_id') and
         policy.get('operation') == 'PINNED_RESEARCH_CANDIDATE' and policy.get('research_only') is True,
         'AUTHORIZATION_FAILED', 'Missing/mismatched externally frozen research scope')
    need(all(type(policy.get(k)) is type(v) and policy.get(k) == v for k, v in RESTRICTIONS.items()),
         'AUTHORIZATION_FAILED', 'Forbidden research/production privilege')
    need(ref['build_fingerprint'] == canonical_sha(lock['P2']) and ref['P3_fingerprint'] == canonical_sha(lock['P3']) and
         ref['P4_1_fingerprint'] == canonical_sha(lock['P4_1']), 'IMPLEMENTATION_INCOMPATIBLE', 'Reference contract mismatch')
    if ref['baseline_kind'] == 'p2':
        need(policy.get('p2_anchor_authorized') is True and ref['P4_1_verification'] == 'NOT_APPLICABLE_P2_ANCHOR',
             'BOOTSTRAP_REQUIRED', 'P2 requires explicit bootstrap authorization; it is not P4.1 approved')
    else:
        need(ref['baseline_kind'] == 'research' and ref['P4_1_verification'] == 'P4_1_VERIFIED',
             'AUTHORIZATION_FAILED', 'Historical P4 approval is insufficient')
    return policy


def verify_baseline(project, root, ref, lock):
    from .research_baseline import read_parent
    info = read_parent(project, root, canonical_sha(lock['P2']), baseline_format=ref['baseline_kind'])
    for field in ('baseline_id', 'package_checksum', 'generation', 'upstreams'):
        need(info[field] == ref[field], 'INVALID_BASELINE', 'Baseline/reference mismatch: ' + field)
    if ref['baseline_kind'] == 'research':
        need(info['P4_1_verification'] == ref['P4_1_verification'], 'INVALID_BASELINE', 'Actual baseline lacks P4.1 verification')
        parent = read(Path(root) / 'baseline.json')['identity']['parent']
    else:
        parent = None
    need(parent == ref['parent'], 'INVALID_BASELINE', 'Parent lineage does not match trusted reference')
    need('sha256-' + canonical_sha(read(Path(root) / 'selection.json')) == ref['source_state'],
         'INVALID_BASELINE', 'Baseline source-state mismatch')
    return info


def check_archive(path, expected_sha):
    need(re.fullmatch(r'[0-9a-f]{64}', expected_sha or '') and sha(path) == expected_sha,
         'CORRUPT_DOWNLOAD', 'Transport checksum mismatch')
    with tarfile.open(path, 'r:*') as archive:
        names = set(); size = 0
        for item in archive:
            p = PurePosixPath(item.name); size += item.size
            need(item.isfile() and not p.is_absolute() and '..' not in p.parts and '\\' not in item.name and
                 str(p) == item.name and item.name not in names and size <= 4 * 1024**3,
                 'UNSAFE_ARCHIVE', 'Unsafe/duplicate/oversized archive member')
            names.add(item.name)


def restore(project, archive, destination, ref, lock):
    from .corpus_snapshot import restore_snapshot
    from .research_baseline import restore_baseline
    check_archive(archive, ref['transport_sha256'])
    fp = canonical_sha(lock['P2'])
    if ref['baseline_kind'] == 'p2':
        restore_snapshot(archive, destination, fp)
    else:
        restore_baseline(project, archive, destination, fp, ref['transport_sha256'])
    return verify_baseline(project, destination, ref, lock)


def download(locator, destination, expected_sha):
    """Public immutable release locator from reviewed config; no credentials."""
    def allowed(url, redirect=False):
        u = urllib.parse.urlsplit(url)
        hosts = ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com') if redirect else ('github.com',)
        need(u.scheme == 'https' and u.hostname in hosts and not u.username and not u.password and
             u.port in (None, 443), 'UNSAFE_INPUT', 'Unsupported baseline storage host')
        if not redirect:
            need(re.fullmatch(r'/(Xiangyu-Hu/SPHinXsistant|XiangruZhang007/SPHinX_AI_Assistant)/releases/download/[^/]+/[^/]+', u.path),
                 'UNSAFE_INPUT', 'Only reviewed project release locators are supported')
    allowed(locator)
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            allowed(newurl, True)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    destination = Path(destination)
    need(not destination.exists(), 'INVALID_OUTPUT', 'Download destination must be new')
    try:
        with urllib.request.build_opener(Redirect()).open(locator, timeout=60) as source, destination.open('xb') as target:
            count = 0
            while data := source.read(1024 * 1024):
                count += len(data)
                need(count <= 2 * 1024**3, 'CORRUPT_DOWNLOAD', 'Oversized compressed baseline')
                target.write(data)
        need(sha(destination) == expected_sha, 'CORRUPT_DOWNLOAD', 'Downloaded checksum mismatch')
    except GateError:
        raise
    except OSError as error:
        raise GateError('BASELINE_STORAGE_UNAVAILABLE', str(error)) from error


def verify_sources(project, root, base, revisions, *, git_checkout=False):
    from .corpus_snapshot_build import selected_files
    selected = selected_files(project, root)
    need(selected == read(Path(base) / 'selection.json'), 'SOURCE_STATE_MISMATCH', 'Pinned source bytes/modes differ from the authorized anchor')
    if git_checkout:
        for repository, commit in revisions.items():
            checkout = Path(root) / 'repos' / repository
            def git(*args):
                return subprocess.check_output(['git', '--no-optional-locks', '-C', str(checkout), *args], text=True).strip()
            need(git('rev-parse', 'HEAD') == commit and not git('status', '--porcelain=v1', '--untracked-files=all'),
                 'SOURCE_STATE_MISMATCH', 'Incorrect/dirty upstream checkout')
            tree = git('ls-tree', '-r', 'HEAD')
            entries = {line.split('\t', 1)[1]: line.split('\t', 1)[0].split() for line in tree.splitlines()}
            for item in selected:
                if item['repository'] == repository:
                    need(entries.get(item['path']) == [item['git_mode'], 'blob', item['git_blob']],
                         'SOURCE_STATE_MISMATCH', 'Selected bytes/mode do not match actual Git objects')
    return selected


def materialize_anchor(project, base, destination, ref, lock):
    """Reconstruct only an isolated source copy, including verified Git modes.

    Historical P2 stored source bytes and git_mode in its selection, but its
    source transport does not preserve executable permissions. Never fix that
    by changing the sealed snapshot or ignoring mode identity.
    """
    from .corpus_snapshot import checked_file
    base, destination = Path(base), Path(destination)
    verify_baseline(project, base, ref, lock)
    need(not destination.exists() and not destination.is_symlink(), 'INVALID_OUTPUT', 'Source destination must be new')
    for protected in (Path(project), base):
        need(not destination.resolve().is_relative_to(protected.resolve()) and
             not protected.resolve().is_relative_to(destination.resolve()), 'INVALID_OUTPUT', 'Source copy overlaps protected input')
    selection = read(base / 'selection.json')
    destination.mkdir(parents=True, exist_ok=False)
    executable = 0
    for item in selection:
        relative = 'repos/' + item['repository'] + '/' + item['path']
        source = ('sources/' + item['repository'] + '/' + item['path']) if ref['baseline_kind'] == 'p2' else 'input/' + relative
        need(item['git_mode'] in ('100644', '100755'), 'INVALID_BASELINE', 'Unsupported selected source mode')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_file(base, source), target)
        target.chmod(0o755 if item['git_mode'] == '100755' else 0o644)
        executable += item['git_mode'] == '100755'
    verify_sources(project, destination, base, ref['upstreams'])
    result = {'source_state': ref['source_state'], 'files': len(selection), 'executable_files': executable,
              'source_provenance': 'PINNED_BYTE_COPY', 'original_snapshot_modified': False}
    write(destination / 'materialization.json', result)
    return result


def decision(profile, evidence):
    required = ('runtime', 'baseline', 'provenance', 'independent_full', 'four_artifacts', 'candidate_128', 'full_128', 'execution')
    passed = profile == 'formal' and all(evidence.get(k) is True for k in required)
    # This module has no qualified live provenance provider, even for a caller's flag.
    need(not passed, 'UPSTREAM_PROVENANCE_BLOCKED', 'Formal PASS cannot be supplied by completion flags')
    return {'formal_acceptance': 'NOT_ASSESSED', **RESTRICTIONS}


def verify_evaluated_pair(project, candidate, full, directory, quality, lock, proof):
    """Actual P4.1 layers A/B/C, without granting any approval privilege."""
    from .corpus_snapshot import read_records
    from .approval_evidence import rescore, frozen_implementation, same, replay
    from .research_baseline import compare_full
    compare_full(project, candidate, full, canonical_sha(lock['P2']))
    spec = frozen_implementation(project, Path(project) / 'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json', lock['quality_policy'])
    rows = {}
    for label, root in [('incremental', Path(candidate)), ('full', Path(full))]:
        path = Path(directory) / (label + '-raw-results.json')
        rows[label] = read(path)
        metrics = rescore(rows[label], spec, read_records(root / 'corpus.jsonl'),
                          read_records(root / 'components/documentation.jsonl'))
        need(same(metrics, quality['metrics']), 'RETRIEVAL_MISMATCH', 'Frozen metrics mismatch')
    need(same(rows['incremental'], rows['full']), 'RETRIEVAL_MISMATCH', 'Candidate/full results differ')
    need(sha(Path(directory) / 'incremental-raw-results.json') == quality['raw_results_sha256'],
         'RETRIEVAL_MISMATCH', 'Original result checksum mismatch')
    Path(proof).mkdir(exist_ok=False)
    for label, root in [('incremental', Path(candidate)), ('full', Path(full))]:
        actual = replay(spec, root / 'corpus.jsonl', root / 'components/documentation.jsonl')
        need(same(actual, rows[label]), 'RETRIEVAL_EXECUTION_FAILED', 'Independent frozen R2 replay mismatch')
        write(Path(proof) / (label + '-replay.json'), actual)
    write(Path(proof) / 'verification.json', {'layers': ['A', 'B', 'C'], 'status': 'VERIFIED',
           'original_combinations': 256, 'independent_replay_combinations': 256,
           'candidate_corpus_sha256': sha(Path(candidate) / 'corpus.jsonl'),
           'full_corpus_sha256': sha(Path(full) / 'corpus.jsonl'), **RESTRICTIONS})
    return rows


def run_local(project, base, source_root, output, ref, policy_path, expected_reference_sha,
              *, profile='pinned-research', lock=None, historical_path, git_checkout=False):
    from .corpus_snapshot import read_records
    from .corpus_snapshot_build import build_full_snapshot
    from .incremental_corpus import build_incremental, verify_candidate
    from .research_baseline import compare_full
    from .research_quality import evaluate_quality
    from .approval_evidence import same
    project, base, source_root, output = map(Path, (project, base, source_root, output))
    lock = lock or read(LOCK); started = time.perf_counter()
    validate_request(profile, 'local-research', ref['upstreams'])
    provenance_gate(profile, ref['upstreams'], ref, lock)
    policy = authorize(ref, policy_path, expected_reference_sha, profile, lock)
    runtime = frozen_runtime(project, lock)
    info = verify_baseline(project, base, ref, lock)
    need(ref['baseline_kind'] == 'p2', 'BOOTSTRAP_REQUIRED',
         'Only the explicit P2 anchor is qualified for this pinned runner profile; research parents need a separately bound parent retrieval anchor')
    selection = verify_sources(project, source_root, base, ref['upstreams'], git_checkout=git_checkout)
    need(sha(historical_path) == policy.get('historical_rows_sha256'), 'AUTHORIZATION_FAILED', 'Frozen historical result anchor mismatch')
    need(not output.exists() and output.parent.is_dir(), 'INVALID_OUTPUT', 'Output must be new')
    for protected in (project, base, source_root):
        need(not output.resolve().is_relative_to(protected.resolve()) and not protected.resolve().is_relative_to(output.resolve()),
             'INVALID_OUTPUT', 'Output overlaps protected inputs')
    output.mkdir()
    write(output / 'trusted-reference.json', ref); shutil.copy2(policy_path, output / 'bootstrap-policy.json')
    write(output / 'runtime.json', runtime)
    if profile == 'diagnostic':
        result = {**decision(profile, {}), 'local_integration': 'DIAGNOSTIC_ONLY'}
        write(output / 'result.json', result); return result
    fp = canonical_sha(lock['P2'])
    built = build_incremental(project, base, source_root, output / 'candidate', expected_fingerprint=fp,
             research_only=True, expected_snapshot_id=info['baseline_id'], baseline_format=ref['baseline_kind'])
    inputs = {'chunker_commit': lock['external_chunker_commit'], 'project_head': subprocess.check_output(
              ['git', '--no-optional-locks', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip(),
              'build_contract': lock['P2'], 'upstreams': ref['upstreams'], 'selection': selection}
    full = build_full_snapshot(project, source_root, output / 'full', inputs, identity_scheme='portable-v1')
    parity = compare_full(project, output / 'candidate', output / 'full', fp)
    write(output / 'parity.json', parity)
    historical = read(historical_path)
    parent_sources = source_root  # verified identical selected bytes for the pinned profile
    quality = evaluate_quality(project, output / 'candidate', output / 'full', output / 'quality',
              benchmark=project / 'tests/retrieval_benchmarks/step_1_6_combined_frozen_v2.json',
              policy=lock['quality_policy'], expected_fingerprint=fp, historical_rows=historical,
              parent_rows=historical, parent_corpus=base / 'corpus.jsonl', parent_sources=parent_sources)
    rows = verify_evaluated_pair(project, output / 'candidate', output / 'full', output / 'quality',
                                quality, lock, output / 'execution-verification')
    need(same(historical, rows['incremental']), 'RETRIEVAL_MISMATCH',
         'Pinned unchanged P2 anchor failed to reproduce its externally frozen parent/history rows')
    verify_candidate(project, output / 'candidate', fp)
    need(verify_sources(project, source_root, base, ref['upstreams'], git_checkout=git_checkout) == selection,
         'SOURCE_STATE_MISMATCH', 'Sources changed during execution')
    result = {**decision(profile, {}), 'local_integration': 'LOCAL_VALIDATED',
              'profile': profile, 'source_provenance': ref['source_provenance'],
              'upstream_provenance': 'UPSTREAM_PROVENANCE_BLOCKED', 'baseline_id': info['baseline_id'],
              'candidate_id': built['candidate_id'], 'target_source_state': ref['source_state'],
              'independent_full_snapshot_id': full['snapshot_id'], 'parity': parity['status'],
              'candidate_evaluation_combinations': len(rows['incremental']), 'full_evaluation_combinations': len(rows['full']),
              'R2_execution': 'TWO_INDEPENDENT_EXECUTIONS_MATCH', 'reuse': built['summary'],
              'quality_metrics': quality['metrics'], 'seconds': time.perf_counter() - started}
    write(output / 'result.json', result)
    write(output / 'checksums.json', {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob('*')) if p.is_file()})
    return result


def package_candidate(root, archive):
    root, archive = Path(root), Path(archive)
    result = read(root / 'result.json')
    need(result.get('research_baseline_created') is False and result.get('automatic_research_approval') is False,
         'AUTHORIZATION_FAILED', 'Candidate packaging cannot approve a baseline')
    checks = read(root / 'checksums.json')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    need(actual == set(checks) | {'checksums.json'}, 'INVALID_CANDIDATE', 'Candidate member mismatch')
    for n,h in checks.items():
        from .corpus_snapshot import checked_file
        need(sha(checked_file(root, n)) == h, 'INVALID_CANDIDATE', 'Candidate evidence checksum mismatch')
    with tarfile.open(archive, 'x:gz') as tar:
        for path in sorted(root.rglob('*')):
            need(not path.is_symlink(), 'UNSAFE_ARCHIVE', 'Candidate symlink forbidden')
            if path.is_file():tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return {'transport_sha256': sha(archive), 'remote_storage': 'NOT_UPLOADED', **RESTRICTIONS}
