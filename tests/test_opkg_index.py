"""Real host-opkg regression for multiline Description masking.

HOST_OPKG must point to a trusted native x86-64 OpenWrt host tool. The fixture
feed is explicitly unsigned, local, disposable, and used only for download;
production signing and installation policy are never changed.
"""
import copy
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import imagebuilder
import publish


DESCRIPTION = 'First paragraph.\n \n Second paragraph.\n \n Third paragraph.'


def fixture_ipk(path):
    control = ('Package: multiline-probe\nVersion: 1-r1\nArchitecture: all\n'
               'Description: ' + DESCRIPTION + '\n')
    contents = io.BytesIO()
    with tarfile.open(fileobj=contents, mode='w:gz') as archive:
        data = control.encode()
        member = tarfile.TarInfo('./control')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    with tarfile.open(path, 'w:gz') as archive:
        data = contents.getvalue()
        member = tarfile.TarInfo('./control.tar.gz')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))


class OpkgIndexTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / 'raw'
        self.raw.mkdir()
        self.package = self.raw / 'multiline-probe_1-r1_all.ipk'
        fixture_ipk(self.package)
        self.control = publish.read_control(self.package)
        self.profile = {'id': 'fixture', 'enabled': True, 'firmware': {
            'architecture': 'aarch64_cortex-a53', 'kernel_version': '6.6.133~' + 'a' * 32 + '-r1'}}

    def native_index(self):
        item = {'control': self.control, 'filename': self.package.name,
                'size': self.package.stat().st_size, 'sha256': publish.sha256_file(self.package)}
        return imagebuilder.package_index([item])

    def test_native_index_keeps_description_last_without_changing_control(self):
        original = copy.deepcopy(self.control)
        index = self.native_index().decode()
        self.assertTrue(index.endswith('Description: ' + DESCRIPTION + '\n\n'))
        self.assertEqual(self.control, original)

    def test_supplemental_index_keeps_description_last_without_changing_control(self):
        feed = publish.stage_feed(self.raw, self.root / 'public', self.profile)
        index = (feed / 'Packages').read_text()
        self.assertTrue(index.endswith('Description: ' + DESCRIPTION + '\n\n'))
        self.assertEqual(publish.read_control(feed / self.package.name), self.control)


class RealHostOpkgIndexTest(OpkgIndexTest):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get('HOST_OPKG')
        if not value:
            raise unittest.SkipTest('Set HOST_OPKG to a trusted native OpenWrt host opkg')
        cls.opkg = Path(value).resolve()
        header = cls.opkg.read_bytes()[:20]
        if header[:7] != b'\x7fELF\x02\x01\x01' or header[18:20] != b'\x3e\x00':
            raise AssertionError('HOST_OPKG must be a real x86-64 ELF, not a stub or target binary')

    def download(self, repo, name):
        root = self.root / name
        for path in (root / 'root/tmp', root / 'download', root / 'lists'):
            path.mkdir(parents=True)
        config = root / 'fixture.conf'
        config.write_text('src fixture file:' + str(repo) + '\n')
        prefix = [str(self.opkg), '-f', str(config), '--offline-root', str(root / 'root'),
                  '--lists-dir', str(root / 'lists'), '--cache', str(root / 'cache'), '--add-arch', 'all:100']
        env = dict(os.environ, IPKG_NO_SCRIPT='1')
        if os.environ.get('HOST_OPKG_LIB'):
            env['LD_LIBRARY_PATH'] = os.environ['HOST_OPKG_LIB']
        update = subprocess.run(prefix + ['update'], cwd=root / 'download', env=env,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
        result = subprocess.run(prefix + ['download', 'multiline-probe'], cwd=root / 'download', env=env,
                                text=True, capture_output=True, timeout=15)
        return result, root / 'download' / self.package.name

    def test_legacy_order_reproduces_real_masked_description_failure(self):
        repo = self.root / 'legacy'
        repo.mkdir()
        shutil.copy2(self.package, repo / self.package.name)
        fields = dict(self.control, Filename=self.package.name, Size=str(self.package.stat().st_size),
                      SHA256sum=publish.sha256_file(self.package))
        (repo / 'Packages').write_text('\n'.join(k + ': ' + v for k, v in fields.items()) + '\n\n')
        result, downloaded = self.download(repo, 'old-client')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not have a valid filename field', result.stderr)
        self.assertFalse(downloaded.exists())

    def test_native_index_supports_actual_host_opkg_download(self):
        repo = self.root / 'native'
        repo.mkdir()
        shutil.copy2(self.package, repo / self.package.name)
        (repo / 'Packages').write_bytes(self.native_index())
        result, downloaded = self.download(repo, 'native-client')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(downloaded.read_bytes(), self.package.read_bytes())

    def test_supplemental_index_supports_actual_host_opkg_download(self):
        feed = publish.stage_feed(self.raw, self.root / 'public', self.profile)
        result, downloaded = self.download(feed, 'supplemental-client')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(hashlib.sha256(downloaded.read_bytes()).digest(),
                         hashlib.sha256(self.package.read_bytes()).digest())


if __name__ == '__main__':
    unittest.main()
