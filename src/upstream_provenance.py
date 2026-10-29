"""Explicit schema-2 research orchestration; schema-1 implementations are frozen.

No network, approval, pointer update or production promotion happens in build().
Git object proofs are integrity proofs. Genuine publication is a separately pinned
local research policy, not a signature inferred from a directory or remote URL.
"""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from .corpus_snapshot import (SnapshotError, checked_file, digest, file_digest,
    read_json, write_json, verify_snapshot, snapshot_id)
from .corpus_snapshot_build import selected_files, build_full_snapshot
from . import incremental_corpus as engine
from .research_baseline import PARITY_FILES, read_parent, _check_decisions, lifecycle_contract, package_checksum as legacy_package_checksum
from .approval_evidence import create_evidence, verify_bundle, same
from .ci_v2_research import frozen_runtime

REPOSITORIES = {'SPHinXsys': 'https://github.com/Xiangyu-Hu/SPHinXsys.git',
                'SPHinXsim': 'https://github.com/Xiangyu-Hu/SPHinXsim.git'}
VERSION = 2
FILES = ('src/upstream_provenance.py', 'scripts/upstream_research.py',
         'tests/test_upstream_provenance.py', 'docs/ci_v2_p5a_1_design.md')
LIMITS = {'automatic_research_approval': False, 'automatic_reuse': False,
          'production_promotion': 'NOT_AUTHORIZED'}


def require(ok, message):
    if not ok:
        raise SnapshotError(message)


def contract(project):
    return {'schema_version': VERSION, 'purpose': 'verified real Git revision lineage',
            'files': {n: file_digest(Path(project) / n) for n in FILES},
            'legacy_engine': engine.orchestration_contract(project),
            'legacy_approval': lifecycle_contract(project),
            'runtime_lock_sha256': file_digest(Path(project) / 'configs/ci_v2_runtime_lock.json'),
            'runtime': 'frozen P5A runtime; actual hash seed 0, environment honored',
            'relationships': 'same commit or proven fast-forward only',
            'compatibility': 'exact unchanged P2 fingerprint'}


def state(selection):
    engine.selection_map(selection)
    return 'sha256-' + digest(selection)


def _git(root, *args):
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                                   '-C', str(root), *args], env=env, stderr=subprocess.PIPE)


def _object(raw, kind):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def _encoded(raw):
    return base64.b64encode(raw).decode('ascii')


def _decoded(value, kind, oid):
    raw = base64.b64decode(value, validate=True)
    require(_object(raw, kind) == oid, 'Git object checksum mismatch')
    return raw


def _commit(raw):
    header = raw.split(b'\n\n', 1)[0].splitlines()
    trees = [s[5:].decode('ascii') for s in header if s.startswith(b'tree ')]
    parents = [s[7:].decode('ascii') for s in header if s.startswith(b'parent ')]
    require(len(trees) == 1 and re.fullmatch('[0-9a-f]{40}', trees[0]), 'Invalid commit tree')
    return trees[0], parents


def _tree(raw):
    entries = {}; cursor = 0
    while cursor < len(raw):
        space = raw.index(b' ', cursor); end = raw.index(b'\0', space)
        mode = raw[cursor:space].decode('ascii'); name = raw[space + 1:end].decode('utf-8')
        require(name not in entries and '/' not in name and name not in ('.', '..'), 'Invalid Git tree entry')
        oid = raw[end + 1:end + 21]
        require(len(oid) == 20, 'Truncated tree object')
        entries[name] = (mode, oid.hex()); cursor = end + 21
    return entries


def verify_proof(proof, selection):
    """Offline commit -> tree -> path -> raw blob chain, with no original root."""
    require(set(proof) == {'repository', 'url', 'commit', 'commit_raw', 'trees'}, 'Invalid Git proof schema')
    repo = proof['repository']
    require(proof['url'] == REPOSITORIES.get(repo), 'Incorrect repository identity')
    tree, _ = _commit(_decoded(proof['commit_raw'], 'commit', proof['commit']))
    objects = {oid: _tree(_decoded(raw, 'tree', oid)) for oid, raw in proof['trees'].items()}
    used = set()
    for item in selection:
        if item['repository'] != repo:
            continue
        current = tree
        parts = item['path'].split('/')
        for index, name in enumerate(parts):
            require(current in objects, 'Missing Git directory proof')
            used.add(current)
            mode, oid = objects[current].get(name, (None, None))
            if index == len(parts) - 1:
                require((mode, oid) == (item['git_mode'], item['git_blob']), 'Selected bytes/mode differ from Git tree')
            else:
                require(mode == '40000', 'Unsupported symlink/gitlink path')
                current = oid
    require(used == set(objects), 'Extraneous/unbound Git tree proof')
    return proof['commit']


def verify_sources(project, source, revisions):
    require(set(revisions) == set(REPOSITORIES) and all(isinstance(s, str) and
            re.fullmatch('[0-9a-f]{40}', s) for s in revisions.values()), 'Invalid exact commit pair')
    source = Path(source)
    for repo in REPOSITORIES:
        root = source / 'repos' / repo
        require(not root.is_symlink() and root.resolve().is_relative_to(source.resolve()), 'Unsafe checkout root')
        require(_git(root, 'rev-parse', '--show-toplevel').decode().strip() == str(root.resolve()), 'Not a standalone checkout')
        require(_git(root, 'remote', 'get-url', 'origin').decode().strip() == REPOSITORIES[repo], 'Incorrect repository identity')
        require(_git(root, 'rev-parse', 'HEAD').decode().strip() == revisions[repo], 'Incorrect declared Git commit')
        require(_git(root, 'cat-file', '-t', revisions[repo]).strip() == b'commit', 'Missing commit object')
        require(not _git(root, '-c', 'core.fileMode=true', 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'), 'Dirty source checkout')
    selection = selected_files(project, source)
    proofs = {}
    for repo, sha in revisions.items():
        root = source / 'repos' / repo
        raw = _git(root, 'cat-file', 'commit', sha)
        top, _ = _commit(raw); objects = {}
        for item in selection:
            if item['repository'] != repo:
                continue
            path = root / item['path']
            require(all(not p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)), 'Symlink source path')
            require(stat.S_ISREG(path.stat().st_mode), 'Nonregular selected source')
            current = top
            for index, name in enumerate(item['path'].split('/')):
                if current not in objects:
                    objects[current] = _git(root, 'cat-file', 'tree', current)
                mode, oid = _tree(objects[current]).get(name, (None, None))
                if index == len(item['path'].split('/')) - 1:
                    require((mode, oid) == (item['git_mode'], item['git_blob']), 'Selected bytes/mode differ from Git tree')
                else:
                    require(mode == '40000', 'Unsupported symlink/gitlink path'); current = oid
        proofs[repo] = {'repository': repo, 'url': REPOSITORIES[repo], 'commit': sha,
                       'commit_raw': _encoded(raw), 'trees': {k: _encoded(v) for k, v in sorted(objects.items())}}
        verify_proof(proofs[repo], selection)
    return selection, proofs


def relationship(root, base, target):
    for oid in (base, target):
        require(_git(root, 'cat-file', '-t', oid).strip() == b'commit', 'Missing relationship commit')
    if base == target:
        return {'type': 'SAME_COMMIT', 'commits': {}}
    def ancestor(left, right):
        return subprocess.run(['git', '--no-replace-objects', '-C', str(root), 'merge-base', '--is-ancestor', left, right],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode
    forward = ancestor(base, target)
    if forward != 0:
        if forward != 1:
            raise SnapshotError('UNKNOWN_RELATIONSHIP: ancestry cannot be verified')
        if ancestor(target, base) == 0:
            raise SnapshotError('OLDER_HISTORICAL_COMMIT: rollback is unsupported')
        try:
            common = _git(root, 'merge-base', base, target).strip()
        except subprocess.CalledProcessError:
            common = b''
        raise SnapshotError('DIVERGENT_HISTORY: explicit re-anchoring required' if common else
                            'UNKNOWN_RELATIONSHIP: no verified common history')
    ids = _git(root, 'rev-list', '--ancestry-path', base + '..' + target).decode().splitlines()
    require(len(ids) <= 4096, 'Git ancestry proof exceeds bounded research limit')
    proof = {'type': 'FAST_FORWARD', 'commits': {oid: _encoded(_git(root, 'cat-file', 'commit', oid)) for oid in ids}}
    verify_relationship(proof, base, target)
    return proof


def verify_relationship(proof, base, target):
    if base == target:
        require(proof == {'type': 'SAME_COMMIT', 'commits': {}}, 'Invalid same-commit relationship'); return
    require(proof['type'] == 'FAST_FORWARD' and 0 < len(proof['commits']) <= 4096, 'Unsupported Git relationship')
    parents = {oid: _commit(_decoded(raw, 'commit', oid))[1] for oid, raw in proof['commits'].items()}
    pending = [target]; seen = set()
    while pending:
        oid = pending.pop()
        if oid == base:
            return
        if oid in seen:
            continue
        seen.add(oid); pending.extend(parents.get(oid, []))
    raise SnapshotError('Commit proof does not reach actual base')


def _policy(path, expected_sha):
    path = Path(path)
    require(not path.is_symlink() and file_digest(path) == expected_sha, 'External research policy checksum mismatch')
    value = read_json(path)
    require(value.get('schema_version') == VERSION and value.get('operation') == 'BUILD_CANDIDATE' and
            value.get('research_only') is True and value.get('experiment_id'), 'Invalid explicit research authorization')
    require(all(type(value.get(k)) is type(v) and value[k] == v for k, v in LIMITS.items()), 'Automatic approval/production is forbidden')
    require(value.get('provenance_type') in ('GENUINE_UPSTREAM', 'CONTROLLED_GIT_FIXTURE'), 'Invalid provenance classification')
    return value


def _publication(policy, proofs):
    if policy['provenance_type'] == 'CONTROLLED_GIT_FIXTURE':
        require(not policy.get('publications'), 'Synthetic state cannot claim publication'); return
    for repo, proof in proofs.items():
        entry = policy['publications'][repo][proof['commit']]
        raw = base64.b64decode(entry['response_base64'], validate=True)
        import json
        value = json.loads(raw)
        expected_url = 'https://api.github.com/repos/Xiangyu-Hu/' + repo + '/commits/' + proof['commit']
        require(entry['api_url'] == expected_url and hashlib.sha256(raw).hexdigest() == entry['response_sha256'] and
                value['sha'] == proof['commit'] and value['html_url'] == 'https://github.com/Xiangyu-Hu/' + repo + '/commit/' + proof['commit'],
                'Unverified upstream publication receipt')
        tree, parents = _commit(_decoded(proof['commit_raw'], 'commit', proof['commit']))
        require(value['commit']['tree']['sha'] == tree and [p['sha'] for p in value['parents']] == parents,
                'Published commit/Git object mismatch')


def _seal(root):
    write_json(root / 'checksums.json', {p.relative_to(root).as_posix(): file_digest(p)
        for p in sorted(root.rglob('*')) if p.is_file() and p != root / 'checksums.json'})


def _payload(root):
    checks = read_json(checked_file(root, 'checksums.json'))
    require(set(checks) | {'checksums.json'} == {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}, 'Package member mismatch')
    for n, sha in checks.items():
        require(file_digest(checked_file(root, n)) == sha, 'Payload checksum mismatch: ' + n)


def _receipt(node_id, kind, identity, payload):
    return {'node_id': node_id, 'kind': kind, 'identity': identity,
            'package_checksum': (legacy_package_checksum(node_id, digest(payload)) if kind == 'P2_ANCHOR'
                                 else digest({'node_id': node_id, 'payload': payload}))}


def _node(receipt):
    kind, identity = receipt['kind'], receipt['identity']
    require(kind in ('P2_ANCHOR', 'CANDIDATE', 'RESEARCH_BASELINE'), 'Invalid lineage kind')
    if kind == 'P2_ANCHOR':
        require(identity['generation'] == 0 and identity['parent'] is None and
                receipt['node_id'] == snapshot_id(identity['P2_identity']) and
                identity['upstreams'] == identity['P2_identity']['upstreams'] and
                identity['build_fingerprint'] == identity['P2_identity']['build_fingerprint'], 'Invalid P2 anchor receipt')
        payload = identity['P2_identity']
    else:
        require(type(identity['schema_version']) is int and identity['schema_version'] == VERSION and
                receipt['node_id'] == 'sha256-' + digest(identity), 'Invalid lineage schema/identity')
        payload = identity['artifacts']
    require(receipt['package_checksum'] == _receipt(receipt['node_id'], kind, identity, payload)['package_checksum'], 'Invalid parent package checksum')
    return identity


def _edge(receipt):
    return {k: receipt[k] for k in ('node_id', 'kind', 'package_checksum')}


def verify_lineage(receipts, fingerprint):
    require(bool(receipts), 'Missing lineage')
    seen = set(); prior = None
    for number, receipt in enumerate(receipts):
        node = _node(receipt)
        require(receipt['node_id'] not in seen and type(node['generation']) is int and node['generation'] == number,
                'Cyclic/invalid lineage generation')
        seen.add(receipt['node_id'])
        require(node['build_fingerprint'] == fingerprint and node['repositories'] == REPOSITORIES, 'Incompatible lineage build/repository identity')
        if prior:
            require(node['parent'] == _edge(receipts[number - 1]) and node['base_upstreams'] == prior['upstreams'] and
                    node['base_source_state'] == prior['target_source_state'], 'Incorrect immediate parent/source provenance')
            for repo in REPOSITORIES:
                verify_relationship(node['relationships'][repo], prior['upstreams'][repo], node['upstreams'][repo])
            require(node['provenance_type'] == prior['provenance_type'], 'Silent genuine/fixture lineage conversion')
        prior = node
    return prior


def _new_output(output, protected):
    output = Path(output)
    require(not output.exists() and not output.is_symlink() and output.parent.is_dir(), 'Output must be new with an existing parent')
    for p in protected:
        require(not output.resolve().is_relative_to(Path(p).resolve()) and not Path(p).resolve().is_relative_to(output.resolve()), 'Output overlaps protected input')
    return output


def _parent(project, parent, fingerprint, policy, base_source):
    parent = Path(parent)
    if (parent / 'manifest.json').is_file():
        require(policy.get('p2_anchor_authorized') is True, 'P2 requires explicit bootstrap authorization')
        report = read_parent(project, parent, fingerprint, baseline_format='p2')
        core = read_json(parent / 'manifest.json')['identity']
        sel, proofs = verify_sources(project, base_source, core['upstreams'])
        require(sel == read_json(parent / 'selection.json'), 'P2 selected bytes do not match declared Git commits')
        _publication(policy, proofs)
        node = {'generation': 0, 'parent': None, 'P2_identity': core, 'build_fingerprint': fingerprint,
                'repositories': REPOSITORIES, 'upstreams': core['upstreams'], 'target_source_state': state(sel),
                'provenance_type': policy['provenance_type']}
        receipt = _receipt(report['baseline_id'], 'P2_ANCHOR', node, core)
        return receipt, [receipt], sel, proofs
    report = verify(project, parent, fingerprint)
    require(report['provenance_type'] == policy['provenance_type'], 'Cannot relabel experimental parent as genuine')
    metadata = read_json(parent / 'upstream-candidate.json')
    receipt = report['receipt']
    if receipt['kind'] == 'CANDIDATE':
        require(policy.get('controlled_chain_fixture') == {'candidate_id': receipt['node_id'],
                'package_checksum': receipt['package_checksum'], 'source_state': report['target_source_state'],
                'parent': metadata['identity']['parent']}, 'Unapproved candidate requires an externally frozen controlled chain fixture')
    else:
        require(report['P4_1_verification'] == 'P4_1_VERIFIED', 'Parent requires P4.1 verification')
    sel, proofs = verify_sources(project, base_source, report['upstreams'])
    require(sel == read_json(parent / 'selection.json'), 'Incorrect immediate-parent source state')
    return receipt, report['lineage'], sel, proofs


def build(project, parent, base_source, target_source, output, *, policy_path, expected_policy_sha256, expected_fingerprint):
    """Candidate only. Explicit schema-2 gate, independent of the old P5A profile."""
    project, parent, base_source, target_source = map(Path, (project, parent, base_source, target_source))
    output = _new_output(output, (project, parent, base_source, target_source, policy_path))
    require(not Path(policy_path).resolve().is_relative_to(output.resolve()), 'Policy must be external')
    policy = _policy(policy_path, expected_policy_sha256)
    frozen_runtime(project)
    parent_seal = file_digest(parent / 'checksums.json')
    require(digest(engine.current_contract(project)) == expected_fingerprint, 'Incompatible build fingerprint')
    orchestration = contract(project)
    receipt, lineage, old_selection, base_proofs = _parent(project, parent, expected_fingerprint, policy, base_source)
    scope = policy['scope']; old = _node(receipt)
    require(scope['parent'] == _edge(receipt) and scope['base_source_state'] == state(old_selection) and
            scope['base_upstreams'] == old['upstreams'] and scope['build_fingerprint'] == expected_fingerprint and
            scope['orchestration_fingerprint'] == digest(orchestration), 'Invalid parent/base/build authorization')
    selection, proofs = verify_sources(project, target_source, scope['target_upstreams'])
    require(scope['target_source_state'] == state(selection), 'Invalid target source-state identity')
    _publication(policy, base_proofs); _publication(policy, proofs)
    relationships = {r: relationship(target_source / 'repos' / r, old['upstreams'][r], scope['target_upstreams'][r]) for r in REPOSITORIES}
    changes = engine.classify_files(old_selection, selection); status = {engine.key(c): c['change'] for c in changes}
    changed = [s for s in selection if status[engine.key(s)] != 'UNCHANGED']
    old_inventory = {engine.key(s): s for s in read_json(parent / 'inventory.json')}
    old_graph = {engine.key(s): e for s, e in zip(old_selection, read_json(parent / 'extraction-graph.json'))}
    old_components = {c: engine._records(parent / 'components' / (c + '.jsonl')) for c in engine.COMPONENTS}
    with tempfile.TemporaryDirectory(prefix='.p5a1-', dir=output.parent) as tmp:
        work = Path(tmp); stage = work / 'candidate'; stage.mkdir()
        # The existing extractor expects work/candidate/input; no new extractor.
        for r in REPOSITORIES:
            (stage / 'input/repos' / r).mkdir(parents=True)
        for s in selection:
            rel = Path('repos') / s['repository'] / s['path']
            engine._copy_source(target_source / rel, stage / 'input' / rel, s)
        emitted, evidence, events, warnings = engine._extract_changed(work, changed)
        components = {c: [] for c in engine.COMPONENTS}; inventory = []; graph = []
        for s in selection:
            k = engine.key(s); component = s['component']; reused = status[k] == 'UNCHANGED'
            if reused:
                previous = old_inventory[k]
                records = old_components[component][previous['record_start']:previous['record_start'] + previous['chunk_count']]
                require(digest(records) == previous['record_digest'], 'Reused records changed')
            else:
                records = emitted[k]
            inventory.append({**s, 'status': 'SUCCESS_WITH_CHUNKS' if records else 'SUCCESS_ZERO_CHUNKS',
                'error': None, 'record_start': len(components[component]), 'chunk_count': len(records),
                'chunk_ids': [r['chunk_id'] for r in records], 'record_digest': digest(records)})
            components[component].extend(records); graph.append((old_graph if reused else evidence)[k])
        (stage / 'components').mkdir()
        for c, records in components.items():
            engine._write_lines(stage / 'components' / (c + '.jsonl'), records)
        engine._write_lines(stage / 'corpus.jsonl', sum((components[c] for c in engine.COMPONENTS), []))
        counts = Counter(c['change'] for c in changes)
        summary = {'selected_files': len(selection), **{k.lower() + '_files': counts[k] for k in ('ADDED','MODIFIED','DELETED','UNCHANGED')},
            'reused_files': counts['UNCHANGED'], 'actual_extraction_invocations': sum(e['file_processing_entry'] for e in events),
            'reused_chunks': sum(i['chunk_count'] for i in inventory if status[engine.key(i)] == 'UNCHANGED'),
            'reused_zero_chunk_files': sum(i['chunk_count'] == 0 and status[engine.key(i)] == 'UNCHANGED' for i in inventory)}
        values = {'base-selection.json': old_selection, 'selection.json': selection, 'inventory.json': inventory, 'extraction-graph.json': graph,
                  'build-contract.json': engine.current_contract(project), 'orchestration-contract.json': orchestration,
                  'reuse-summary.json': summary, 'change-manifest.json': changes, 'extraction-trace.json': events,
                  'git-proofs.json': {'base': base_proofs, 'target': proofs}, 'research-policy.json': policy}
        for n, value in values.items(): write_json(stage / n, value)
        shutil.copyfile(policy_path, stage / 'research-policy-original.json')
        deterministic = (*PARITY_FILES, 'change-manifest.json', 'reuse-summary.json', 'extraction-trace.json',
                         'git-proofs.json', 'base-selection.json', 'orchestration-contract.json', 'research-policy-original.json')
        identity = {'schema_version': VERSION, 'generation': old['generation'] + 1, 'parent': _edge(receipt),
            'base_snapshot_id': receipt['node_id'], 'base_upstreams': old['upstreams'], 'upstreams': scope['target_upstreams'],
            'base_source_state': state(old_selection), 'target_source_state': state(selection),
            'target_fixture_id': state(selection), 'repositories': REPOSITORIES,
            'relationships': relationships, 'build_fingerprint': expected_fingerprint,
            'orchestration_fingerprint': digest(orchestration), 'provenance_type': policy['provenance_type'],
            'source_selection_contract': {n: file_digest(project / n) for n in ('configs/indexing_scope.json', 'configs/documentation_scope.json', 'src/indexing_scope.py')},
            'policy_sha256': expected_policy_sha256,
            'artifacts': {n: file_digest(stage / n) for n in deterministic}, **LIMITS}
        identifier = 'sha256-' + digest(identity)
        write_json(stage / 'upstream-candidate.json', {'kind': 'upstream-research-candidate', 'schema_version': VERSION,
                    'candidate_id': identifier, 'identity': identity, 'ancestry': lineage})
        # Compatibility projection for unchanged P4.1 evaluator only. Explicitly
        # schema 2, so no schema-1 reader can silently accept/convert this package.
        write_json(stage / 'candidate.json', {'kind': 'upstream-research-candidate', 'schema_version': VERSION,
                    'candidate_id': identifier, 'identity': identity})
        engine._verify_materialized(project, stage, selection, inventory, graph)
        require(verify_sources(project, target_source, scope['target_upstreams']) == (selection, proofs), 'Target changed during build')
        require(verify_sources(project, base_source, old['upstreams']) == (old_selection, base_proofs), 'Parent sources changed during build')
        require(contract(project) == orchestration, 'Orchestration changed during build')
        require(file_digest(parent / 'checksums.json') == parent_seal, 'Parent changed during build')
        final_parent = _parent(project, parent, expected_fingerprint, policy, base_source)
        require(final_parent == (receipt, lineage, old_selection, base_proofs), 'Parent package changed during build')
        require(file_digest(policy_path) == expected_policy_sha256, 'Policy changed during build')
        write_json(stage / 'execution.json', {'base_source': str(base_source.resolve()), 'target_source': str(target_source.resolve()),
                    'generator_summaries': warnings, 'observational_only': True})
        _seal(stage); result = verify(project, stage, expected_fingerprint)
        output.mkdir(exist_ok=False); os.replace(stage, output)
    return {k: v for k, v in result.items() if k not in ('lineage', 'receipt')}


def verify(project, root, fingerprint, *, expected_id=None):
    project, root = Path(project), Path(root)
    require(digest(engine.current_contract(project)) == fingerprint, 'Incompatible actual builder')
    _payload(root)
    m = read_json(checked_file(root, 'upstream-candidate.json')); i = m['identity']
    require(type(m['schema_version']) is int and m['schema_version'] == VERSION and m['kind'] == 'upstream-research-candidate', 'Unsupported lineage schema')
    require(m['candidate_id'] == 'sha256-' + digest(i) and i['schema_version'] == VERSION, 'Invalid candidate identity')
    require(read_json(root / 'candidate.json') == {k: v for k, v in m.items() if k != 'ancestry'}, 'Invalid evaluator projection')
    require(i['build_fingerprint'] == fingerprint == digest(read_json(root / 'build-contract.json')), 'Incompatible build contract')
    require(i['orchestration_fingerprint'] == digest(contract(project)) == digest(read_json(root / 'orchestration-contract.json')), 'Incompatible versioned orchestration')
    require(i['source_selection_contract'] == {n: file_digest(project / n) for n in i['source_selection_contract']} and
            set(i['source_selection_contract']) == {'configs/indexing_scope.json', 'configs/documentation_scope.json', 'src/indexing_scope.py'}, 'Invalid source-selection contract')
    require(all(type(i.get(k)) is type(v) and i[k] == v for k, v in LIMITS.items()), 'Approval/production forbidden')
    for n, h in i['artifacts'].items(): require(file_digest(checked_file(root, n)) == h, 'Invalid deterministic artifact')
    require(set(i['artifacts']) == {*PARITY_FILES, 'change-manifest.json', 'reuse-summary.json', 'extraction-trace.json',
                                  'git-proofs.json', 'base-selection.json', 'orchestration-contract.json', 'research-policy-original.json'}, 'Incomplete deterministic identity')
    selection = read_json(root / 'selection.json')
    require(state(selection) == i['target_source_state'] == i['target_fixture_id'], 'Invalid target source identity')
    r = _receipt(m['candidate_id'], 'CANDIDATE', i, i['artifacts'])
    lineage = [*m['ancestry'], r]; verify_lineage(lineage, fingerprint)
    require(i['base_snapshot_id'] == i['parent']['node_id'], 'Parent alias mismatch')
    policy = _policy(root / 'research-policy-original.json', i['policy_sha256'])
    require(same(policy, read_json(root / 'research-policy.json')) and policy['provenance_type'] == i['provenance_type'], 'Policy classification mismatch')
    scope = policy['scope']
    require(scope == {'parent': i['parent'], 'base_source_state': i['base_source_state'],
            'target_source_state': i['target_source_state'], 'base_upstreams': i['base_upstreams'],
            'target_upstreams': i['upstreams'], 'build_fingerprint': fingerprint,
            'orchestration_fingerprint': i['orchestration_fingerprint']}, 'Frozen scope mismatch')
    proof = read_json(root / 'git-proofs.json')
    # Parent selected bytes are authenticated at construction and bound to its
    # trusted receipt. Retain the exact parent selection for offline tree checks.
    base_selection = read_json(root / 'base-selection.json') if (root / 'base-selection.json').exists() else None
    require(base_selection is not None and state(base_selection) == i['base_source_state'], 'Missing/incorrect base selection')
    for label, chosen, upstreams in [('base', base_selection, i['base_upstreams']), ('target', selection, i['upstreams'])]:
        require(set(proof[label]) == set(REPOSITORIES), 'Incomplete repository proof')
        for repo in REPOSITORIES:
            require(proof[label][repo]['repository'] == repo and verify_proof(proof[label][repo], chosen) == upstreams[repo], 'Incorrect declared Git provenance')
        _publication(policy, proof[label])
    structural = engine._verify_materialized(project, root, selection, read_json(root / 'inventory.json'), read_json(root / 'extraction-graph.json'))
    p41 = 'NOT_APPROVED'; node_id = m['candidate_id']
    if (root / 'upstream-baseline.json').exists():
        b = read_json(root / 'upstream-baseline.json'); bi = b['identity']
        require(type(b['schema_version']) is int and b['schema_version'] == VERSION and b['kind'] == 'upstream-research-baseline' and
                b['baseline_id'] == 'sha256-' + digest(bi), 'Invalid versioned baseline identity')
        require(bi['candidate_identity'] == i and bi['candidate_id'] == m['candidate_id'], 'Candidate/baseline mismatch')
        require({k: bi[k] for k in i if k != 'artifacts'} == {k: v for k, v in i.items() if k != 'artifacts'}, 'Baseline lineage differs')
        for n, h in bi['artifacts'].items(): require(file_digest(checked_file(root, n)) == h, 'Baseline approval payload mismatch')
        evidence = verify_bundle(project, root / 'approval-evidence', expected_manifest_sha=bi['evidence_manifest_sha256'])
        quality = read_json(root / 'quality.json'); authorization = read_json(root / 'authorization.json')
        _check_decisions(authorization, quality, m['candidate_id'], i['parent']['node_id'], file_digest(root / 'corpus.jsonl'), fingerprint)
        require(evidence['candidate_id'] == m['candidate_id'] and evidence['parent_baseline_id'] == i['parent']['node_id'] and
                evidence['source_state'] == i['target_source_state'] and evidence['candidate_corpus_sha256'] == file_digest(root / 'corpus.jsonl') and
                same(read_json(root / 'approval-evidence/quality.json'), quality) and
                same(read_json(root / 'approval-evidence/authorization.json'), authorization), 'P4.1 baseline evidence mismatch')
        p41 = evidence['status']; node_id = b['baseline_id']
        r = _receipt(node_id, 'RESEARCH_BASELINE', bi, bi['artifacts']); lineage = [*m['ancestry'], r]
        verify_lineage(lineage, fingerprint)
    if expected_id is not None: require(node_id == expected_id, 'Unexpected node identity')
    return {**structural, 'node_id': node_id, 'candidate_id': m['candidate_id'], 'generation': i['generation'],
            'upstreams': i['upstreams'], 'provenance_type': i['provenance_type'], 'target_source_state': i['target_source_state'],
            'build_compatibility': 'COMPATIBLE', 'P4_1_verification': p41, 'receipt': r, 'lineage': lineage,
            'quality': 'QUALITY_REVIEW_REQUIRED', **LIMITS}


def compare_full(project, candidate, full, fingerprint):
    c = verify(project, candidate, fingerprint); full = Path(full); candidate = Path(candidate)
    require(verify_snapshot(full, fingerprint)['build_compatibility'] == 'COMPATIBLE', 'Incompatible full reference')
    require(read_json(full / 'manifest.json')['identity']['upstreams'] == c['upstreams'], 'Full reference target revisions mismatch')
    comparisons = {n: {'sha256': file_digest(candidate / n), 'exact_bytes_equal': (candidate / n).read_bytes() == (full / n).read_bytes()} for n in PARITY_FILES}
    require(all(v['exact_bytes_equal'] for v in comparisons.values()), 'Four-artifact parity failed')
    return {'status': 'PARITY_PASS', 'candidate_id': c['candidate_id'], 'comparisons': comparisons, 'full_reference_independently_built': True}


def full_reference(project, source, output, revisions, fingerprint):
    frozen_runtime(project)
    selection, proofs = verify_sources(project, source, revisions)
    actual = engine.current_contract(project)
    require(digest(actual) == fingerprint, 'Incompatible full builder')
    result = build_full_snapshot(project, source, output, {'chunker_commit': actual['external_chunker_commit'],
            'project_head': _git(project, 'rev-parse', 'HEAD').decode().strip(), 'build_contract': actual,
            'upstreams': revisions, 'selection': selection}, identity_scheme='portable-v1')
    require(verify_sources(project, source, revisions) == (selection, proofs), 'Full sources changed')
    return result


def approve(project, candidate, parent, full, output, *, expected_fingerprint, quality, authorization,
            approval_policy, expected_policy_sha256, evidence=None):
    """Explicit operator approval only, with unchanged P4.1 evidence/replay."""
    candidate, parent, full = map(Path, (candidate, parent, full))
    output = _new_output(output, (project, candidate, parent, full, approval_policy))
    c = verify(project, candidate, expected_fingerprint); m = read_json(candidate / 'upstream-candidate.json'); i = m['identity']
    require(not (candidate / 'upstream-baseline.json').exists(), 'Baseline cannot be silently approved again')
    require(i['parent']['kind'] != 'CANDIDATE', 'Controlled fixture ancestry cannot become an approved baseline')
    require(all(r['kind'] != 'CANDIDATE' for r in m['ancestry']), 'Fixture lineage cannot receive research approval')
    if i['parent']['kind'] == 'P2_ANCHOR':
        p = read_parent(project, parent, expected_fingerprint, baseline_format='p2')
        require(p['baseline_id'] == i['parent']['node_id'] and read_json(parent / 'manifest.json')['identity'] == m['ancestry'][-1]['identity']['P2_identity'], 'Wrong actual P2 parent')
        p['package_checksum'] = i['parent']['package_checksum']
    else:
        pr = verify(project, parent, expected_fingerprint)
        require(_edge(pr['receipt']) == i['parent'] and pr['P4_1_verification'] == 'P4_1_VERIFIED', 'Wrong/unapproved actual parent')
        p = {'baseline_id': pr['node_id'], 'generation': pr['generation'], 'package_checksum': pr['receipt']['package_checksum'], 'P4_1_verification': pr['P4_1_verification']}
    _check_decisions(authorization, quality, c['candidate_id'], p['baseline_id'], file_digest(candidate / 'corpus.jsonl'), expected_fingerprint)
    parity = compare_full(project, candidate, full, expected_fingerprint)
    with tempfile.TemporaryDirectory(prefix='.p5a1-approval-', dir=output.parent) as tmp:
        stage = Path(tmp) / 'baseline'; shutil.copytree(candidate, stage)
        (stage / 'checksums.json').unlink()
        create_evidence(project, candidate, parent, full, stage / 'approval-evidence', quality, authorization,
                        approval_policy, expected_policy_sha256, parent_report=p, evidence=evidence)
        for n, v in [('quality.json', quality), ('authorization.json', authorization), ('parity.json', parity)]: write_json(stage / n, v)
        approval_files = {p.relative_to(stage).as_posix(): file_digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        identity = {**i, 'candidate_id': c['candidate_id'], 'candidate_identity': i,
                    'artifacts': approval_files, 'evidence_manifest_sha256': file_digest(stage / 'approval-evidence/checksums.json')}
        write_json(stage / 'upstream-baseline.json', {'kind': 'upstream-research-baseline', 'schema_version': VERSION,
                   'baseline_id': 'sha256-' + digest(identity), 'identity': identity})
        _seal(stage); result = verify(project, stage, expected_fingerprint)
        output.mkdir(exist_ok=False); os.replace(stage, output)
    return result
