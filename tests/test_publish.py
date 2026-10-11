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

    def test_full_builds_with_same_source_use_distinct_sdk_urls(self):
        self.profile['firmware'].update(commit='a' * 40, target='mediatek', subtarget='filogic')
        target = self.root / 'raw/bin/targets/mediatek/filogic'
        target.mkdir(parents=True)
        archive = target / 'sdk.tar.xz'
        destinations = []
        for number in (1, 2):
            archive.write_bytes(f'prepared SDK generation {number}'.encode())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            build_id = 'redmi-ax6000-' + 'a' * 12 + '-' + digest[:12]
            self.profile.update(build_id=build_id, sdk={
                'url': f'https://example.com/firmware/{build_id}/sdk.tar.xz', 'sha256': digest})
            destination = self.module.stage_firmware(self.root / 'raw', self.root / 'public', self.profile)
            destinations.append(destination)
            self.assertEqual(destination.name, build_id)
            self.assertEqual((destination / archive.name).read_bytes(), archive.read_bytes())
        self.assertNotEqual(*destinations)
        self.assertNotEqual((destinations[0] / archive.name).read_bytes(),
                            (destinations[1] / archive.name).read_bytes())

    def test_sdk_identity_rejects_wrong_digest_or_directory(self):
        self.profile['firmware'].update(commit='a' * 40, target='mediatek', subtarget='filogic')
        digest = 'b' * 64
        self.profile.update(build_id='redmi-ax6000-' + 'a' * 12 + '-' + digest[:12],
                            sdk={'url': 'https://example.com/sdk.tar.xz', 'sha256': digest})
        target = self.root / 'raw/bin/targets/mediatek/filogic'
        target.mkdir(parents=True)
        (target / 'sdk.tar.xz').write_bytes(b'wrong prepared context')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.module.stage_firmware(self.root / 'raw', self.root / 'public', self.profile)
        self.assertFalse((self.root / 'public').exists())
        self.profile['build_id'] = '../other'
        with self.assertRaisesRegex(ValueError, 'build id'):
            self.module.firmware_build_id(self.profile)

    def test_stock_downloads_exclude_generic_bootloaders_and_keep_packages(self):
        device = 'xiaomi_redmi-router-ax6000-stock'
        self.profile['firmware'].update(commit='a' * 40, target='mediatek', subtarget='filogic', device=device)
        target = self.root / 'raw/bin/targets/mediatek/filogic'
        target.mkdir(parents=True)
        prefix = 'immortalwrt-mediatek-filogic'
        images = {f'{prefix}-{device}-squashfs-sysupgrade.bin', f'{prefix}-{device}-initramfs-kernel.bin'}
        metadata = {'config.buildinfo', 'feeds.buildinfo', 'version.buildinfo', 'profiles.json',
                    prefix + '.manifest'}
        excluded = {'mt7981-ram-ddr3-bl2.bin', 'mt7986-ram-ddr4-bl2.bin',
                    'vmlinux.bin', prefix + '-kernel.bin',
                    f'{prefix}-{device}-kernel.bin',
                    f'{prefix}-xiaomi_redmi-router-ax6000-ubootmod-squashfs-sysupgrade.bin',
                    'unrelated-sdk.tar.xz'}
        for name in images | metadata | excluded:
            (target / name).write_bytes(name.encode())
        (target / 'sha256sums').write_text('stale entries including generic bootloaders\n')
        archive = target / 'actual-sdk.tar.xz'
        archive.write_bytes(b'exact SDK')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        build_id = 'redmi-ax6000-' + 'a' * 12 + '-' + digest[:12]
        self.profile.update(build_id=build_id, sdk={'url': f'https://example.com/firmware/{build_id}/{archive.name}',
                                                   'sha256': digest})
        package = self.root / 'raw/bin/packages/kmod-wireguard.ipk'
        package.parent.mkdir(parents=True)
        ipk(package)
        original_package = package.read_bytes()
        destination = self.module.stage_firmware(self.root / 'raw', self.root / 'public', self.profile)
        self.assertEqual({p.name for p in destination.iterdir()},
                         images | metadata | {archive.name, 'SHA256SUMS', 'sha256sums'})
        self.assertEqual((destination / 'sha256sums').read_bytes(), (destination / 'SHA256SUMS').read_bytes())
        checksums = (destination / 'SHA256SUMS').read_text()
        for name in images | metadata | {archive.name}:
            self.assertIn(hashlib.sha256((destination / name).read_bytes()).hexdigest() + '  ' + name, checksums)
        feed = self.module.stage_feed(self.root / 'raw', self.root / 'public', self.profile)
        self.assertEqual((feed / package.name).read_bytes(), original_package)
        for name in excluded:
            self.assertTrue((target / name).exists(), 'Full raw provenance artifact must remain')


if __name__ == '__main__':
    unittest.main()
