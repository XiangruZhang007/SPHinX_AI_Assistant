"""Versioned, self-contained P4 research baselines. No production eligibility.

package_checksum is the SHA256 of the canonical logical content package receipt,
not a tar transport hash. Ancestry contains receipts, never embedded old corpora.
Actual immediate parent packages are verified at approval; portable receipts bind
that verified lineage for later standalone use. SHA alone is not remote trust.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from .corpus_snapshot import (SnapshotError, checked_file, digest, file_digest,
    read_json, write_json, verify_snapshot, snapshot_id)
from .incremental_corpus import (current_contract, orchestration_contract,
    verify_candidate, build_incremental)

KIND = 'p4-research-baseline'
VERSION = 1
P4_FILES = ('src/research_baseline.py', 'src/research_quality.py',
            'scripts/research_baseline.py', 'tests/test_research_baseline.py',
            'src/approval_evidence.py', 'tests/test_approval_evidence.py')
PARITY_FILES = ('corpus.jsonl', 'selection.json', 'inventory.json', 'extraction-graph.json')
EXECUTION_FILES = {'execution.json', 'base-verification.json', 'validation.json', 'checksums.json'}


def lifecycle_contract(project):
    return {'schema_version': 1, 'files': {n: file_digest(Path(project) / n) for n in P4_FILES},
            'P3_orchestration': orchestration_contract(project)}


def package_checksum(identifier, payload):
    return digest({'baseline_id': identifier, 'payload_sha256': payload})


def _receipt(identifier, identity):
    return {'baseline_id': identifier, 'identity': identity,
            'package_checksum': package_checksum(identifier, identity['payload_sha256'])}


def _check_receipt(receipt):
    identity, identifier = receipt['identity'], receipt['baseline_id']
    generation = identity['generation']
    if type(generation) is not int or generation < 0:
        raise SnapshotError('Invalid generation')
    if generation == 0:
        if identifier != snapshot_id(identity['P2_identity']) or identity['parent'] is not None:
            raise SnapshotError('Invalid P2 lineage anchor')
        if identity['payload_sha256'] != digest(identity['P2_identity']):
            raise SnapshotError('Invalid P2 content package receipt')
    elif identifier != 'sha256-' + digest(identity):
        raise SnapshotError('Baseline identity checksum mismatch')
    if receipt['package_checksum'] != package_checksum(identifier, identity['payload_sha256']):
        raise SnapshotError('Parent package checksum mismatch')


def _check_lineage(receipts):
    if not receipts:
        raise SnapshotError('Missing lineage')
    seen = set()
    for number, receipt in enumerate(receipts):
        _check_receipt(receipt)
        if receipt['baseline_id'] in seen or receipt['identity']['generation'] != number:
            raise SnapshotError('Cyclic/invalid lineage generation')
        seen.add(receipt['baseline_id'])
        if number:
            parent = receipt['identity']['parent']
            previous = receipts[number - 1]
            if parent != {k: previous[k] for k in ('baseline_id', 'package_checksum')}:
                raise SnapshotError('Incorrect parent identity/package checksum')


def _baseline_report(root):
    metadata = read_json(checked_file(root, 'baseline.json'))
    if (metadata.get('kind') != KIND or type(metadata.get('schema_version')) is not int or
            metadata['schema_version'] != VERSION):
        raise SnapshotError('Unsupported baseline schema')
    receipt = _receipt(metadata['baseline_id'], metadata['identity'])
    _check_lineage([*metadata['ancestry'], receipt])
    return metadata, receipt


def verify_baseline(project, root, expected_fingerprint, expected_id=None):
    root = Path(root)
    # P3 verifier checks all package hashes, original discovery, actual bytes,
    # modes, inventory, native graph and exact component/corpus association.
    report = verify_candidate(project, root, expected_fingerprint)
    metadata, receipt = _baseline_report(root)
    identity = metadata['identity']
    for ancestor in metadata['ancestry']:
        prior = ancestor['identity']
        prior_contract = prior['P2_identity'] if prior['generation'] == 0 else prior
        if prior_contract['build_fingerprint'] != expected_fingerprint or prior_contract['upstreams'] != identity['upstreams']:
            raise SnapshotError('Incompatible lineage build/source repository identity')
    if expected_id is not None and metadata['baseline_id'] != expected_id:
        raise SnapshotError('Unexpected baseline ID')
    if identity['build_fingerprint'] != expected_fingerprint:
        raise SnapshotError('Incompatible research baseline fingerprint')
    if identity['candidate_id'] != report['candidate_id']:
        raise SnapshotError('Candidate/baseline identity mismatch')
    files = read_json(root / 'checksums.json')
    payload = {n: h for n, h in files.items() if n != 'baseline.json'}
    if digest(payload) != identity['payload_sha256']:
        raise SnapshotError('Baseline payload identity mismatch')
    if digest(read_json(root / 'build-contract.json')) != expected_fingerprint:
        raise SnapshotError('Stored build contract mismatch')
    if digest(read_json(root / 'p3-orchestration-contract.json')) != identity['P3_fingerprint']:
        raise SnapshotError('Stored orchestration fingerprint mismatch')
    if digest(read_json(root / 'p4-contract.json')) != identity['P4_fingerprint']:
        raise SnapshotError('Stored lifecycle fingerprint mismatch')
    if identity['target_source_state'] != read_json(root / 'candidate.json')['identity']['target_fixture_id']:
        raise SnapshotError('Baseline source state mismatch')
    decision = read_json(root / 'research-decision.json')
    if decision != identity['decision'] or decision['research_use'] != 'RESEARCH_BASELINE_APPROVED':
        raise SnapshotError('Research baseline is not approved')
    if decision['automatic_reuse'] is not False or decision['production_promotion'] != 'NOT_AUTHORIZED':
        raise SnapshotError('Production approval is forbidden')
    parity = read_json(root / 'parity.json')
    quality = read_json(root / 'quality.json')
    auth = read_json(root / 'authorization.json')
    if parity['status'] != 'PARITY_PASS' or parity['candidate_id'] != report['candidate_id']:
        raise SnapshotError('Missing passing parity decision')
    if (set(parity.get('comparisons', {})) != set(PARITY_FILES) or
            parity.get('full_reference_independently_built') is not True or
            any(parity['comparisons'][n] != {'sha256': file_digest(root / n),
                'exact_bytes_equal': True} for n in PARITY_FILES)):
        raise SnapshotError('Invalid external parity artifact evidence')
    _check_decisions(auth, quality, report['candidate_id'], identity['parent']['baseline_id'],
                     file_digest(root / 'corpus.jsonl'), expected_fingerprint)
    p41 = 'P4_1_NOT_VERIFIED'
    if 'P4_1_evidence_sha256' in identity:
        from .approval_evidence import verify_bundle
        evidence = verify_bundle(project, root / 'approval-evidence',
                                 expected_manifest_sha=identity['P4_1_evidence_sha256'])
        if (evidence['candidate_id'] != report['candidate_id'] or
                evidence['parent_baseline_id'] != identity['parent']['baseline_id'] or
                evidence['source_state'] != identity['target_source_state'] or
                evidence['candidate_corpus_sha256'] != file_digest(root / 'corpus.jsonl') or
                read_json(root / 'approval-evidence/quality.json') != quality or
                read_json(root / 'approval-evidence/authorization.json') != auth):
            raise SnapshotError('Baseline/P4.1 evidence association mismatch')
        p41 = evidence['status']
    return {**report, 'kind': KIND, 'baseline_id': metadata['baseline_id'],
            'candidate_internal_parity': report['incremental_parity'],
            'external_full_rebuild_parity': 'PARITY_PASS', 'research_baseline_parity': 'PARITY_PASS',
            'P4_1_verification': p41, 'retrieval_evaluation': quality['assessment'],
            'generation': identity['generation'], 'package_checksum': receipt['package_checksum'],
            'upstreams': identity['upstreams'], 'research_use': 'RESEARCH_BASELINE_APPROVED',
            'ancestry_receipts': [*metadata['ancestry'], receipt]}


def read_parent(project, root, expected_fingerprint, *, baseline_format):
    root = Path(root)
    if baseline_format == 'p2':
        report = verify_snapshot(root, expected_fingerprint)
        if report['build_compatibility'] != 'COMPATIBLE':
            raise SnapshotError('Incompatible/unchecked P2 base')
        core = read_json(root / 'manifest.json')['identity']
        identity = {'generation': 0, 'parent': None, 'P2_identity': core, 'payload_sha256': digest(core)}
        receipt = _receipt(report['snapshot_id'], identity)
        return {'baseline_id': report['snapshot_id'], 'generation': 0,
                'package_checksum': receipt['package_checksum'], 'ancestry_receipts': [receipt],
                'upstreams': core['upstreams']}
    if baseline_format == 'research':
        return verify_baseline(project, root, expected_fingerprint)
    raise SnapshotError('Unsupported baseline format')


def compare_full(project, candidate, full, expected_fingerprint):
    candidate, full = Path(candidate), Path(full)
    verified = verify_candidate(project, candidate, expected_fingerprint)
    reference = verify_snapshot(full, expected_fingerprint)
    if reference['build_compatibility'] != 'COMPATIBLE':
        raise SnapshotError('Incompatible independent full reference')
    if (read_json(full / 'manifest.json')['identity']['upstreams'] !=
            read_json(candidate / 'provenance.json')['base_upstream_revisions']):
        raise SnapshotError('Independent full reference upstream provenance mismatch')
    equality = {n: (candidate / n).read_bytes() == (full / n).read_bytes() for n in PARITY_FILES}
    if not all(equality.values()):
        raise SnapshotError('Failed full-rebuild parity: ' + str(equality))
    return {'status': 'PARITY_PASS', 'candidate_id': verified['candidate_id'],
            'comparisons': {n: {'sha256': file_digest(candidate / n), 'exact_bytes_equal': True}
                            for n in PARITY_FILES}, 'full_reference_independently_built': True}


def _check_decisions(auth, quality, candidate_id, parent_id, corpus_sha, fingerprint):
    if (auth.get('schema_version') != 1 or auth.get('research_only') is not True or
            auth.get('approved') is not True or auth.get('candidate_id') != candidate_id or
            auth.get('parent_baseline_id') != parent_id or not auth.get('experiment_id') or
            auth.get('quality_status') != quality.get('status') or
            quality.get('status') not in ('QUALITY_ACCEPTED', 'QUALITY_REVIEW_REQUIRED') or
            quality.get('candidate_id') != candidate_id or quality.get('corpus_sha256') != corpus_sha or
            quality.get('build_fingerprint') != fingerprint or not quality.get('policy_sha256')):
        raise SnapshotError('Missing/rejected/mismatched research quality authorization')
    if quality.get('policy_sha256') != digest(quality.get('policy')):
        raise SnapshotError('Quality policy checksum mismatch')
    if (quality['policy'].get('historical_quality') == 'QUALITY_REVIEW_REQUIRED' and
            quality['status'] == 'QUALITY_ACCEPTED'):
        raise SnapshotError('Historical quality limitation cannot be upgraded automatically')
    if quality['status'] == 'QUALITY_REVIEW_REQUIRED' and auth.get('allow_quality_review') is not True:
        raise SnapshotError('Unresolved quality review is not explicitly authorized')
    if quality.get('assessment') not in ('FROZEN_128_EVALUATED', 'RECORDED_REVIEW_NOT_EVALUATED'):
        raise SnapshotError('Missing research quality assessment')
    if quality['assessment'] == 'FROZEN_128_EVALUATED' and (
            quality.get('combinations_each') != 128 or quality.get('independent_candidate_full_rows_equal') is not True):
        raise SnapshotError('Incomplete required frozen quality evaluation')


def approve_baseline(project, candidate, parent, full, output, *, expected_fingerprint,
                     parent_format, quality, authorization, approval_policy=None,
                     expected_policy_sha256=None, evidence=None):
    """Explicit local research decision; validates actual immediate parent package."""
    candidate, parent, full, output = map(Path, (candidate, parent, full, output))
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise SnapshotError('Baseline destination must be new with an existing parent')
    for protected in (candidate, parent, full, Path(project) / 'repos', Path(project) / 'tools'):
        if output.resolve().is_relative_to(protected.resolve()) or protected.resolve().is_relative_to(output.resolve()):
            raise SnapshotError('Baseline output overlaps protected input')
    verified = verify_candidate(project, candidate, expected_fingerprint)
    base = read_parent(project, parent, expected_fingerprint, baseline_format=parent_format)
    manifest = read_json(candidate / 'candidate.json')
    if manifest['identity']['base_snapshot_id'] != base['baseline_id']:
        raise SnapshotError('Candidate was built from a different parent')
    _check_decisions(authorization, quality, verified['candidate_id'], base['baseline_id'],
                     file_digest(candidate / 'corpus.jsonl'), expected_fingerprint)
    parity = compare_full(project, candidate, full, expected_fingerprint)
    contract = lifecycle_contract(project)
    with tempfile.TemporaryDirectory(prefix='.p4-approval-', dir=output.parent) as directory:
        stage = Path(directory) / 'baseline'
        stage.mkdir()
        for path in sorted(candidate.rglob('*')):
            if path.is_symlink():
                raise SnapshotError('Candidate contains symlink')
            if path.is_file() and path.relative_to(candidate).as_posix() not in EXECUTION_FILES:
                relative = path.relative_to(candidate)
                dest = stage / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        from .approval_evidence import create_evidence
        create_evidence(project, candidate, parent, full, stage / 'approval-evidence',
                        quality, authorization, approval_policy, expected_policy_sha256,
                        parent_report=base, evidence=evidence)
        decision = {'research_use': 'RESEARCH_BASELINE_APPROVED', 'automatic_reuse': False,
                    'production_promotion': 'NOT_AUTHORIZED', 'quality_status': quality['status']}
        for name, value in [('parity.json', parity), ('quality.json', quality),
                            ('authorization.json', authorization), ('research-decision.json', decision),
                            ('p4-contract.json', contract)]:
            write_json(stage / name, value)
        payload = {p.relative_to(stage).as_posix(): file_digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        identity = {'generation': base['generation'] + 1,
                    'parent': {k: base[k] for k in ('baseline_id', 'package_checksum')},
                    'candidate_id': verified['candidate_id'],
                    'target_source_state': manifest['identity']['target_fixture_id'],
                    'upstreams': base['upstreams'], 'build_fingerprint': expected_fingerprint,
                    'P3_fingerprint': digest(read_json(stage / 'p3-orchestration-contract.json')),
                    'P4_fingerprint': digest(contract), 'payload_sha256': digest(payload), 'decision': decision}
        identity['P4_1_evidence_sha256'] = file_digest(stage / 'approval-evidence/checksums.json')
        metadata = {'kind': KIND, 'schema_version': VERSION, 'baseline_id': 'sha256-' + digest(identity),
                    'identity': identity, 'ancestry': base['ancestry_receipts']}
        write_json(stage / 'baseline.json', metadata)
        write_json(stage / 'checksums.json', {**payload, 'baseline.json': file_digest(stage / 'baseline.json')})
        verified_output = verify_baseline(project, stage, expected_fingerprint)
        if read_parent(project, parent, expected_fingerprint, baseline_format=parent_format) != base:
            raise SnapshotError('Immediate parent changed during approval')
        if lifecycle_contract(project) != contract:
            raise SnapshotError('Lifecycle implementation changed during approval')
        output.mkdir(exist_ok=False)
        try:
            os.replace(stage, output)
        except BaseException:
            output.rmdir()
            raise
    return verified_output


def build_next(project, base, source_root, output, *, expected_fingerprint, expected_id):
    return build_incremental(project, base, source_root, output,
                             expected_fingerprint=expected_fingerprint, research_only=True,
                             expected_snapshot_id=expected_id, baseline_format='research')


def revalidate_legacy(project, baseline, parent, full, output, *, expected_fingerprint,
                      expected_id, parent_format, quality, authorization, approval_policy,
                      expected_policy_sha256, evidence=None):
    """Write a new sidecar; never alter a historical package or its decision."""
    from .approval_evidence import create_evidence, verify_bundle
    baseline, parent, output = map(Path, (baseline, parent, output))
    prior = verify_baseline(project, baseline, expected_fingerprint, expected_id)
    if prior['P4_1_verification'] != 'P4_1_NOT_VERIFIED':
        raise SnapshotError('Expected a historical P4 package')
    base = read_parent(project, parent, expected_fingerprint, baseline_format=parent_format)
    if read_json(baseline / 'baseline.json')['identity']['parent']['baseline_id'] != base['baseline_id']:
        raise SnapshotError('Historical revalidation parent mismatch')
    _check_decisions(authorization, quality, prior['candidate_id'], base['baseline_id'],
                     file_digest(baseline / 'corpus.jsonl'), expected_fingerprint)
    compare_full(project, baseline, full, expected_fingerprint)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise SnapshotError('Sidecar destination must be new')
    for protected in (baseline, parent, Path(full), Path(project)):
        if output.resolve().is_relative_to(protected.resolve()) or protected.resolve().is_relative_to(output.resolve()):
            raise SnapshotError('Sidecar overlaps protected input')
    with tempfile.TemporaryDirectory(prefix='.p41-revalidation-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'evidence'
        create_evidence(project, baseline, parent, full, stage, quality, authorization,
                        approval_policy, expected_policy_sha256, parent_report=base, evidence=evidence,
                        legacy_subject={k: prior[k] for k in ('baseline_id', 'package_checksum')})
        result = verify_bundle(project, stage)
        if verify_baseline(project, baseline, expected_fingerprint, expected_id) != prior:
            raise SnapshotError('Historical package changed during revalidation')
        output.mkdir(exist_ok=False)
        try:
            os.replace(stage, output)
        except BaseException:
            output.rmdir()
            raise
    return {**result, 'historical_subject': {k: prior[k] for k in ('baseline_id', 'package_checksum')}}


def package_baseline(project, root, archive, expected_fingerprint):
    report = verify_baseline(project, root, expected_fingerprint)
    with tarfile.open(archive, 'x:gz') as tar:
        for path in sorted(Path(root).rglob('*')):
            if path.is_symlink():
                raise SnapshotError('Baseline symlink')
            if path.is_file():
                tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return {'archive_sha256': file_digest(archive), 'baseline_id': report['baseline_id'],
            'package_checksum': report['package_checksum']}


def restore_baseline(project, archive, output, expected_fingerprint, expected_archive_sha256):
    if not expected_archive_sha256 or file_digest(archive) != expected_archive_sha256:
        raise SnapshotError('Baseline transport checksum mismatch')
    output = Path(output)
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise SnapshotError('Restore destination must be new')
    with tempfile.TemporaryDirectory(prefix='.p4-restore-', dir=output.parent) as directory:
        stage = Path(directory) / 'baseline'
        with tarfile.open(archive, 'r:*') as tar:
            members = tar.getmembers()
            names = set()
            for member in members:
                relative = Path(member.name)
                if (not member.isfile() or relative.is_absolute() or '..' in relative.parts or
                        member.name in names):
                    raise SnapshotError('Unsafe baseline archive')
                names.add(member.name)
            stage.mkdir()
            for member in members:
                dest = stage / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, dest.open('xb') as target:
                    shutil.copyfileobj(source, target)
                dest.chmod(member.mode & 0o777)
        report = verify_baseline(project, stage, expected_fingerprint)
        output.mkdir(exist_ok=False)
        try:
            os.replace(stage, output)
        except BaseException:
            output.rmdir()
            raise
    return report
