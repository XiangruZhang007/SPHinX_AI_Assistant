"""Explicitly authorized snapshot recovery. Public Release reads only; no writes.

The expected reference digest is supplied outside the downloaded package. An
integrity check is not an authorization and a P2 anchor is not research approval.
"""
from pathlib import Path
import os
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request

from . import ci_v2_research as c
from .corpus_snapshot import checked_file, digest, file_digest, read_json

REGISTRY = c.ROOT / 'configs/ci_v2_schema2_trusted_baselines.json'
RELEASE_REPOSITORY = 'XiangruZhang007/SPHinX_AI_Assistant'
FORMATS = {'p2-snapshot-v1': (1, 'EXPLICIT_P2_BOOTSTRAP'),
           'schema2-research-baseline': (2, 'EXPLICIT_RESEARCH_BASELINE_REUSE')}
DOWNLOAD_IMPLEMENTATION = 'atomic-no-clobber-v2'


def validate_reference(ref, expected_sha, *, remote=False, controlled=False):
    c.need(digest(ref) == expected_sha, 'INVALID_REFERENCE', 'Independent trusted reference checksum mismatch')
    required = {'schema_version', 'package_format', 'accepted_package_schema', 'node_id', 'package_checksum',
        'manifest_sha256', 'transport_sha256', 'source_state', 'upstreams', 'build_fingerprint',
        'corpus_sha256', 'authorization', 'storage', 'parent_rows_sha256'}
    c.need(isinstance(ref, dict) and required <= ref.keys() and type(ref['schema_version']) is int
           and ref['schema_version'] == 2, 'INVALID_REFERENCE', 'Incomplete schema-2 trust reference')
    c.need(ref['package_format'] in FORMATS, 'INVALID_REFERENCE', 'Unsupported exact package format')
    schema, authorization = FORMATS[ref['package_format']]
    c.need(type(ref['accepted_package_schema']) is int and ref['accepted_package_schema'] == schema,
           'INVALID_REFERENCE', 'Unsupported accepted package schema')
    for field in ('package_checksum', 'manifest_sha256', 'transport_sha256', 'build_fingerprint', 'corpus_sha256', 'parent_rows_sha256'):
        c.need(isinstance(ref[field], str) and re.fullmatch('[0-9a-f]{64}', ref[field]), 'INVALID_REFERENCE', 'Invalid hash: ' + field)
    for field in ('node_id', 'source_state'):
        c.need(isinstance(ref[field], str) and re.fullmatch('sha256-[0-9a-f]{64}', ref[field]), 'INVALID_REFERENCE', 'Invalid identity: ' + field)
    c.validate_request('formal', 'schema2', ref['upstreams'])
    auth = ref['authorization']
    c.need(auth.get('type') == authorization and auth.get('scope') == 'ISOLATED_RESEARCH_PARENT_REUSE'
        and auth.get('research_only') is True and auth.get('experiment_id')
        and auth.get('automatic_research_approval') is False and auth.get('production_promotion') == 'NOT_AUTHORIZED',
        'AUTHORIZATION_FAILED', 'Missing explicit scoped bootstrap/research authorization')
    kind = ref['storage'].get('kind')
    c.need(kind in ('GITHUB_RELEASE_ASSET', 'LOCAL_QUALIFICATION_ONLY'), 'INVALID_REFERENCE', 'Unsupported trusted storage')
    if remote:
        c.need(not controlled and kind == 'GITHUB_RELEASE_ASSET', 'CONFIGURATION_REQUIRED', 'Remote registry needs a reviewed immutable Release asset')
        release_locator(ref['storage'])
        release_locator(ref.get('parent_rows_storage', {}))
    else:
        c.need(not controlled or kind == 'LOCAL_QUALIFICATION_ONLY', 'AUTHORIZATION_FAILED', 'Fixture privileges cannot enter remote trust')
    return ref


def registry_reference(registry, key):
    c.need(registry.get('schema_version') == 2 and isinstance(registry.get('baselines'), dict), 'INVALID_REFERENCE', 'Wrong registry schema')
    c.validate_request('formal', key, c.read(c.LOCK)['upstreams'])
    entry = registry['baselines'].get(key)
    c.need(isinstance(entry, dict), 'BOOTSTRAP_REQUIRED', 'No reviewed schema-2 trusted baseline: ' + key)
    validate_reference(entry['reference'], entry['expected_reference_sha256'], remote=True)
    return entry


def verify_package(project, root, ref, *, controlled=False):
    from . import upstream_provenance as u
    from .research_baseline import read_parent
    root = Path(root); fp = ref['build_fingerprint']
    c.need(file_digest(checked_file(root, 'checksums.json')) == ref['manifest_sha256'], 'INVALID_BASELINE', 'Sealed manifest mismatch')
    if ref['package_format'] == 'p2-snapshot-v1':
        info = read_parent(project, root, fp, baseline_format='p2')
        node, package = info['baseline_id'], info['package_checksum']
        upstreams = info['upstreams']; approval = 'NOT_APPLICABLE_P2_ANCHOR'
        c.need(not (root / 'upstream-baseline.json').exists(), 'INVALID_BASELINE', 'Mixed baseline formats')
    else:
        info = u.verify(project, root, fp, expected_id=ref['node_id'])
        c.need(info['receipt']['kind'] == 'RESEARCH_BASELINE' and info['P4_1_verification'] == 'P4_1_VERIFIED',
               'INVALID_BASELINE', 'Unapproved candidate is not a trusted research baseline')
        c.need(info['provenance_type'] == ('CONTROLLED_GIT_FIXTURE' if controlled else 'GENUINE_UPSTREAM'),
               'INVALID_BASELINE', 'Controlled provenance cannot become a genuine trusted baseline')
        node, package = info['node_id'], info['receipt']['package_checksum']
        upstreams = info['upstreams']; approval = info['P4_1_verification']
    selected = read_json(root / 'selection.json')
    checks = {'node_id': node, 'package_checksum': package, 'upstreams': upstreams,
              'source_state': u.state(selected), 'corpus_sha256': file_digest(root / 'corpus.jsonl')}
    for name, actual in checks.items():
        c.need(actual == ref[name], 'INVALID_BASELINE', 'Trusted package binding mismatch: ' + name)
    return {**checks, 'approval': approval, 'package_format': ref['package_format'],
            'parent_reuse': 'P2_BOOTSTRAP_ONLY' if ref['package_format'] == 'p2-snapshot-v1' else 'P4_1_VERIFIED',
            'controlled_fixture': controlled, 'production_promotion': 'NOT_AUTHORIZED'}


def extract_regular(archive, destination, expected_sha):
    """Bounded new-directory extraction; reject links, aliases and path traversal."""
    archive, destination = Path(archive), Path(destination)
    c.check_archive(archive, expected_sha)
    c.need(not destination.exists() and not destination.is_symlink() and destination.parent.is_dir(),
           'INVALID_OUTPUT', 'Restore destination must be new')
    c.need(not archive.resolve().is_relative_to(destination.resolve()), 'INVALID_OUTPUT', 'Archive overlaps destination')
    destination.mkdir()
    with tarfile.open(archive, 'r:*') as tar:
        for member in tar:
            c.need(member.isfile(), 'UNSAFE_ARCHIVE', 'Archive changed to a nonregular member during extraction')
            target = destination / member.name
            c.need(target.resolve().is_relative_to(destination.resolve()), 'UNSAFE_ARCHIVE', 'Extraction escaped destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as stream:
                shutil.copyfileobj(source, stream)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    c.need(file_digest(archive) == expected_sha, 'CORRUPT_DOWNLOAD', 'Archive changed during extraction')


def restore(project, archive, destination, ref, expected_sha, *, controlled=False):
    from . import upstream_provenance as u
    validate_reference(ref, expected_sha, controlled=controlled)
    destination = Path(destination)
    u._new_output(destination, (project, archive))
    with tempfile.TemporaryDirectory(prefix='.trusted-restore-', dir=destination.parent) as tmp:
        staged = Path(tmp) / 'payload'
        extract_regular(archive, staged, ref['transport_sha256'])
        info = verify_package(project, staged, ref, controlled=controlled)
        os.replace(staged, destination)
    return info


def release_locator(storage):
    c.need(storage.get('kind') == 'GITHUB_RELEASE_ASSET' and storage.get('repository') == RELEASE_REPOSITORY
        and all(type(storage.get(k)) is int and storage[k] > 0 for k in ('release_id', 'asset_id'))
        and isinstance(storage.get('tag'), str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', storage['tag'])
        and storage['tag'].lower() not in ('latest', 'main', 'master')
        and isinstance(storage.get('asset_name'), str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', storage['asset_name'])
        and storage.get('immutable_required') is True, 'UNSAFE_INPUT', 'Unpinned or incomplete immutable Release locator')
    return 'https://api.github.com/repos/' + RELEASE_REPOSITORY


def _request(url, *, binary=False):
    """Public reads, no credentials. Bound redirects even for octet-stream GET."""
    def allowed(target):
        parsed = urllib.parse.urlsplit(target)
        c.need(parsed.scheme == 'https' and parsed.hostname in
            ('api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
            and not parsed.username and not parsed.password and parsed.port in (None, 443),
            'UNSAFE_INPUT', 'Unsafe Release download redirect')
    allowed(url)
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            allowed(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    request = urllib.request.Request(url, headers={'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
                                                  'User-Agent': 'SPHinXsistant-research-recovery'})
    return urllib.request.build_opener(Redirect()).open(request, timeout=60)


def download(storage, destination, expected_sha):
    """Numeric immutable asset identity + externally pinned bytes, never latest."""
    import json
    destination = Path(destination)
    c.need(not destination.exists() and not destination.is_symlink(), 'INVALID_OUTPUT', 'Download destination must be new')
    base = release_locator(storage)
    def metadata(url):
        with _request(url) as stream:
            raw = stream.read(4 * 1024**2 + 1)
        c.need(len(raw) <= 4 * 1024**2, 'CORRUPT_DOWNLOAD', 'Oversized Release metadata')
        return json.loads(raw)
    release = metadata(base + '/releases/' + str(storage['release_id']))
    c.need(release['id'] == storage['release_id'] and release.get('immutable') is True
        and release.get('draft') is False and release['tag_name'] == storage['tag'],
        'INVALID_REFERENCE', 'Release is not the pinned published immutable release')
    asset_url = base + '/releases/assets/' + str(storage['asset_id'])
    asset = metadata(asset_url)
    c.need(asset['id'] == storage['asset_id'] and asset['name'] == storage['asset_name']
        and asset.get('state') == 'uploaded' and type(asset.get('size')) is int
        and 0 < asset['size'] <= 2 * 1024**3
        and any(x['id'] == storage['asset_id'] and x['name'] == storage['asset_name'] for x in release['assets']),
        'INVALID_REFERENCE', 'Asset does not belong to pinned release/name')
    if asset.get('digest') is not None:
        c.need(asset['digest'] == 'sha256:' + expected_sha, 'CORRUPT_DOWNLOAD', 'Release digest mismatch')
    # Only a completely verified private file may become the final destination.
    # A same-filesystem exclusive link publishes it atomically without replacing
    # a file another process/operator created while the download was in flight.
    with tempfile.TemporaryDirectory(prefix='.release-download-', dir=destination.parent) as tmp:
        staged = Path(tmp) / 'payload'
        with _request(asset_url, binary=True) as source, staged.open('xb') as target:
            size = 0
            while data := source.read(1024**2):
                size += len(data)
                c.need(size <= asset['size'], 'CORRUPT_DOWNLOAD', 'Oversized asset stream')
                target.write(data)
            target.flush()
            os.fsync(target.fileno())
        c.need(size == asset['size'] and file_digest(staged) == expected_sha, 'CORRUPT_DOWNLOAD', 'Asset length/checksum mismatch')
        os.link(staged, destination)
    return {'release_id': release['id'], 'asset_id': asset['id'], 'sha256': expected_sha, 'immutable': True}
