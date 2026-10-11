import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'publish.py'


def ipk(path, name='kmod-wireguard', arch='aarch64_cortex-a53', kernel='6.6.99~abcdef-r1'):
    control = f'Package: {name}\nVersion: 1-r1\nArchitecture: {arch}\nDescription: fixture\n'
    if name.startswith('kmod-'):
        control += f'Depends: kernel (= {kernel}), libc\n'
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        member = tarfile.TarInfo('./control')
        content = control.encode()
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    with tarfile.open(path, mode='w:gz') as archive:
        member = tarfile.TarInfo('./control.tar.gz')
        content = data.getvalue()
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'Publisher must exist before packages can be published')
        spec = importlib.util.spec_from_file_location('publish', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = {'id': 'redmi-ax6000', 'enabled': True, 'firmware': {
            'architecture': 'aarch64_cortex-a53', 'kernel_version': '6.6.99~abcdef-r1'}}

    def test_reads_real_opkg_control_archive(self):
        package = self.root / 'wireguard.ipk'
        ipk(package)
        self.assertEqual(self.module.read_control(package)['Package'], 'kmod-wireguard')

    def test_kernel_mismatch_blocks_publication(self):
        package = self.root / 'wireguard.ipk'
        ipk(package, kernel='6.6.99~DIFFERENT-r1')
        with self.assertRaisesRegex(ValueError, 'kernel|ABI'):
            self.module.validate_package(self.module.read_control(package), self.profile)

    def test_architecture_mismatch_blocks_publication(self):
        package = self.root / 'tailscale.ipk'
        ipk(package, name='tailscale', arch='aarch64_generic')
        with self.assertRaisesRegex(ValueError, 'architecture'):
            self.module.validate_package(self.module.read_control(package), self.profile)

    def test_all_architecture_is_accepted(self):
        package = self.root / 'pbr.ipk'
        ipk(package, name='pbr', arch='all')
        self.module.validate_package(self.module.read_control(package), self.profile)

    def test_kmod_missing_exact_kernel_dependency_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'kernel|ABI'):
            self.module.validate_package({'Package': 'kmod-tun', 'Architecture': 'aarch64_cortex-a53',
                                          'Version': '1', 'Depends': 'libc'}, self.profile)

    def test_feed_filenames_and_hashes_match_bytes(self):
        raw = self.root / 'raw'
        raw.mkdir()
        package = raw / 'kmod-wireguard_1_aarch64_cortex-a53.ipk'
        ipk(package)
        output = self.root / 'public'
        feed = self.module.stage_feed(raw, output, self.profile)
        index = (feed / 'Packages').read_text()
        self.assertIn('Filename: ' + package.name, index)
        self.assertIn('SHA256sum: ' + hashlib.sha256(package.read_bytes()).hexdigest(), index)
        self.assertEqual(gzip.decompress((feed / 'Packages.gz').read_bytes()).decode(), index)
        self.assertEqual((feed / package.name).read_bytes(), package.read_bytes())

    def test_mismatched_package_does_not_create_feed(self):
        raw = self.root / 'raw'
        raw.mkdir()
        ipk(raw / 'wrong.ipk', kernel='6.6.1~wrong-r1')
        output = self.root / 'public'
        with self.assertRaises(ValueError):
            self.module.stage_feed(raw, output, self.profile)
        self.assertFalse(output.exists())

    def test_empty_build_is_rejected(self):
        raw = self.root / 'raw'
        raw.mkdir()
        with self.assertRaisesRegex(ValueError, 'empty|packages'):
            self.module.stage_feed(raw, self.root / 'public', self.profile)


if __name__ == '__main__':
    unittest.main()
