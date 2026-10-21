"""Reject unverified downloads and unsafe source archives before compiling."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build-opkg-host.py'
SPEC = importlib.util.spec_from_file_location('host_opkg_bootstrap', SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(bootstrap)


class HostOpkgBootstrapTests(unittest.TestCase):
    def test_cached_source_digest_is_checked_before_copying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cache = root / 'cache'; cache.mkdir()
            downloads = root / 'downloads'; downloads.mkdir()
            source = cache / 'fixture.tar.zst'; source.write_bytes(b'valid')
            spec = {'name': 'fixture', 'size': 5, 'sha256': hashlib.sha256(b'valid').hexdigest()}
            self.assertEqual(bootstrap.obtain(spec, downloads, cache).read_bytes(), b'valid')
            (downloads / source.name).unlink(); source.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                bootstrap.obtain(spec, downloads, cache)
            self.assertFalse((downloads / source.name).exists())
            source.unlink(); source.symlink_to(root / 'outside')
            (root / 'outside').write_bytes(b'valid')
            with self.assertRaisesRegex(ValueError, 'regular cached'):
                bootstrap.obtain(spec, downloads, cache)

    def test_traversal_links_and_duplicate_source_members_refuse_before_extraction(self):
        for unsafe in ('../escape', '/absolute', 'source/../../escape', 'source/link', 'source/CMakeLists.txt'):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as directory:
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w:') as archive:
                    valid = tarfile.TarInfo('source/CMakeLists.txt'); valid.size = 1
                    archive.addfile(valid, io.BytesIO(b'x'))
                    member = tarfile.TarInfo(unsafe)
                    if unsafe == 'source/link':
                        member.type = tarfile.SYMTYPE; member.linkname = '../../outside'
                    archive.addfile(member)
                root = Path(directory)
                with patch.object(bootstrap.subprocess, 'check_output', return_value=data.getvalue()):
                    with self.assertRaises(ValueError):
                        bootstrap.extract_source(root / 'verified-source.tar.zst', 'source', root / 'extracted')
                self.assertFalse((root / 'extracted').exists())


if __name__ == '__main__':
    unittest.main()
