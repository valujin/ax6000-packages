"""Publish validated native stock factory images without changing raw provenance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_stock_factory_payload import compressed, fit, raw_ubi, roots
from test_publish import ipk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('publish_stock_factory', ROOT / 'scripts/publish.py')
publisher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(publisher)
USIGN = os.environ.get('USIGN')
PREFIX = 'immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-'


class StockFactoryPublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.raw = self.root / 'raw'
        self.target = self.raw / 'bin/targets/mediatek/filogic'; self.target.mkdir(parents=True)
        self.ram = self.target / (PREFIX + 'initramfs-kernel.bin')
        self.factory = self.target / (PREFIX + 'initramfs-factory.ubi')
        _, entries = roots(); self.ram.write_bytes(fit(compressed(entries)))
        self.factory.write_bytes(raw_ubi(self.ram.read_bytes()))
        archive = self.target / 'sdk.tar.xz'; archive.write_bytes(b'verified frozen SDK')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        build = 'redmi-ax6000-' + 'a' * 12 + '-' + digest[:12]
        self.profile = {'id': 'redmi-ax6000', 'enabled': True, 'build_id': build, 'packages': ['tailscale'],
                        'package_only': ['tailscale'], 'image_packages': [], 'firmware': {
                            'commit': 'a' * 40, 'device': 'xiaomi_redmi-router-ax6000-stock',
                            'architecture': 'aarch64_cortex-a53', 'kernel_version': '6.6.133~' + 'a' * 32 + '-r1',
                            'target': 'mediatek', 'subtarget': 'filogic'},
                        'sdk': {'url': f'https://example.org/firmware/{build}/sdk.tar.xz', 'sha256': digest}}

    def test_real_valid_factory_is_offered_with_checksums_and_raw_bytes_preserved(self):
        excluded = self.target / 'immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-ubootmod-initramfs-factory.ubi'
        excluded.write_bytes(b'foreign factory')
        original = {p: p.read_bytes() for p in self.target.iterdir()}
        output = publisher.stage_firmware(self.raw, self.root / 'public', self.profile)
        self.assertTrue((output / self.factory.name).is_file(), 'Validated stock factory must be downloadable')
        self.assertEqual((output / self.factory.name).read_bytes(), self.factory.read_bytes())
        self.assertFalse((output / excluded.name).exists())
        self.assertIn(hashlib.sha256(self.factory.read_bytes()).hexdigest() + '  ' + self.factory.name,
                      (output / 'SHA256SUMS').read_text())
        self.assertEqual((output / 'SHA256SUMS').read_bytes(), (output / 'sha256sums').read_bytes())
        for path, data in original.items(): self.assertEqual(path.read_bytes(), data)

    def test_invalid_or_over_limit_factory_cannot_create_output(self):
        self.factory.write_bytes(self.factory.read_bytes()[:-1])
        with self.assertRaises(ValueError): publisher.stage_firmware(self.raw, self.root / 'invalid', self.profile)
        self.assertFalse((self.root / 'invalid').exists())
        with self.factory.open('wb') as stream: stream.truncate(30 * 1024**2 + 1)
        with self.assertRaises(ValueError): publisher.stage_firmware(self.raw, self.root / 'too-large', self.profile)
        self.assertFalse((self.root / 'too-large').exists())

    def test_tailscale_policy_requires_factory_but_legacy_absence_is_allowed(self):
        self.factory.unlink()
        with self.assertRaises(ValueError): publisher.stage_firmware(self.raw, self.root / 'missing', self.profile)
        self.assertFalse((self.root / 'missing').exists())
        self.profile.pop('package_only')
        output = publisher.stage_firmware(self.raw, self.root / 'legacy', self.profile)
        self.assertTrue((output / self.ram.name).is_file())

    def test_factory_symlink_is_rejected_before_output_creation(self):
        outside = self.root / 'outside.ubi'; self.factory.rename(outside)
        self.factory.symlink_to(outside)
        with self.assertRaises(ValueError): publisher.stage_firmware(self.raw, self.root / 'symlink', self.profile)
        self.assertFalse((self.root / 'symlink').exists())

    @unittest.skipUnless(USIGN, 'Pinned official usign required')
    def test_cli_checks_factory_before_any_feed_or_output_is_created(self):
        key, public = self.root / 'key', self.root / 'public-key'
        subprocess.run([USIGN, '-G', '-s', str(key), '-p', str(public)], check=True, capture_output=True)
        package = self.raw / 'bin/packages/tailscale.ipk'; package.parent.mkdir(parents=True)
        ipk(package, name='tailscale', arch='all')
        profile = self.root / 'profile.json'; profile.write_text(json.dumps(self.profile))
        lock = self.root / 'lock.json'; lock.write_text(json.dumps({'profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest()}))
        self.factory.write_bytes(b'not a stock UBI')
        output = self.root / 'rejected-public'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/publish.py'), '--profile', str(profile),
            '--input', str(self.raw), '--output', str(output), '--lock', str(lock), '--key', str(key),
            '--public-key', str(public), '--usign', USIGN], text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists(), 'Factory preflight must precede feed staging/signing')


if __name__ == '__main__': unittest.main()
