"""Firmware must not default to moving feeds with a different kernel identity."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('firmware_repository', ROOT / 'scripts/prepare-firmware.py')
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


class FirmwareRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source / '.config').write_bytes(b'CONFIG_KERNEL_FIXED=y\n')

    def test_defaults_have_no_foreign_feeds_and_bundle_real_trusted_key(self):
        firmware.repository_files(self.source)
        feeds = self.source / 'files/etc/opkg/distfeeds.conf'
        self.assertFalse(any(line.split()[:1] in [['src'], ['src/gz']]
                             for line in feeds.read_text().splitlines()))
        key = self.source / 'files/etc/opkg/keys/eaad5e29fcc9ed69'
        self.assertEqual(key.read_bytes(), (ROOT / 'keys/repository.pub').read_bytes())
        installer = self.source / 'files/usr/sbin/ax6000-install-feed'
        self.assertEqual(installer.read_bytes(), (ROOT / 'scripts/install-feed.sh').read_bytes())
        self.assertEqual(installer.stat().st_mode & 0o777, 0o755)
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        firmware.repository_files(self.source)
        firmware.repository_files(self.source, check_only=True)
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes()
                                  for p in self.source.rglob('*') if p.is_file()})
        self.assertEqual((self.source / '.config').read_bytes(), b'CONFIG_KERNEL_FIXED=y\n')

    def test_missing_defaults_are_rejected_during_verification(self):
        with self.assertRaisesRegex(ValueError, 'repository overlay'):
            firmware.repository_files(self.source, check_only=True)

    def test_foreign_overlay_is_not_overwritten_and_preflight_prevents_partial_writes(self):
        key = self.source / 'files/etc/opkg/keys/eaad5e29fcc9ed69'
        key.parent.mkdir(parents=True)
        key.write_bytes(b'foreign key\n')
        with self.assertRaisesRegex(ValueError, 'repository overlay'):
            firmware.repository_files(self.source)
        self.assertEqual(key.read_bytes(), b'foreign key\n')
        self.assertFalse((self.source / 'files/etc/opkg/distfeeds.conf').exists())

    def test_overlay_symlink_is_rejected(self):
        outside = self.source / 'outside'
        outside.mkdir()
        (self.source / 'files').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'repository overlay'):
            firmware.repository_files(self.source)
        self.assertEqual(list(outside.iterdir()), [])

    def test_nonexecutable_installer_is_rejected_before_build_or_export(self):
        firmware.repository_files(self.source)
        (self.source / 'files/usr/sbin/ax6000-install-feed').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'permissions'):
            firmware.repository_files(self.source, check_only=True)
