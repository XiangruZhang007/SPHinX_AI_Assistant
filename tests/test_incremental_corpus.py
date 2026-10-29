"""Real existing extractors, unchanged production configs, isolated P3 fixtures."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import incremental_corpus as inc
from src.corpus_snapshot import (SnapshotError, digest, file_digest, read_json,
    write_json, seal_snapshot, verify_snapshot)
from src.corpus_snapshot_build import build_full_snapshot, selected_files

P = Path(__file__).resolve().parents[1]


def make_sources(root):
    specs = {'SPHinXsys/src/a.cpp': b'int a() { return 1; }\n',
             'SPHinXsys/src/b.cpp': b'int b() { return 2; }\n',
             'SPHinXsys/src/zero.h': b'// no functions\n',
             'SPHinXsim/sphinxsim/bindings/a.py': b'def f():\n    return 1\n',
             'SPHinXsim/sphinxsim/sph_simulation/a.cpp': b'struct Parameters { int value; };\n',
             'SPHinXsim/examples/a.json': b'{"value": 1}\n'}
    config = read_json(P / 'configs/documentation_scope.json')
    for repo, rules in config['repositories'].items():
        for name in rules['include_files']:
            specs[repo + '/' + name] = b'# Title\n\nFixture text.\n' if name.endswith('.md') else b'Title\n=====\n\nFixture text.\n'
    for name, raw in specs.items():
        path = root / 'repos' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def reseal_base(root):
    """Update transport hashes to exercise deeper P2 validation, not just SHA checks."""
    manifest = read_json(root / 'manifest.json')
    identity = manifest['identity']
    for name in ('inventory', 'extraction_graph', 'corpus'):
        desc = identity[name]
        desc['sha256'] = file_digest(root / desc['path'])
    for desc in identity['components'].values():
        desc['sha256'] = file_digest(root / desc['path'])
    (root / 'manifest.json').unlink()
    (root / 'checksums.json').unlink()
    seal_snapshot(root, identity, manifest['validation'])


def replace_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


class IncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.shared.cleanup)
        cls.shared_root = Path(cls.shared.name)
        cls.original = cls.shared_root / 'original'
        make_sources(cls.original)
        cls.contract = inc.current_contract(P)
        cls.fp = digest(cls.contract)
        cls.inputs = {'chunker_commit': cls.contract['external_chunker_commit'],
                      'project_head': 'isolated-unit-fixture', 'build_contract': cls.contract,
                      'upstreams': {'SPHinXsys': 'a' * 40, 'SPHinXsim': 'b' * 40},
                      'selection': selected_files(P, cls.original)}
        cls.base_original = cls.shared_root / 'base'
        build_full_snapshot(P, cls.original, cls.base_original, cls.inputs, identity_scheme='portable-v1')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'sources'
        shutil.copytree(self.original, self.source)
        self.base = self.root / 'base'
        shutil.copytree(self.base_original, self.base)
        self.output = self.root / 'candidate'

    def build(self, **kwargs):
        return inc.build_incremental(P, self.base, self.source, self.output,
                                     expected_fingerprint=kwargs.pop('expected_fingerprint', self.fp),
                                     research_only=kwargs.pop('research_only', True), **kwargs)

    def parity(self):
        reference = self.root / 'full'
        inputs = {**self.inputs, 'selection': selected_files(P, self.source)}
        build_full_snapshot(P, self.source, reference, inputs, identity_scheme='portable-v1')
        for name in ('corpus.jsonl', 'inventory.json', 'extraction-graph.json', 'selection.json'):
            self.assertEqual((self.output / name).read_bytes(), (reference / name).read_bytes(), name)
        return reference

    def test_no_change_reuses_zero_chunks_without_extraction(self):
        before = {p.relative_to(self.base).as_posix(): file_digest(p) for p in self.base.rglob('*') if p.is_file()}
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 0)
        self.assertEqual(result['summary']['reused_zero_chunk_files'], 1)
        self.assertEqual((self.output / 'corpus.jsonl').read_bytes(), (self.base / 'corpus.jsonl').read_bytes())
        self.assertEqual(inc.verify_candidate(P, self.output, self.fp)['structural_status'], 'STRUCTURALLY_VALID')
        self.assertEqual(before, {p.relative_to(self.base).as_posix(): file_digest(p) for p in self.base.rglob('*') if p.is_file()})
        self.parity()

    def test_earlier_file_gains_chunks_later_reused_with_new_offset(self):
        path = self.source / 'repos/SPHinXsys/src/a.cpp'
        path.write_bytes(path.read_bytes() + b'int added() { return 3; }\n')
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        old = read_json(self.base / 'inventory.json')[1]
        new = read_json(self.output / 'inventory.json')[1]
        self.assertEqual(new['chunk_ids'], old['chunk_ids'])
        self.assertEqual(new['record_digest'], old['record_digest'])
        self.assertEqual(new['record_start'], old['record_start'] + 1)
        self.parity()

    def test_earlier_file_loses_chunks_nonzero_to_zero(self):
        (self.source / 'repos/SPHinXsys/src/a.cpp').write_bytes(b'// now empty\n')
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        new = read_json(self.output / 'inventory.json')
        self.assertEqual(new[0]['status'], 'SUCCESS_ZERO_CHUNKS')
        self.assertEqual(new[1]['record_start'], 0)
        self.parity()

    def test_python_rebuild(self):
        (self.source / 'repos/SPHinXsim/sphinxsim/bindings/a.py').write_bytes(b'def f():\n    return 77\n')
        result = self.build()
        self.assertEqual(result['summary']['modified_files'], 1)
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        self.assertEqual(read_json(self.output / 'extraction-trace.json')[0]['operation'], 'source')
        self.parity()

    def test_json_rebuild(self):
        (self.source / 'repos/SPHinXsim/examples/a.json').write_bytes(b'{"value": 2}\n')
        self.build()
        self.assertEqual(read_json(self.output / 'extraction-trace.json')[0]['operation'], 'json')
        self.parity()

    def test_document_rebuild(self):
        (self.source / 'repos/SPHinXsim/docs/cli-usage.md').write_bytes(b'# CLI\n\nChanged.\n')
        self.build()
        self.assertEqual(read_json(self.output / 'extraction-trace.json')[0]['operation'], 'documentation')
        self.parity()

    def test_added_source(self):
        (self.source / 'repos/SPHinXsim/examples/new.py').write_bytes(b'def new():\n    return 42\n')
        result = self.build()
        self.assertEqual(result['summary']['added_files'], 1)
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        self.parity()

    def test_deleted_source_never_extracted(self):
        (self.source / 'repos/SPHinXsys/src/a.cpp').unlink()
        result = self.build()
        self.assertEqual(result['summary']['deleted_files'], 1)
        self.assertEqual(result['summary']['actual_extraction_invocations'], 0)
        self.parity()

    def test_rename_delete_add_regenerates_portable_ids(self):
        old = self.source / 'repos/SPHinXsys/src/a.cpp'
        old.rename(old.with_name('renamed.cpp'))
        result = self.build()
        self.assertEqual(result['summary']['added_files'], 1)
        self.assertEqual(result['summary']['deleted_files'], 1)
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        changes = read_json(self.output / 'change-manifest.json')['files']
        added = next(c for c in changes if c['change'] == 'ADDED')
        deleted = next(c for c in changes if c['change'] == 'DELETED')
        self.assertFalse(set(added['target_chunk_ids']) & set(deleted['previous_chunk_ids']))
        self.parity()

    def test_zero_to_nonzero(self):
        (self.source / 'repos/SPHinXsys/src/zero.h').write_bytes(b'int was_zero() { return 5; }\n')
        self.build()
        entry = next(i for i in read_json(self.output / 'inventory.json') if i['path'] == 'src/zero.h')
        self.assertGreater(entry['chunk_count'], 0)
        self.parity()

    def test_modified_zero_remains_successful(self):
        (self.source / 'repos/SPHinXsys/src/zero.h').write_bytes(b'// changed but still no functions\n')
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 1)
        self.assertEqual(result['summary']['regenerated_chunks'], 0)
        self.parity()

    def test_added_and_deleted_zero_files(self):
        (self.source / 'repos/SPHinXsys/src/zero.h').unlink()
        (self.source / 'repos/SPHinXsim/examples/empty.py').write_bytes(b'# no functions\n')
        self.build()
        changes = read_json(self.output / 'change-manifest.json')['files']
        self.assertEqual(next(c for c in changes if c['change'] == 'ADDED')['status'], 'SUCCESS_ZERO_CHUNKS')
        self.parity()

    def test_mixed_changes(self):
        (self.source / 'repos/SPHinXsys/src/a.cpp').unlink()
        (self.source / 'repos/SPHinXsys/src/new.cpp').write_bytes(b'int newone() { return 9; }\n')
        (self.source / 'repos/SPHinXsim/examples/a.json').write_bytes(b'{"value":3}\n')
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 2)
        self.parity()

    def test_missing_document_rejected(self):
        (self.source / 'repos/SPHinXsim/docs/cli-usage.md').unlink()
        with self.assertRaisesRegex(SnapshotError, 'mandatory document'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_renamed_document_rejected(self):
        old = self.source / 'repos/SPHinXsim/docs/cli-usage.md'
        old.rename(old.with_name('renamed.md'))
        with self.assertRaisesRegex(SnapshotError, 'mandatory document'):
            self.build()

    def test_outside_whitelist_is_not_indexed(self):
        (self.source / 'repos/SPHinXsim/docs/unapproved.md').write_bytes(b'# Not selected\n')
        result = self.build()
        self.assertEqual(result['summary']['actual_extraction_invocations'], 0)
        self.assertNotIn('unapproved.md', (self.output / 'inventory.json').read_text())
        self.parity()

    def test_metadata_change_classifies_modified(self):
        selected = selected_files(P, self.source)
        for field, value in [('source_role', 'different'), ('language', 'different'), ('git_mode', '100755')]:
            changed = copy.deepcopy(selected)
            changed[0][field] = value
            self.assertEqual(inc.classify_files(selected, changed)[0]['change'], 'MODIFIED')

    def test_duplicate_identity_and_bad_hash_rejected(self):
        selected = selected_files(P, self.source)
        for invalid in [selected + [selected[0]], [{**selected[0], 'source_sha256': None}]]:
            with self.assertRaises(SnapshotError):
                inc.classify_files(selected, invalid)

    def test_explicit_research_authorization_required(self):
        with self.assertRaisesRegex(SnapshotError, 'authorization'):
            self.build(research_only=False)
        self.assertFalse(self.output.exists())

    def test_missing_fingerprint_rejected(self):
        with self.assertRaisesRegex(SnapshotError, 'fingerprint'):
            self.build(expected_fingerprint=None)

    def test_wrong_expected_fingerprint_rejected(self):
        with self.assertRaisesRegex(SnapshotError, 'incompatible'):
            self.build(expected_fingerprint='0' * 64)

    def test_configuration_change_blocks_reuse(self):
        with patch.object(inc, 'current_contract', return_value={**self.contract, 'different_config': True}):
            with self.assertRaisesRegex(SnapshotError, 'incompatible'):
                self.build()

    def test_corrupted_base_payload_rejected(self):
        with (self.base / 'corpus.jsonl').open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaises(SnapshotError):
            self.build()

    def test_corrupted_native_parent_rejected_even_after_resealing(self):
        graph = read_json(self.base / 'extraction-graph.json')
        graph[0]['native'][0]['parent_chunk_id'] = 'missing'
        replace_json(self.base / 'extraction-graph.json', graph)
        reseal_base(self.base)
        with self.assertRaisesRegex(SnapshotError, 'parent'):
            self.build()

    def test_duplicate_base_inventory_rejected_even_after_resealing(self):
        items = read_json(self.base / 'inventory.json')
        items[1] = items[0]
        replace_json(self.base / 'inventory.json', items)
        reseal_base(self.base)
        with self.assertRaises(SnapshotError):
            self.build()

    def test_corrupted_offsets_rejected_even_after_resealing(self):
        items = read_json(self.base / 'inventory.json')
        items[1]['record_start'] += 1
        replace_json(self.base / 'inventory.json', items)
        reseal_base(self.base)
        with self.assertRaises(SnapshotError):
            self.build()

    def test_source_failure_does_not_publish_candidate(self):
        (self.source / 'repos/SPHinXsim/sphinxsim/bindings/a.py').write_bytes(b'\xff')
        with self.assertRaises(SnapshotError):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.p3-work-*')))

    def test_output_no_clobber(self):
        self.output.mkdir()
        (self.output / 'keep').write_text('preserved')
        with self.assertRaisesRegex(SnapshotError, 'overwrite'):
            self.build()
        self.assertEqual((self.output / 'keep').read_text(), 'preserved')

    def test_candidate_corruption_and_stale_records_rejected(self):
        self.build()
        with (self.output / 'corpus.jsonl').open('ab') as stream:
            stream.write(b'corrupt')
        with self.assertRaises(SnapshotError):
            inc.verify_candidate(P, self.output, self.fp)

    def test_independent_process_roots_and_candidate_identity(self):
        self.build()
        other = self.root / 'other-root'
        shutil.copytree(self.source, other)
        target = self.root / 'process-output'
        run = subprocess.run([sys.executable, '-B', str(P / 'scripts/build_incremental_corpus.py'), 'build',
            '--base-snapshot', str(self.base), '--source-root', str(other), '--output', str(target),
            '--expected-fingerprint', self.fp, '--research-only'], cwd=self.root,
            env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        for name in ('corpus.jsonl', 'inventory.json', 'extraction-graph.json', 'change-manifest.json', 'candidate.json'):
            self.assertEqual((self.output / name).read_bytes(), (target / name).read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
