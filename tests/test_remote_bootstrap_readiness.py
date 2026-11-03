"""Narrow interrupted-transport tests: controlled public-API fixtures, no network."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from src import trusted_snapshot as t
import test_trusted_snapshot as fixtures


class AtomicDownloadTests(unittest.TestCase):
    request = fixtures.ReleaseRecoveryTests.request
    download = fixtures.ReleaseRecoveryTests.download

    def setUp(self):
        fixtures.ReleaseRecoveryTests.setUp(self)

    def assert_clean_failure(self):
        self.assertFalse((self.root / 'download').exists())
        self.assertEqual(list(self.root.glob('.release-download-*')), [])

    def test_interrupted_stream_cleans_partial_file(self):
        outer = self
        class Broken(io.BytesIO):
            def read(self, n=-1):
                if self.tell(): raise OSError('Controlled stream interruption')
                return super().read(2)
        def request(url, *, binary=False):
            return Broken(outer.data) if binary else outer.request(url)
        with patch.object(t, '_request', side_effect=request):
            with self.assertRaises(OSError): t.download(self.storage, self.root / 'download', self.sha)
        self.assert_clean_failure()

    def test_corrupt_bytes_never_reach_final_path(self):
        self.data = bytes([self.data[0] ^ 1]) + self.data[1:]
        with self.assertRaises(ValueError): self.download()
        self.assert_clean_failure()

    def test_short_stream_cleans_staging(self):
        self.data = self.data[:-1]
        with self.assertRaises(ValueError): self.download()
        self.assert_clean_failure()

    def test_oversized_stream_cleans_staging(self):
        self.data += b'oversized'
        with self.assertRaises(ValueError): self.download()
        self.assert_clean_failure()

    def test_missing_asset_leaves_no_partial_file(self):
        def request(url, *, binary=False):
            if '/assets/' in url: raise urllib.error.HTTPError(url, 404, 'Controlled missing asset', {}, None)
            return self.request(url)
        with patch.object(t, '_request', side_effect=request):
            with self.assertRaises(urllib.error.HTTPError): t.download(self.storage, self.root / 'download', self.sha)
        self.assert_clean_failure()

    def test_interrupted_open_leaves_no_temp_directory(self):
        def request(url, *, binary=False):
            if binary: raise TimeoutError('Controlled timeout opening stream')
            return self.request(url)
        with patch.object(t, '_request', side_effect=request):
            with self.assertRaises(TimeoutError): t.download(self.storage, self.root / 'download', self.sha)
        self.assert_clean_failure()

    def test_destination_is_invisible_until_verified(self):
        outer = self
        class Observed(io.BytesIO):
            def read(self, n=-1):
                outer.assertFalse((outer.root / 'download').exists())
                return super().read(n)
        def request(url, *, binary=False):
            return Observed(self.data) if binary else self.request(url)
        with patch.object(t, '_request', side_effect=request):
            t.download(self.storage, self.root / 'download', self.sha)
        self.assertEqual((self.root / 'download').read_bytes(), self.data)
        self.assertEqual(list(self.root.glob('.release-download-*')), [])

    def test_existing_destination_never_replaced_or_requested(self):
        destination = self.root / 'download'; destination.write_bytes(b'operator-owned file')
        with patch.object(t, '_request') as request:
            with self.assertRaises(ValueError): t.download(self.storage, destination, self.sha)
            request.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'operator-owned file')

    def test_concurrent_destination_creation_is_not_overwritten(self):
        outer = self
        class Racing(io.BytesIO):
            def read(self, n=-1):
                value = super().read(n)
                if not value: (outer.root / 'download').write_bytes(b'concurrent operator file')
                return value
        def request(url, *, binary=False):
            return Racing(self.data) if binary else self.request(url)
        with patch.object(t, '_request', side_effect=request):
            with self.assertRaises(FileExistsError): t.download(self.storage, self.root / 'download', self.sha)
        self.assertEqual((self.root / 'download').read_bytes(), b'concurrent operator file')
        self.assertEqual(list(self.root.glob('.release-download-*')), [])

    def test_stale_final_download_is_rejected(self):
        destination = self.root / 'download'; destination.write_bytes(b'old partial download')
        with self.assertRaises(ValueError): self.download()
        self.assertEqual(destination.read_bytes(), b'old partial download')

    def test_unrelated_stale_staging_is_never_consumed(self):
        stale = self.root / '.release-download-unrelated'; stale.mkdir()
        (stale / 'payload').write_bytes(b'untrusted stale bytes')
        self.download()
        self.assertEqual((self.root / 'download').read_bytes(), self.data)
        self.assertEqual((stale / 'payload').read_bytes(), b'untrusted stale bytes')

    def test_download_does_not_modify_registry(self):
        original = t.REGISTRY.read_bytes()
        self.download()
        self.assertEqual(t.REGISTRY.read_bytes(), original)


if __name__ == '__main__': unittest.main()
