"""Manual P3 research candidates; the immutable P2 extraction contract is reused.

Source generators run on a sparse, verified tree containing only changed files.
Documentation uses the existing single-document entry point. No extraction rules,
discovery globals, historical snapshots, or production gates are modified here.
Full-build comparisons are deliberately external to this incremental operation.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from . import chunk_generation_v1 as g
from . import documentation_chunking as doc
from .corpus_snapshot import (
    SnapshotError, _source_span, checked_file, digest, file_digest, read_json,
    read_records, source_bytes, validate_graph, verify_snapshot, write_json,
)
from .corpus_snapshot_build import FileCollector, build_contract, selected_files
from .indexing_scope import normalize_relative_path
from .indexing_scope import discover_files

P3_FILES = ('src/incremental_corpus.py', 'scripts/build_incremental_corpus.py',
            'tests/test_incremental_corpus.py')
COMPONENTS = ('sys', 'sim', 'documentation')
SELECTION_FIELDS = {'repository', 'path', 'component', 'git_mode', 'git_blob',
                    'source_sha256', 'source_bytes', 'source_role', 'language'}


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def key(item):
    if item['repository'] not in ('SPHinXsys', 'SPHinXsim'):
        raise SnapshotError('Unknown repository')
    if normalize_relative_path(item['path']) != item['path']:
        raise SnapshotError('Noncanonical file identity')
    return item['repository'], item['path']


def selection_map(selection):
    result = {}
    for item in selection:
        if not isinstance(item, dict) or not SELECTION_FIELDS <= item.keys():
            raise SnapshotError('Malformed source selection')
        identity = key(item)
        if identity in result:
            raise SnapshotError('Duplicate logical file identity')
        if item['component'] not in COMPONENTS or not _hash(item['source_sha256']):
            raise SnapshotError('Invalid source classification/hash')
        if type(item['source_bytes']) is not int or item['source_bytes'] < 0:
            raise SnapshotError('Invalid source length')
        if item['git_mode'] not in ('100644', '100755') or not item['source_role'] or not item['language']:
            raise SnapshotError('Invalid file-level metadata')
        result[identity] = item
    return result


def classify_files(previous, target):
    """Target order first, then deleted paths in their original base order."""
    old, new = selection_map(previous), selection_map(target)
    result = []
    for identity, item in new.items():
        before = old.get(identity)
        classification = 'ADDED' if before is None else (
            'UNCHANGED' if all(before[k] == item[k] for k in SELECTION_FIELDS) else 'MODIFIED')
        result.append({'repository': identity[0], 'path': identity[1], 'change': classification})
    result.extend({'repository': repo, 'path': path, 'change': 'DELETED'}
                  for repo, path in old if (repo, path) not in new)
    return result


def orchestration_contract(project):
    return {'version': 1, 'purpose': 'P3 research orchestration; separate from P2 compatibility',
            'files': {name: file_digest(Path(project) / name) for name in P3_FILES}}


def current_contract(project):
    """Never trust a caller-supplied external chunker revision as the actual one."""
    root = Path(project) / 'tools/treesitter-chunker'
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', *args], cwd=root,
                                       env=env, text=True).strip()
    revision = git('rev-parse', 'HEAD')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise SnapshotError('External chunker is not clean')
    return build_contract(project, revision)


def _records(path):
    # Empty components are valid in a research target; P2 snapshot format itself
    # remains unchanged and still applies its original nonempty-component rule.
    return read_records(path) if Path(path).stat().st_size else []


def _lines(records):
    return b''.join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()
                    for r in records)


def _write_lines(path, records):
    with Path(path).open('xb') as stream:
        stream.write(_lines(records))


class SelectionTiming:
    """Measure shared discovery inside selected_files without replacing it."""
    def __init__(self):
        self.starts = {}
        self.discovery_seconds = 0.0

    def observe(self, frame, event, arg):
        if frame.f_code is discover_files.__code__:
            if event == 'call':
                self.starts[id(frame)] = time.perf_counter()
            elif event == 'return':
                self.discovery_seconds += time.perf_counter() - self.starts.pop(id(frame))

    def __enter__(self):
        if sys.getprofile() is not None:
            raise SnapshotError('Selection timing requires an unoccupied process profiler')
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *args):
        sys.setprofile(None)


class ExtractionTrace:
    """Observe actual Python calls without replacing any generator functions.

    Top-level file processors count once per file. Supplement/recovery calls are
    recorded separately. Tree-sitter parser calls during hashing/verification do
    not count as extraction. This synchronous CLI does not run worker threads.
    """
    def __init__(self, root):
        self.root = Path(root)
        self.events = []
        self.functions = {
            g._raw_source_chunks.__code__: ('source', True),
            g._config_document_record.__code__: ('json', True),
            doc.chunk_document.__code__: ('documentation', True),
            g.recover_delegating_constructors.__code__: ('constructor_recovery', False),
            g.data_definitions.__code__: ('data_definitions', False),
        }

    def observe(self, frame, event, arg):
        if event != 'call' or frame.f_code not in self.functions:
            return
        kind, primary = self.functions[frame.f_code]
        local = frame.f_locals
        if kind == 'documentation':
            repo, relative = local['repository'], local['relative_path']
        else:
            path = Path(local['source_path'])
            parts = path.relative_to(self.root / 'repos').parts
            repo, relative = parts[0], Path(*parts[1:]).as_posix()
        self.events.append({'repository': repo, 'path': relative,
                            'operation': kind, 'file_processing_entry': primary})

    def __enter__(self):
        if sys.getprofile() is not None:
            raise SnapshotError('Extraction observation requires an unoccupied process profiler')
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *args):
        sys.setprofile(None)


def _copy_source(source, destination, item):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink() or not source.is_file():
        raise SnapshotError('Unsafe source copy')
    shutil.copy2(source, destination)
    if (file_digest(destination) != item['source_sha256'] or
            destination.stat().st_size != item['source_bytes'] or
            ('100755' if destination.stat().st_mode & 0o111 else '100644') != item['git_mode']):
        raise SnapshotError('Source changed while freezing target inputs')


def _extract_changed(work, changed):
    sparse = work / 'changed-root'
    for repo in ('SPHinXsys', 'SPHinXsim'):
        (sparse / 'repos' / repo).mkdir(parents=True)
    for item in changed:
        relative = Path('repos') / item['repository'] / item['path']
        _copy_source(work / 'candidate/input' / relative, sparse / relative, item)
    collector = FileCollector(sparse, changed)
    emitted = {}

    def observer(path, records, native, transformed, error):
        collector(path, records, native, transformed, error)
        if error:
            raise SnapshotError('Changed-file extraction failed: ' + error)
        emitted[key(changed[len(collector.inventory) - 1])] = records

    summaries = {}
    with ExtractionTrace(sparse) as trace:
        if any(i['component'] == 'sys' for i in changed):
            summaries['sys'] = g.generate(sparse, work / 'changed-sys.jsonl', work / 'changed-sys-summary.json',
                                         identity_scheme='portable-v1', file_observer=observer)
        if any(i['component'] == 'sim' for i in changed):
            summaries['sim'] = g.generate_sphinxsim(sparse, work / 'changed-sim.jsonl', work / 'changed-sim-summary.json',
                                                   identity_scheme='portable-v1', file_observer=observer)
        for item in changed:
            if item['component'] == 'documentation':
                path = sparse / 'repos' / item['repository'] / item['path']
                records, report = doc.chunk_document(path.read_bytes(), item['repository'], item['path'])
                doc.validate_records(records, {key(item): path.read_bytes()})
                observer(path, records, [], [], None)
                summaries.setdefault('documentation', []).append(report)
    if len(collector.inventory) != len(changed):
        raise SnapshotError('Changed source lacks a completed-file event')
    calls = [(e['repository'], e['path']) for e in trace.events if e['file_processing_entry']]
    if calls != [key(item) for item in changed]:
        raise SnapshotError('Actual extraction calls differ from changed-file selection')
    return emitted, {key(i): e for i, e in zip(changed, collector.graph)}, trace.events, summaries


def _descriptor(root, name):
    return {'path': name, 'sha256': file_digest(root / name)}


def _verify_materialized(project, root, selection, inventory, graph):
    actual_selection = selected_files(project, root / 'input')
    if actual_selection != selection:
        raise SnapshotError('Candidate discovery/classification/source hashes differ')
    selection_map(selection)
    if len(selection) != len(inventory) or len(selection) != len(graph):
        raise SnapshotError('Incomplete inventory/graph')
    components = {c: _records(root / 'components' / (c + '.jsonl')) for c in COMPONENTS}
    corpus = _records(root / 'corpus.jsonl')
    if corpus != sum((components[c] for c in COMPONENTS), []):
        raise SnapshotError('Invalid record ordering or component inclusion')
    if _lines(corpus) != (root / 'corpus.jsonl').read_bytes():
        raise SnapshotError('Noncanonical ordered corpus serialization')
    cursors = dict.fromkeys(COMPONENTS, 0)
    references = Counter()
    for source, item, evidence in zip(selection, inventory, graph):
        if {k: item[k] for k in SELECTION_FIELDS} != source:
            raise SnapshotError('Inventory source classification mismatch')
        component = source['component']
        count, start = item['chunk_count'], cursors[component]
        if type(count) is not int or count < 0 or item['record_start'] != start:
            raise SnapshotError('Invalid inventory offsets/count')
        emitted = components[component][start:start + count]
        if len(emitted) != count or digest(emitted) != item['record_digest'] or item['chunk_ids'] != [r['chunk_id'] for r in emitted]:
            raise SnapshotError('Inventory-to-record mapping mismatch')
        if item['error'] is not None or item['status'] != ('SUCCESS_WITH_CHUNKS' if count else 'SUCCESS_ZERO_CHUNKS'):
            raise SnapshotError('Failed/skipped source in candidate')
        if key(evidence) != key(source):
            raise SnapshotError('Extraction graph/source mismatch')
        data = source_bytes((root / 'input/repos' / source['repository'] / source['path']).read_bytes(),
                            component == 'documentation')
        for record in emitted:
            if record['source_path'] != 'repos/' + source['repository'] + '/' + source['path'] or record['source_role'] != source['source_role']:
                raise SnapshotError('Stale/cross-file source provenance')
            _source_span(record, data)
        references.update(validate_graph(evidence, emitted, data))
        cursors[component] += count
    if any(cursors[c] != len(components[c]) for c in COMPONENTS):
        raise SnapshotError('Uninventoried records')
    return {'structural_status': 'STRUCTURALLY_VALID', 'records': len(corpus),
            'selected_files': len(selection), 'zero_chunk_files': sum(i['chunk_count'] == 0 for i in inventory),
            'reference_checks': dict(references)}


def verify_candidate(project, root, expected_fingerprint):
    """Read-only verification of a research candidate, never a promotion gate."""
    root = Path(root)
    if not _hash(expected_fingerprint):
        raise SnapshotError('Missing/invalid independently supplied build fingerprint')
    if digest(current_contract(project)) != expected_fingerprint:
        raise SnapshotError('Actual builder incompatible with independently expected fingerprint')
    manifest = read_json(checked_file(root, 'candidate.json'))
    if manifest['kind'] != 'p3-research-candidate' or manifest['schema_version'] != 1:
        raise SnapshotError('Unsupported research candidate contract')
    identity = manifest['identity']
    if identity['build_fingerprint'] != expected_fingerprint or manifest['candidate_id'] != 'sha256-' + digest(identity):
        raise SnapshotError('Candidate identity/fingerprint mismatch')
    checks = read_json(checked_file(root, 'checksums.json'))
    if set(checks) != {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} - {'checksums.json'}:
        raise SnapshotError('Candidate payload inventory mismatch')
    for relative, h in checks.items():
        if file_digest(checked_file(root, relative)) != h:
            raise SnapshotError('Candidate payload checksum mismatch: ' + relative)
    for descriptor in identity['artifacts'].values():
        if file_digest(checked_file(root, descriptor['path'])) != descriptor['sha256']:
            raise SnapshotError('Candidate descriptor mismatch')
    selection = read_json(root / 'selection.json')
    if identity['target_fixture_id'] != 'sha256-' + digest(selection):
        raise SnapshotError('Target fixture identity mismatch')
    report = _verify_materialized(project, root, selection, read_json(root / 'inventory.json'),
                                  read_json(root / 'extraction-graph.json'))
    report.update({'build_compatibility': 'COMPATIBLE', 'candidate_id': manifest['candidate_id'],
                   'retrieval_quality_status': 'QUALITY_REVIEW_REQUIRED', 'automatic_reuse': False,
                   'production_promotion': 'NOT_AUTHORIZED', 'incremental_parity': 'NOT_ASSESSED'})
    return report


def build_incremental(project, base, source_root, output, *, expected_fingerprint,
                      research_only=False, expected_snapshot_id=None):
    """Build one manual candidate. No internal full rebuild and no cache repair."""
    project, base, source_root, output = map(Path, (project, base, source_root, output))
    if research_only is not True:
        raise SnapshotError('Explicit research-only authorization required')
    if not _hash(expected_fingerprint):
        raise SnapshotError('Missing/invalid independently supplied build fingerprint')
    if output.exists() or output.is_symlink():
        raise SnapshotError('Output already exists; refusing overwrite')
    # Output must not create inputs during discovery or overwrite protected trees.
    for protected in (base.resolve(), source_root.resolve(), project.resolve() / 'repos',
                      project.resolve() / 'tools', project.resolve() / 'data'):
        if output.resolve().is_relative_to(protected) or protected.is_relative_to(output.resolve()):
            raise SnapshotError('Output overlaps protected source/snapshot tree')
    if not output.parent.is_dir():
        raise SnapshotError('Output parent must already exist')
    started = time.perf_counter()
    timings = {}
    @contextlib.contextmanager
    def timed(name):
        before = time.perf_counter()
        try:
            yield
        finally:
            timings[name] = time.perf_counter() - before

    with timed('snapshot_loading_validation_and_compatibility'):
        actual_contract = current_contract(project)
        if digest(actual_contract) != expected_fingerprint:
            raise SnapshotError('Actual builder incompatible with independently expected fingerprint')
        base_report = verify_snapshot(base, expected_fingerprint)
        if base_report['build_compatibility'] != 'COMPATIBLE':
            raise SnapshotError('Base snapshot is not COMPATIBLE')
        if expected_snapshot_id is not None and base_report['snapshot_id'] != expected_snapshot_id:
            raise SnapshotError('Unexpected base snapshot identity')
        old_selection = read_json(base / 'selection.json')
        old_inventory = read_json(base / 'inventory.json')
        old_graph = read_json(base / 'extraction-graph.json')
        old_components = {c: _records(base / 'components' / (c + '.jsonl')) for c in COMPONENTS}
        selection_map(old_selection)
        old_index = {key(i): i for i in old_inventory}
        old_evidence = {key(i): e for i, e in zip(old_inventory, old_graph)}
        p3_contract = orchestration_contract(project)
    with timed('discovery_hashing_and_comparison'):
        with SelectionTiming() as selection_timing:
            selection = selected_files(project, source_root)
        changes = classify_files(old_selection, selection)
        change_map = {key(c): c['change'] for c in changes}
        changed = [i for i in selection if change_map[key(i)] != 'UNCHANGED']
    timings['source_discovery'] = selection_timing.discovery_seconds
    timings['file_hashing_metadata_and_comparison'] = (
        timings['discovery_hashing_and_comparison'] - selection_timing.discovery_seconds)
    # Work and candidate stay private until all structural checks succeed. Failed
    # outputs never appear at the requested destination or overwrite old results.
    with tempfile.TemporaryDirectory(prefix='.p3-work-', dir=output.parent) as temporary:
        work = Path(temporary)
        target = work / 'candidate'
        target.mkdir()
        with timed('freeze_target_sources'):
            for repo in ('SPHinXsys', 'SPHinXsim'):
                (target / 'input/repos' / repo).mkdir(parents=True)
            for item in selection:
                rel = Path('repos') / item['repository'] / item['path']
                _copy_source(source_root / rel, target / 'input' / rel, item)
        with timed('changed_file_extraction'):
            emitted, evidence, events, warnings = _extract_changed(work, changed)
        components = {c: [] for c in COMPONENTS}
        inventory, graph, file_records = [], [], {}
        with timed('corpus_assembly'):
            for source in selection:
                identity = key(source)
                component = source['component']
                if change_map[identity] == 'UNCHANGED':
                    old = old_index[identity]
                    records = old_components[component][old['record_start']:old['record_start'] + old['chunk_count']]
                    if digest(records) != old['record_digest']:
                        raise SnapshotError('Reused record digest changed')
                else:
                    records = emitted[identity]
                file_records[identity] = records
                components[component].extend(records)
        with timed('inventory_reconstruction'):
            cursors = dict.fromkeys(COMPONENTS, 0)
            for source in selection:
                component = source['component']
                records = file_records[key(source)]
                inventory.append({**source, 'status': 'SUCCESS_WITH_CHUNKS' if records else 'SUCCESS_ZERO_CHUNKS',
                                  'error': None, 'record_start': cursors[component],
                                  'chunk_count': len(records), 'chunk_ids': [r['chunk_id'] for r in records],
                                  'record_digest': digest(records)})
                cursors[component] += len(records)
        with timed('extraction_graph_reconstruction_or_reuse'):
            graph = [(old_evidence if change_map[key(source)] == 'UNCHANGED' else evidence)[key(source)]
                     for source in selection]
        with timed('change_manifest_and_statistics'):
            index = {key(i): i for i in inventory}
            for change in changes:
                identity = key(change)
                previous, new = old_index.get(identity), index.get(identity)
                change.update({'previous_sha256': previous['source_sha256'] if previous else None,
                               'target_sha256': new['source_sha256'] if new else None,
                               'previous_chunk_count': previous['chunk_count'] if previous else 0,
                               'target_chunk_count': new['chunk_count'] if new else 0,
                               'previous_chunk_ids': previous['chunk_ids'] if previous else [],
                               'target_chunk_ids': new['chunk_ids'] if new else [],
                               'processing': 'REUSED' if change['change'] == 'UNCHANGED' else (
                                   'REMOVED' if new is None else 'REGENERATED'),
                               'status': new['status'] if new else 'DELETED'})
            categories = Counter(c['change'] for c in changes)
            summary = {'selected_files': len(selection),
                       **{name.lower() + '_files': categories[name] for name in ('UNCHANGED', 'MODIFIED', 'ADDED', 'DELETED')},
                       'reused_files': categories['UNCHANGED'], 'rebuilt_files': len(changed),
                       'reused_chunks': sum(c['target_chunk_count'] for c in changes if c['change'] == 'UNCHANGED'),
                       'regenerated_chunks': sum(c['target_chunk_count'] for c in changes if c['processing'] == 'REGENERATED'),
                       'removed_chunks': sum(c['previous_chunk_count'] for c in changes if c['change'] in ('DELETED', 'MODIFIED')),
                       'reused_zero_chunk_files': sum(c['change'] == 'UNCHANGED' and c['target_chunk_count'] == 0 for c in changes),
                       'actual_extraction_invocations': sum(e['file_processing_entry'] for e in events),
                       'processing_failures': 0,
                       'rename_policy': 'delete old path plus add/reprocess new path; no rename inference'}
        with timed('output_serialization'):
            (target / 'components').mkdir()
            for component, records in components.items():
                _write_lines(target / 'components' / (component + '.jsonl'), records)
            _write_lines(target / 'corpus.jsonl', sum((components[c] for c in COMPONENTS), []))
            for name, value in [('selection.json', selection), ('inventory.json', inventory),
                                ('extraction-graph.json', graph), ('change-manifest.json', {'schema_version': 1, 'files': changes}),
                                ('reuse-summary.json', summary), ('extraction-trace.json', events),
                                ('build-contract.json', actual_contract), ('p3-orchestration-contract.json', p3_contract),
                                ('base-verification.json', base_report),
                                ('provenance.json', {'base_snapshot_id': base_report['snapshot_id'],
                                                     'base_upstream_revisions': read_json(base / 'manifest.json')['identity']['upstreams'],
                                                     'target_fixture_id': 'sha256-' + digest(selection),
                                                     'target_is_derived_fixture_not_upstream_commit': True,
                                                     'selection_sha256': digest(selection)})]:
                write_json(target / name, value)
        with timed('final_integrity_validation'):
            validation = _verify_materialized(project, target, selection, inventory, graph)
            if selected_files(project, source_root) != selection:
                raise SnapshotError('Target source state changed during incremental build')
            if digest(current_contract(project)) != expected_fingerprint or orchestration_contract(project) != p3_contract:
                raise SnapshotError('Implementation/configuration changed during build')
            # Protect against concurrent changes to the base while it is consumed.
            if verify_snapshot(base, expected_fingerprint) != base_report:
                raise SnapshotError('Base snapshot changed during incremental build')
        validation.update({'build_compatibility': 'COMPATIBLE', 'retrieval_quality_status': 'QUALITY_REVIEW_REQUIRED',
                           'automatic_reuse': False, 'production_promotion': 'NOT_AUTHORIZED',
                           'incremental_parity': 'NOT_ASSESSED', 'parity_is_external_acceptance_step': True})
        write_json(target / 'validation.json', validation)
        timings['preseal_elapsed_seconds'] = time.perf_counter() - started
        write_json(target / 'execution.json', {'source_root': str(source_root.resolve()), 'output': str(output.resolve()),
                   'base': str(base.resolve()), 'timings_seconds': timings, 'hash_seed': os.environ.get('PYTHONHASHSEED'),
                   'changed_file_generator_summaries': warnings,
                   'timing_groups': 'discovery_hashing_and_comparison is the parent of source_discovery and file_hashing_metadata_and_comparison; finalization excluded from preseal elapsed; returned total includes publication'})
        deterministic = ('corpus.jsonl', 'selection.json', 'inventory.json', 'extraction-graph.json',
                         'change-manifest.json', 'reuse-summary.json', 'extraction-trace.json', 'provenance.json')
        identity = {'base_snapshot_id': base_report['snapshot_id'], 'build_fingerprint': expected_fingerprint,
                    'orchestration_fingerprint': digest(p3_contract), 'target_fixture_id': 'sha256-' + digest(selection),
                    'artifacts': {name: _descriptor(target, name) for name in deterministic}}
        manifest = {'kind': 'p3-research-candidate', 'schema_version': 1,
                    'candidate_id': 'sha256-' + digest(identity), 'identity': identity}
        write_json(target / 'candidate.json', manifest)
        write_json(target / 'checksums.json', {p.relative_to(target).as_posix(): file_digest(p)
                                              for p in sorted(target.rglob('*')) if p.is_file()})
        # mkdir is the no-clobber claim. Replace only the empty directory owned by
        # this invocation; never replace an existing user result, even if empty.
        output.mkdir(exist_ok=False)
        try:
            os.replace(target, output)
        except BaseException:
            output.rmdir()
            raise
    return {**validation, 'candidate_id': manifest['candidate_id'], 'summary': summary,
            'total_elapsed_seconds': time.perf_counter() - started,
            'timings_seconds': timings}
