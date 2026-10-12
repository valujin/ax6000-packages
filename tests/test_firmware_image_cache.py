"""A resume must remove stale stock factory/catalog files before rebuilding."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('image_cache_prepare', ROOT / 'scripts/prepare-firmware.py')
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


class ImageCacheReset(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        self.receipt = self.root / 'cleanup.json'
        name = 'immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-initramfs-factory.ubi'
        build = Path('build_dir/target-aarch64_cortex-a53_musl')
        self.paths = [Path('bin/targets/mediatek/filogic') / name,
                      build / 'linux-mediatek_filogic/tmp' / name,
                      build / 'json_info_files' / (name + '.json'),
                      Path('bin/targets/mediatek/filogic/profiles.json')]
        for path in self.paths:
            target = self.source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'stale')
        (self.source / '.config').write_bytes(b'CONFIG_FIXED=y\n')
        self.other = self.source / 'bin/targets/mediatek/filogic/other-factory.ubi'
        self.other.write_bytes(b'other')

    def test_only_exact_stock_cache_removed_and_receipt_is_idempotent(self):
        firmware.reset_cached_images(self.profile, self.source, self.receipt)
        self.assertTrue(all(not (self.source / p).exists() for p in self.paths))
        self.assertEqual(self.other.read_bytes(), b'other')
        self.assertEqual((self.source / '.config').read_bytes(), b'CONFIG_FIXED=y\n')
        report = json.loads(self.receipt.read_text())
        self.assertEqual(set(report['files']), {p.as_posix() for p in self.paths})
        self.assertTrue(all(record['size'] == 5 for record in report['files'].values()))
        first = self.receipt.read_bytes()
        firmware.reset_cached_images(self.profile, self.source, self.receipt)
        self.assertEqual(self.receipt.read_bytes(), first)

    def test_real_cli_phase_accepts_external_receipt_and_removes_only_stock_cache(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-firmware.py'),
                        '--profile', str(ROOT / 'profiles/redmi-ax6000-new.json'),
                        '--lock', str(ROOT / 'state/new-lock.json'),
                        '--source-dir', str(self.source), '--phase', 'reset-cached-images',
                        '--cleanup-receipt', str(self.receipt)], check=True, capture_output=True)
        self.assertTrue(self.receipt.is_file())
        self.assertTrue(all(not (self.source / p).exists() for p in self.paths))
        self.assertEqual(self.other.read_bytes(), b'other')

    def test_symlink_target_or_parent_refused_before_any_deletion(self):
        target = self.source / self.paths[2]
        target.unlink()
        target.symlink_to(self.other)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            firmware.reset_cached_images(self.profile, self.source, self.receipt)
        self.assertTrue((self.source / self.paths[0]).exists())
        self.assertFalse(self.receipt.exists())
        target.unlink()
        target.parent.rename(target.parent.with_name('actual-json'))
        target.parent.symlink_to(target.parent.with_name('actual-json'), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            firmware.reset_cached_images(self.profile, self.source, self.receipt)
        self.assertTrue((self.source / self.paths[0]).exists())

    def test_foreign_receipt_or_receipt_inside_source_refused_before_deletion(self):
        self.receipt.write_text('{"foreign":true}')
        with self.assertRaisesRegex(ValueError, 'receipt'):
            firmware.reset_cached_images(self.profile, self.source, self.receipt)
        with self.assertRaisesRegex(ValueError, 'outside'):
            firmware.reset_cached_images(self.profile, self.source, self.source / 'receipt.json')
        self.assertTrue(all((self.source / p).exists() for p in self.paths))


if __name__ == '__main__':
    unittest.main()
