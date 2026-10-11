"""Real signing integration; set USIGN to an OpenWrt host usign executable."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from urllib.parse import urlsplit

from test_publish import ipk

ROOT = Path(__file__).resolve().parents[1]
USIGN = os.environ.get('USIGN') or shutil.which('usign')


def kernel_ipk(path, version):
    data = io.BytesIO()
    control = (f'Package: kernel\nVersion: {version}\n'
               'Architecture: aarch64_cortex-a53\nDescription: kernel fixture\n').encode()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        member = tarfile.TarInfo('./control')
        member.size = len(control)
        archive.addfile(member, io.BytesIO(control))
    with tarfile.open(path, mode='w:gz') as archive:
        member = tarfile.TarInfo('./control.tar.gz')
        member.size = len(data.getvalue())
        archive.addfile(member, io.BytesIO(data.getvalue()))


class FirmwareExportIntegration(unittest.TestCase):
    def test_exported_profile_lock_and_sdk_url_describe_actual_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
            profile['packages'] = ['kmod-wireguard']
            profile['image_packages'] = []
            profile['build_source']['publication_url'] = 'https://example.com/packages'
            profile_path = root / 'profile.json'
            profile_path.write_text(json.dumps(profile))
            lock = {'schema_version': 1, 'profile_id': profile['id'],
                    'profile_sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                    'firmware': profile['firmware'], 'sdk': profile['sdk'],
                    'feeds': {feed['name']: feed['commit'] for feed in profile['feeds']}}
            lock_path = root / 'lock.json'
            lock_path.write_text(json.dumps(lock))
            source = root / 'source'
            target = source / 'bin/targets/mediatek/filogic'
            target.mkdir(parents=True)
            packages = source / 'bin/packages/aarch64_cortex-a53/base'
            packages.mkdir(parents=True)
            abi = '6.6.133~' + 'a' * 32 + '-r1'
            kernel_ipk(packages / 'kernel.ipk', abi)
            ipk(packages / 'kmod-wireguard.ipk', kernel=abi)
            (target / 'immortalwrt-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin').write_bytes(b'image')
            sdk = target / 'immortalwrt-sdk-mediatek-filogic.tar.xz'
            kernel_relative = Path('build_dir/target-aarch64_cortex-a53_musl/linux-mediatek_filogic/linux-6.6.133')
            kernel_tree = source / kernel_relative
            kernel_files = {'Makefile': b'all:', '.config': b'CONFIG_WIREGUARD=m\n',
                            'Module.symvers': b'0x12345678\tfixture\tvmlinux\tEXPORT_SYMBOL\n',
                            '.vermagic': ('a' * 32 + '\n').encode(),
                            '.config.set': b'CONFIG_WIREGUARD=m\n',
                            'include/generated/autoconf.h': b'#define CONFIG_WIREGUARD_MODULE 1\n',
                            'include/config/kernel.release': b'6.6.133\n'}
            for name, data in kernel_files.items():
                path = kernel_tree / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (source / 'tmp').mkdir()
            (source / 'tmp/.packageinfo').write_text(
                'Source-Makefile: package/kernel/linux/Makefile\nPackage: kmod-wireguard\n')
            with tarfile.open(sdk, 'w:xz') as archive:
                sdk_files = {'Makefile': b'all:'}
                sdk_files.update({str(kernel_relative / name): data for name, data in kernel_files.items()
                                  if name not in ('.vermagic', '.config.set')})
                for name, data in sdk_files.items():
                    member = tarfile.TarInfo('sdk/' + name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            signer = source / 'staging_dir/host/bin/usign'
            signer.parent.mkdir(parents=True)
            signer.write_text('#!/bin/sh\nexit 0\n')
            signer.chmod(0o755)
            (source / '.config').write_text('CONFIG_TARGET_mediatek=y\n')
            (source / 'build-selection.json').write_text(json.dumps({'selected_kmods': ['kmod-wireguard']}))
            output = root / 'raw'
            result = subprocess.run(['python3', str(ROOT / 'scripts/prepare-firmware.py'),
                                     '--profile', str(profile_path), '--lock', str(lock_path),
                                     '--source-dir', str(source), '--phase', 'export',
                                     '--output-dir', str(output)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = json.loads((output / 'profile.json').read_text())
            generated_lock = json.loads((output / 'lock.json').read_text())
            manifest = json.loads((output / 'firmware-manifest.json').read_text())
            self.assertEqual(generated['firmware']['kernel_version'], abi)
            self.assertEqual(generated_lock['firmware'], generated['firmware'])
            self.assertEqual(generated_lock['sdk'], generated['sdk'])
            self.assertEqual(generated_lock['profile_sha256'],
                             hashlib.sha256((output / 'profile.json').read_bytes()).hexdigest())
            self.assertEqual(generated['sdk']['sha256'], hashlib.sha256(sdk.read_bytes()).hexdigest())
            self.assertEqual(urlsplit(generated['sdk']['url']).path,
                             '/packages/firmware/' + manifest['build_id'] + '/' + sdk.name)
            self.assertFalse(generated['all_kmods'])
            self.assertEqual(generated_lock['feeds']['base'], profile['firmware']['commit'])
            with tarfile.open(output / 'bin/targets/mediatek/filogic' / sdk.name) as archive:
                baseline = json.load(archive.extractfile('sdk/ax6000-baseline/manifest.json'))
                self.assertEqual(baseline['kernel_version'], abi)
                self.assertEqual(baseline['core_kmods'], ['kmod-wireguard'])
                self.assertEqual(archive.extractfile('sdk/ax6000-baseline/ipks/kmod-wireguard.ipk').read(),
                                 (packages / 'kmod-wireguard.ipk').read_bytes())
                for name, data in kernel_files.items():
                    self.assertEqual(archive.extractfile('sdk/' + str(kernel_relative / name)).read(), data)


@unittest.skipUnless(USIGN, 'real OpenWrt host usign unavailable; set USIGN to run signing integration')
class RealSigningIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'raw'
        self.raw.mkdir()
        self.key = self.root / 'private.key'
        self.original_public = self.root / 'public.key'
        subprocess.run([USIGN, '-G', '-s', str(self.key), '-p', str(self.original_public)],
                       check=True, capture_output=True)
        self.profile = {'schema_version': 1, 'id': 'router', 'enabled': True,
                        'firmware': {'kernel_version': '6.6.95~abcdef-r1',
                                     'architecture': 'aarch64_cortex-a53'}}
        self.profile_file = self.root / 'profile.json'
        self.profile_file.write_text(json.dumps(self.profile))
        self.lock = {'schema_version': 1,
                     'profile_sha256': hashlib.sha256(self.profile_file.read_bytes()).hexdigest(),
                     'feeds': {}, 'tailscale': {'version': '1.96.5'}}
        self.lock_file = self.root / 'lock.json'
        self.lock_file.write_text(json.dumps(self.lock))
        self.output = self.root / 'public'

    def publish(self):
        return subprocess.run(['python3', str(ROOT / 'scripts/publish.py'),
                               '--profile', str(self.profile_file), '--input', str(self.raw),
                               '--output', str(self.output), '--key', str(self.key),
                               '--usign', USIGN, '--lock', str(self.lock_file)],
                              text=True, capture_output=True)

    def test_real_signed_publication_verifies_decompressed_opkg_index(self):
        package = self.raw / 'kmod-wireguard_1_aarch64_cortex-a53.ipk'
        ipk(package, kernel=self.profile['firmware']['kernel_version'])
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        feed = self.output / 'feeds/router' / self.profile['firmware']['kernel_version']
        decompressed = self.root / 'decompressed-Packages'
        decompressed.write_bytes(gzip.decompress((feed / 'Packages.gz').read_bytes()))
        self.assertEqual(decompressed.read_bytes(), (feed / 'Packages').read_bytes())
        public = self.output / 'repository.pub'
        # usign comments may differ, but the encoded key must match the generated pair.
        self.assertEqual(public.read_text().splitlines()[-1],
                         self.original_public.read_text().splitlines()[-1])
        verify = subprocess.run([USIGN, '-V', '-m', str(decompressed), '-p', str(public),
                                 '-x', str(feed / 'Packages.sig')], capture_output=True)
        self.assertEqual(verify.returncode, 0, verify.stderr.decode())
        decompressed.write_bytes(decompressed.read_bytes() + b'\ncorrupted')
        tampered = subprocess.run([USIGN, '-V', '-m', str(decompressed), '-p', str(public),
                                   '-x', str(feed / 'Packages.sig')], capture_output=True)
        self.assertNotEqual(tampered.returncode, 0)
        self.assertEqual((self.output / 'state/lock.json').read_bytes(), self.lock_file.read_bytes())
        self.assertFalse(any(path.read_bytes() == self.key.read_bytes()
                             for path in self.output.rglob('*') if path.is_file()))

    def test_mismatched_kernel_never_creates_publication_output(self):
        ipk(self.raw / 'wrong.ipk', kernel='6.6.95~wrong-r1')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('kernel ABI mismatch', result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
