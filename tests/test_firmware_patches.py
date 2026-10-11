"""Compatibility patches must preserve source identity and never change kernel config."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('prepare_firmware_patches', ROOT / 'scripts/prepare-firmware.py')
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


def vendor_source_fixture(source):
    """GPL driver files from pinned ec9ef10; no full source checkout."""
    manifest = json.loads((ROOT / 'patches/firmware/manifest.json').read_text())
    for spec in manifest['patches']:
        fixture = Path(spec['path']).name.replace('_', '-') + '.gz'
        before = gzip.decompress((ROOT / 'tests/fixtures' / fixture).read_bytes())
        if hashlib.sha256(before).hexdigest() != spec['before_sha256']:
            raise ValueError('pinned driver fixture has another source hash')
        driver = source / spec['path']
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_bytes(before)
    target = source / 'target/linux/mediatek/Makefile'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('KERNEL_PATCHVER:=6.6\n')
    return manifest


class FirmwarePatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.target = self.source / 'driver.c'
        self.before = b'int map(void) {\n\treturn iommu_map(domain, addr, addr, size, prot);\n}\n'
        self.after = self.before.replace(b'size, prot)', b'size, prot, GFP_KERNEL)')
        self.target.write_bytes(self.before)
        self.patch = self.source / 'fix.patch'
        self.patch.write_text('--- a/driver.c\n+++ b/driver.c\n@@ -1,3 +1,3 @@\n'
                              ' int map(void) {\n'
                              '-\treturn iommu_map(domain, addr, addr, size, prot);\n'
                              '+\treturn iommu_map(domain, addr, addr, size, prot, GFP_KERNEL);\n'
                              ' }\n')
        self.spec = {'path': 'driver.c', 'patch': 'fix.patch',
                     'sha256': hashlib.sha256(self.patch.read_bytes()).hexdigest(),
                     'before_sha256': hashlib.sha256(self.before).hexdigest(),
                     'after_sha256': hashlib.sha256(self.after).hexdigest()}
        (self.source / '.config').write_bytes(b'CONFIG_FIXED_KERNEL=m\n')

    def test_patch_changes_only_driver_and_is_idempotent(self):
        config = (self.source / '.config').read_bytes()
        self.assertTrue(callable(getattr(firmware, 'apply_source_patch', None)),
                        'firmware preparation must apply the reviewed compatibility patch')
        with self.assertRaisesRegex(ValueError, 'not applied'):
            firmware.apply_source_patch(self.source, self.spec, self.patch, check_only=True)
        firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertEqual(self.target.read_bytes(), self.after)
        firmware.apply_source_patch(self.source, self.spec, self.patch)
        firmware.apply_source_patch(self.source, self.spec, self.patch, check_only=True)
        self.assertEqual(self.target.read_bytes(), self.after)
        self.assertEqual((self.source / '.config').read_bytes(), config)

    def test_source_drift_refuses_before_writing(self):
        self.target.write_bytes(self.before + b'/* unrelated edit */\n')
        original = self.target.read_bytes()
        with self.assertRaisesRegex(ValueError, 'source hash'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertEqual(self.target.read_bytes(), original)

    def test_patch_drift_refuses_before_writing(self):
        self.patch.write_bytes(self.patch.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'patch hash'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_vendor_fix_refuses_another_kernel_series(self):
        target = self.source / 'target/linux/mediatek/Makefile'
        target.parent.mkdir(parents=True)
        target.write_text('KERNEL_PATCHVER:=6.12\n')
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        with self.assertRaisesRegex(ValueError, 'kernel series'):
            firmware.vendor_fixes(profile, self.source)
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_production_patch_applies_to_exact_pinned_driver_and_records_hashes(self):
        manifest = vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        original_config = (self.source / '.config').read_bytes()
        firmware.vendor_fixes(profile, self.source)
        firmware.vendor_fixes(profile, self.source)
        firmware.vendor_fixes(profile, self.source, check_only=True)
        for spec in manifest['patches']:
            with self.subTest(patch=spec['patch']):
                patched = self.source / spec['path']
                self.assertEqual(hashlib.sha256(patched.read_bytes()).hexdigest(),
                                 spec['after_sha256'])
        self.assertEqual((self.source / '.config').read_bytes(), original_config)
        record = self.source / 'firmware-source-patches.json'
        self.assertEqual(json.loads(record.read_text()), manifest)
        record.unlink()
        with self.assertRaisesRegex(ValueError, 'provenance'):
            firmware.vendor_fixes(profile, self.source, check_only=True)


if __name__ == '__main__':
    unittest.main()
