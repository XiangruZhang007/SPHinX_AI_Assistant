"""Recovery tests use explicitly controlled Git fixtures; no network writes."""
import copy
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from src import trusted_snapshot as t, ci_v2_research as c, upstream_provenance as u
from src.corpus_snapshot import digest, file_digest, read_json, write_json, package_snapshot
from src.corpus_snapshot_build import build_full_snapshot
from src.research_baseline import read_parent
from src.research_quality import review_not_evaluated
from test_incremental_corpus import make_sources
from test_upstream_provenance import git, commit, policy_for

P = Path(__file__).resolve().parents[1]


def pack(root, archive):
    with tarfile.open(archive, 'x:gz') as tar:
        for path in sorted(root.rglob('*')):
            if path.is_file(): tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)


class TrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.tmp.cleanup)
        r = cls.shared = Path(cls.tmp.name); cls.source = r / 'source'; make_sources(cls.source)
        cls.revs = {}
        for repo, url in u.REPOSITORIES.items():
            root = cls.source / 'repos' / repo
            git(root, 'init'); git(root, 'remote', 'add', 'origin', url)
            cls.revs[repo] = commit(root)
        lock = c.read(c.LOCK); cls.fp = digest(lock['P2'])
        selection, _ = u.verify_sources(P, cls.source, cls.revs)
        build_full_snapshot(P, cls.source, r / 'base', {'chunker_commit': lock['external_chunker_commit'],
            'project_head': 'CONTROLLED_LOCAL_FIXTURE', 'build_contract': lock['P2'],
            'upstreams': cls.revs, 'selection': selection}, identity_scheme='portable-v1')
        package_snapshot(r / 'base', r / 'p2.tar.gz', cls.fp)
        policy = policy_for(r / 'base', cls.source, cls.source, cls.revs, cls.fp)
        write_json(r / 'build-policy.json', policy)
        u.build(P, r / 'base', cls.source, cls.source, r / 'candidate', policy_path=r / 'build-policy.json',
            expected_policy_sha256=file_digest(r / 'build-policy.json'), expected_fingerprint=cls.fp)
        u.full_reference(P, cls.source, r / 'full', cls.revs, cls.fp)
        candidate = read_json(r / 'candidate/upstream-candidate.json'); identity = candidate['identity']
        quality = review_not_evaluated(r / 'candidate', cls.fp, lock['quality_policy'])
        authorization = {'schema_version': 1, 'research_only': True, 'approved': True,
            'candidate_id': candidate['candidate_id'], 'parent_baseline_id': identity['parent']['node_id'],
            'experiment_id': 'EXPLICIT_SYNTHETIC_APPROVAL_FIXTURE', 'quality_status': 'QUALITY_REVIEW_REQUIRED', 'allow_quality_review': True}
        ap = {'schema_version': 1, 'scope': {'experiment_id': authorization['experiment_id'],
            'parent_baseline_id': authorization['parent_baseline_id'], 'target_source_state': identity['target_source_state'],
            'build_fingerprint': cls.fp, 'quality_policy_sha256': digest(quality['policy'])},
            'mode': 'STRUCTURAL_ONLY', 'quality_status': 'QUALITY_REVIEW_REQUIRED',
            'automatic_reuse': False, 'production_promotion': 'NOT_AUTHORIZED'}
        write_json(r / 'approval-policy.json', ap)
        u.approve(P, r / 'candidate', r / 'base', r / 'full', r / 'approved', expected_fingerprint=cls.fp,
            quality=quality, authorization=authorization, approval_policy=r / 'approval-policy.json',
            expected_policy_sha256=file_digest(r / 'approval-policy.json'))
        pack(r / 'approved', r / 'approved.tar.gz'); pack(r / 'candidate', r / 'candidate.tar.gz')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.ref = self.reference(self.shared / 'base', self.shared / 'p2.tar.gz')

    def reference(self, root, archive, research=False):
        if research:
            info = u.verify(P, root, self.fp); identifier = info['node_id']; package = info['receipt']['package_checksum']
        else:
            info = read_parent(P, root, self.fp, baseline_format='p2'); identifier = info['baseline_id']; package = info['package_checksum']
        return {'schema_version': 2, 'package_format': 'schema2-research-baseline' if research else 'p2-snapshot-v1',
            'accepted_package_schema': 2 if research else 1, 'node_id': identifier, 'package_checksum': package,
            'manifest_sha256': file_digest(root / 'checksums.json'), 'transport_sha256': file_digest(archive),
            'source_state': u.state(read_json(root / 'selection.json')), 'upstreams': self.revs,
            'build_fingerprint': self.fp, 'corpus_sha256': file_digest(root / 'corpus.jsonl'),
            'parent_rows_sha256': '0' * 64, 'storage': {'kind': 'LOCAL_QUALIFICATION_ONLY'},
            'authorization': {'type': 'EXPLICIT_RESEARCH_BASELINE_REUSE' if research else 'EXPLICIT_P2_BOOTSTRAP',
                'scope': 'ISOLATED_RESEARCH_PARENT_REUSE', 'research_only': True, 'experiment_id': 'CONTROLLED_TEST_FIXTURE',
                'automatic_research_approval': False, 'production_promotion': 'NOT_AUTHORIZED'}}

    def restore(self, *, archive=None, controlled=False):
        return t.restore(P, archive or self.shared / 'p2.tar.gz', self.root / 'restore', self.ref, digest(self.ref), controlled=controlled)

    def reject(self):
        with self.assertRaises((ValueError, OSError, KeyError, TypeError)): self.restore()
        self.assertFalse((self.root / 'restore').exists())

    def test_valid_explicit_P2_bootstrap_is_not_P41_approval(self):
        result = self.restore()
        self.assertEqual(result['parent_reuse'], 'P2_BOOTSTRAP_ONLY')
        self.assertEqual(result['approval'], 'NOT_APPLICABLE_P2_ANCHOR')

    def test_independent_directory_recovery(self):
        self.restore()
        self.assertEqual(file_digest(self.root / 'restore/corpus.jsonl'), self.ref['corpus_sha256'])

    def test_explicit_controlled_P41_approved_schema2_package(self):
        self.ref = self.reference(self.shared / 'approved', self.shared / 'approved.tar.gz', research=True)
        r = self.restore(archive=self.shared / 'approved.tar.gz', controlled=True)
        self.assertEqual(r['approval'], 'P4_1_VERIFIED'); self.assertTrue(r['controlled_fixture'])

    def test_controlled_approved_package_cannot_claim_genuine(self):
        self.ref = self.reference(self.shared / 'approved', self.shared / 'approved.tar.gz', research=True)
        with self.assertRaisesRegex(ValueError, 'Controlled provenance'):
            self.restore(archive=self.shared / 'approved.tar.gz')

    def test_unapproved_candidate_rejected(self):
        self.ref = self.reference(self.shared / 'candidate', self.shared / 'candidate.tar.gz', research=True)
        with self.assertRaisesRegex(ValueError, 'Unapproved candidate'):
            self.restore(archive=self.shared / 'candidate.tar.gz', controlled=True)

    def test_missing_authorization(self):
        self.ref['authorization'] = {}; self.reject()

    def test_authorization_cannot_approve(self):
        self.ref['authorization']['automatic_research_approval'] = True; self.reject()

    def test_wrong_transport_checksum(self):
        self.ref['transport_sha256'] = '0' * 64; self.reject()

    def test_wrong_node_id(self):
        self.ref['node_id'] = 'sha256-' + '0' * 64; self.reject()

    def test_wrong_source_state(self):
        self.ref['source_state'] = 'sha256-' + '0' * 64; self.reject()

    def test_wrong_upstream_revision(self):
        self.ref['upstreams'] = {**self.revs, 'SPHinXsim': '0' * 40}; self.reject()

    def test_wrong_corpus_hash(self):
        self.ref['corpus_sha256'] = '0' * 64; self.reject()

    def test_wrong_logical_package_checksum(self):
        self.ref['package_checksum'] = '0' * 64; self.reject()

    def test_wrong_manifest_checksum(self):
        self.ref['manifest_sha256'] = '0' * 64; self.reject()

    def test_unsupported_package_schema(self):
        self.ref['accepted_package_schema'] = 3; self.reject()

    def test_legacy_schema1_research_is_not_converted(self):
        self.ref['package_format'] = 'schema1-research'; self.reject()

    def test_reference_cannot_self_supply_expected_hash(self):
        with self.assertRaisesRegex(ValueError, 'Independent trusted reference'):
            t.restore(P, self.shared / 'p2.tar.gz', self.root / 'restore', self.ref, '0' * 64)

    def test_corrupt_archive_bytes(self):
        corrupt = self.root / 'corrupt.tar.gz'; corrupt.write_bytes(b'not an archive')
        self.ref['transport_sha256'] = file_digest(corrupt)
        with self.assertRaises((ValueError, OSError, tarfile.TarError)):
            self.restore(archive=corrupt)

    def unsafe(self, name, kind=tarfile.REGTYPE, duplicate=False):
        archive = self.root / 'unsafe.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for _ in range(2 if duplicate else 1):
                member = tarfile.TarInfo(name); member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = 1; tar.addfile(member, io.BytesIO(b'x'))
                else:
                    member.linkname = '/etc/passwd'; tar.addfile(member)
        self.ref['transport_sha256'] = file_digest(archive)
        with self.assertRaises((ValueError, OSError)):
            self.restore(archive=archive)
        self.assertFalse((self.root / 'restore').exists())

    def test_traversal_rejected(self): self.unsafe('../escape')
    def test_absolute_path_rejected(self): self.unsafe('/escape')
    def test_duplicate_member_rejected(self): self.unsafe('same', duplicate=True)
    def test_symlink_member_rejected(self): self.unsafe('link', tarfile.SYMTYPE)
    def test_hardlink_member_rejected(self): self.unsafe('link', tarfile.LNKTYPE)
    def test_backslash_path_rejected(self): self.unsafe('a\\escape')

    def test_archive_member_corrupt_after_transport_repin(self):
        payload = self.root / 'payload'; shutil.copytree(self.shared / 'base', payload)
        (payload / 'corpus.jsonl').write_text('corrupt\n')
        archive = self.root / 'mutated.tar.gz'; pack(payload, archive)
        self.ref['transport_sha256'] = file_digest(archive)
        with self.assertRaises(ValueError): self.restore(archive=archive)
        self.assertFalse((self.root / 'restore').exists())

    def test_existing_destination_is_preserved(self):
        (self.root / 'restore').mkdir(); (self.root / 'restore/keep').write_text('keep')
        with self.assertRaises(ValueError): self.restore()
        self.assertEqual((self.root / 'restore/keep').read_text(), 'keep')


class ReleaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.storage = {'kind': 'GITHUB_RELEASE_ASSET', 'repository': t.RELEASE_REPOSITORY,
                        'release_id': 123, 'asset_id': 456, 'tag': 'research-p2-sha256-pinned',
                        'asset_name': 'anchor.tar.gz', 'immutable_required': True}
        self.data = b'controlled transport payload'
        self.sha = c.hashlib.sha256(self.data).hexdigest()
        self.release = {'id': 123, 'immutable': True, 'draft': False, 'tag_name': self.storage['tag'],
                        'assets': [{'id': 456, 'name': 'anchor.tar.gz'}]}
        self.asset = {'id': 456, 'name': 'anchor.tar.gz', 'state': 'uploaded', 'size': len(self.data), 'digest': 'sha256:' + self.sha}

    def request(self, url, *, binary=False):
        return io.BytesIO(self.data if binary else json.dumps(self.release if '/releases/123' in url else self.asset).encode())

    def download(self):
        with patch.object(t, '_request', side_effect=self.request):
            return t.download(self.storage, self.root / 'download', self.sha)

    def test_pinned_immutable_asset_download_controlled_transport(self):
        self.assertTrue(self.download()['immutable'])

    def test_mutable_release_rejected(self):
        self.release['immutable'] = False
        with self.assertRaises(ValueError): self.download()

    def test_asset_replaced_under_same_filename_rejected(self):
        self.asset['id'] = 789
        with self.assertRaises(ValueError): self.download()

    def test_asset_from_other_release_rejected(self):
        self.release['assets'] = []
        with self.assertRaises(ValueError): self.download()

    def test_moving_latest_locator_rejected(self):
        self.storage['tag'] = 'latest'
        with self.assertRaises(ValueError): self.download()

    def test_missing_immutability_requirement_rejected(self):
        self.storage.pop('immutable_required')
        with self.assertRaises(ValueError): self.download()

    def test_wrong_download_checksum_rejected(self):
        self.sha = '0' * 64
        with self.assertRaises(ValueError): self.download()

    def test_truncated_stream_rejected(self):
        self.asset['size'] += 1
        with self.assertRaises(ValueError): self.download()

    def test_foreign_repository_rejected(self):
        self.storage['repository'] = 'untrusted/repo'
        with self.assertRaises(ValueError): self.download()


if __name__ == '__main__': unittest.main()
