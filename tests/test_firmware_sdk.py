"""Locally built SDK normalization never weakens downloaded archive extraction."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('firmware_sdk', ROOT / 'scripts/prepare-firmware.py')
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


class LocalSdkNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / 'sdk.tar'
        self.normalized = self.root / 'normalized.tar'
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)

    def fixture(self, links=(), extra=()):
        with tarfile.open(self.archive, 'w') as tar:
            member = tarfile.TarInfo('sdk/Makefile')
            member.size, member.mode = 4, 0o644
            tar.addfile(member, io.BytesIO(b'all:'))
            for name, target in links:
                member = tarfile.TarInfo(name)
                member.type, member.linkname = tarfile.SYMTYPE, target
                tar.addfile(member)
            for name, data in extra:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))

    def test_portable_forwarders_retain_arguments_and_downloaded_extractor_stays_strict(self):
        self.fixture([('sdk/staging_dir/host/bin/which', '/usr/bin/which'),
                      ('sdk/relative', 'Makefile')])
        before = self.archive.read_bytes()
        firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
        sdk = firmware.prepare.extract_sdk(self.normalized, self.root / 'extracted')
        which = sdk / 'staging_dir/host/bin/which'
        trusted = Path('/usr/bin/which').resolve(strict=True)
        self.assertFalse(which.is_symlink())
        self.assertEqual(which.read_bytes(), b'#!/bin/sh\nexec /usr/bin/which "$@"\n')
        self.assertEqual(which.stat().st_mode & 0o777, 0o755)
        self.assertEqual(subprocess.check_output([str(which), 'sh'], text=True).strip(),
                         subprocess.check_output([str(trusted), 'sh'], text=True).strip())
        self.assertEqual((sdk / 'Makefile').read_bytes(), b'all:')
        self.assertTrue((sdk / 'relative').is_symlink())
        self.assertEqual(self.archive.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'absolute SDK archive link'):
            firmware.prepare.extract_sdk(self.archive, self.root / 'strict-downloaded')

    def test_all_approved_host_tools_use_portable_fixed_forwarders(self):
        same = ['which', 'gzip', 'realpath', 'rsync', 'bash', 'egrep', 'wget',
                'seq', 'git', 'diff', 'file', 'perl', 'grep', 'getopt', 'unzip',
                'cp', 'install', 'stat', 'g++']
        links = [('sdk/staging_dir/host/bin/' + name, '/usr/bin/' + name) for name in same]
        links += [('sdk/staging_dir/host/bin/gcc', '/usr/bin/cc'),
                  ('sdk/staging_dir/host/bin/python', '/usr/bin/python3.12'),
                  ('sdk/staging_dir/host/bin/python3', '/usr/bin/python3.12'),
                  ('sdk/staging_dir/host/bin/awk', '/usr/bin/gawk')]
        self.fixture(links)
        firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
        sdk = firmware.prepare.extract_sdk(self.normalized, self.root / 'extracted')
        for path, original in links:
            target = '/usr/bin/python3' if original == '/usr/bin/python3.12' else original
            wrapper = sdk / Path(path).relative_to('sdk')
            self.assertEqual(wrapper.read_text(), '#!/bin/sh\nexec ' + target + ' "$@"\n')
            self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
        self.assertIn('gzip', subprocess.check_output(
            [str(sdk / 'staging_dir/host/bin/gzip'), '--version'], text=True))
        original = self.root / 'input with spaces'
        copied = self.root / 'output with spaces'
        original.write_bytes(b'quoted argument fixture')
        subprocess.run([str(sdk / 'staging_dir/host/bin/cp'), str(original), str(copied)], check=True)
        self.assertEqual(copied.read_bytes(), original.read_bytes())

    def test_source_scripts_keep_licensed_bytes_and_mode_without_absolute_links(self):
        links = []
        for name, filename in [('xxd', 'xxdi.pl'), ('ldconfig', 'noop.sh')]:
            script = self.source / 'scripts' / filename
            script.write_bytes(b'#!/bin/sh\n# SPDX-License-Identifier: GPL-2.0-only\nexit 0\n')
            script.chmod(0o755)
            links.append(('sdk/staging_dir/host/bin/' + name, str(script)))
        self.fixture(links)
        firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
        sdk = firmware.prepare.extract_sdk(self.normalized, self.root / 'extracted')
        for name, filename in [('xxd', 'xxdi.pl'), ('ldconfig', 'noop.sh')]:
            target = sdk / 'staging_dir/host/bin' / name
            self.assertEqual(target.read_bytes(), (self.source / 'scripts' / filename).read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertFalse(target.is_symlink())

    def test_source_script_symlink_cannot_read_a_foreign_host_file(self):
        script = self.source / 'scripts/xxdi.pl'
        script.symlink_to('/usr/bin/which')
        self.fixture([('sdk/staging_dir/host/bin/xxd', str(script))])
        with self.assertRaises(ValueError):
            firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
        self.assertFalse(self.normalized.exists())

    def test_source_script_invalid_mode_or_oversize_fails_before_output(self):
        script = self.source / 'scripts/noop.sh'
        for data, mode in [(b'#!/bin/sh\nexit 0\n', 0o644), (b'x' * (1024 * 1024 + 1), 0o755)]:
            with self.subTest(mode=mode, size=len(data)):
                script.write_bytes(data)
                script.chmod(mode)
                self.fixture([('sdk/staging_dir/host/bin/ldconfig', str(script))])
                with self.assertRaisesRegex(ValueError, 'size or executable mode'):
                    firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
                self.assertFalse(self.normalized.exists())

    def test_noncanonical_path_or_multiple_roots_fail_before_output(self):
        for name in ['sdk/../foreign', 'sdk//foreign', './sdk/foreign', '/sdk/foreign', 'other/foreign']:
            with self.subTest(name=name):
                self.fixture(extra=[(name, b'foreign')])
                with self.assertRaises(ValueError):
                    firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
                self.assertFalse(self.normalized.exists())

    def test_unknown_absolute_escaping_and_nonmatching_which_links_fail_closed(self):
        for name, target in [('sdk/staging_dir/host/bin/other', '/usr/bin/which'),
                             ('sdk/staging_dir/host/bin/which', '/etc/passwd'),
                             ('sdk/staging_dir/host/bin/which', '../../../../../../usr/bin/which'),
                             ('sdk/bad', '../../outside')]:
            with self.subTest(name=name, target=target):
                self.fixture([(name, target)])
                with self.assertRaises(ValueError):
                    firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
                self.assertFalse(self.normalized.exists())

    def test_duplicate_path_or_symlink_parent_collision_is_rejected_before_output(self):
        cases = [([('sdk/staging_dir/host/bin/which', '/usr/bin/which')],
                  [('sdk/staging_dir/host/bin/which', b'foreign')]),
                 ([('sdk/staging_dir/host', '../other'),
                   ('sdk/staging_dir/host/bin/which', '/usr/bin/which')], []),
                 ([], [('sdk/Makefile/child', b'foreign')])]
        for links, extra in cases:
            with self.subTest(links=links, extra=extra):
                self.fixture(links, extra)
                with self.assertRaises(ValueError):
                    firmware.normalize_local_sdk(self.archive, self.normalized, self.source)
                self.assertFalse(self.normalized.exists())


if __name__ == '__main__':
    unittest.main()
