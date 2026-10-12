"""Run the router shell installer against isolated /etc paths and mocked network/opkg."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ABI = '6.6.133~0123456789abcdef0123456789abcdef-r1'
BASE = 'https://valujin.github.io/ax6000-packages'
FEED = f'src/gz ax6000_custom {BASE}/feeds/redmi-ax6000/{ABI}'


class InstallFeedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.etc = self.root / 'etc'
        self.opkgdir = self.etc / 'opkg'
        self.opkgdir.mkdir(parents=True)
        self.mock = self.root / 'mock'
        self.mock.mkdir()
        self.script = self.root / 'install-feed.sh'
        self.script.write_text((ROOT / 'scripts/install-feed.sh').read_text().replace('/etc', str(self.etc)))
        self.env = dict(os.environ, PATH=str(self.mock) + ':' + os.environ['PATH'],
                        MOCK_ABI=ABI, MOCK_ARCH='aarch64_cortex-a53', MOCK_SIG_FAIL='0',
                        MOCK_UPDATE_FAIL='0', MOCK_LOG=str(self.root / 'opkg.log'))
        self.tool('id', '#!/bin/sh\nprintf "0\\n"\n')
        self.tool('wget', '''#!/usr/bin/env python3
import json, pathlib, sys
path = pathlib.Path(sys.argv[sys.argv.index('-O') + 1]); url = sys.argv[-1]
if url.endswith('/profile.json'):
 path.write_text(json.dumps({'id': 'redmi-ax6000', 'firmware': {'architecture': 'aarch64_cortex-a53', 'kernel_version': ''' + repr(ABI) + '''}}))
else: path.write_text('mock verified download\\n')
''')
        self.tool('jsonfilter', '''#!/usr/bin/env python3
import json, sys
value=json.load(open(sys.argv[sys.argv.index('-i')+1]))
for part in sys.argv[sys.argv.index('-e')+1][2:].split('.'): value=value[part]
print(value)
''')
        self.tool('usign', '''#!/usr/bin/env python3
import os,sys
if '-F' in sys.argv: print('eaad5e29fcc9ed69')
else: sys.exit(int(os.environ['MOCK_SIG_FAIL']))
''')
        self.tool('opkg', '''#!/usr/bin/env python3
import os, pathlib, sys
cmd=sys.argv[1:]
if cmd==['status','kernel']: print('Package: kernel\\nVersion: '+os.environ['MOCK_ABI'])
elif cmd==['print-architecture']: print('arch '+os.environ['MOCK_ARCH']+' 10')
elif cmd==['update']:
 with open(os.environ['MOCK_LOG'],'a') as log: log.write('update\\n')
 sys.exit(int(os.environ['MOCK_UPDATE_FAIL']))
else: raise SystemExit('Unexpected package operation: '+repr(cmd))
''')
        self.configs = {
            self.etc / 'opkg.conf': 'dest root /\noption check_signature\narch aarch64_cortex-a53 10\n'
                'src/gz embedded https://foreign.example/6.6.151\n',
            self.opkgdir / 'distfeeds.conf': '# original comment\nsrc/gz immortalwrt_core https://foreign.example/core\n'
                'src/gz immortalwrt_amneziawg https://foreign.example/missing\n',
            self.opkgdir / 'customfeeds.conf': 'option http_timeout 30\nsrc/gz ax6000_custom https://old.example/wrong-abi\n',
            self.opkgdir / 'vendor.conf': 'src vendor https://foreign.example/high-version\n'
                'lists_dir ext /var/opkg-lists\n',
        }
        for index, (path, content) in enumerate(self.configs.items()):
            path.write_text(content)
            path.chmod(0o600 if index % 2 else 0o640)
        self.modes = {path: path.stat().st_mode & 0o777 for path in self.configs}

    def tool(self, name, text):
        path = self.mock / name
        path.write_text(text)
        path.chmod(0o755)

    def run_installer(self):
        return subprocess.run(['sh', str(self.script)], env=self.env, capture_output=True, text=True)

    def assert_unmodified(self):
        for path, original in self.configs.items():
            self.assertEqual(path.read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, self.modes[path])
        self.assertFalse((self.opkgdir / 'keys').exists())
        self.assertEqual(list(self.opkgdir.glob('ax6000-feed-backup.*')), [])
        self.assertFalse((self.root / 'opkg.log').exists())

    def active_sources(self):
        paths = [self.etc / 'opkg.conf', *self.opkgdir.glob('*.conf')]
        return [line for path in paths for line in path.read_text().splitlines()
                if line.split() and line.split()[0] in ('src', 'src/gz')]

    def test_identity_and_signature_fail_before_any_configuration_mutation(self):
        for variable, value in [('MOCK_ABI', '6.6.95~wrong-r1'), ('MOCK_ARCH', 'wrong-arch'), ('MOCK_SIG_FAIL', '1')]:
            with self.subTest(variable=variable):
                previous = self.env[variable]
                self.env[variable] = value
                result = self.run_installer()
                self.assertNotEqual(result.returncode, 0)
                self.assert_unmodified()
                self.env[variable] = previous

    def test_all_loaded_feeds_are_disabled_with_exact_backups_and_permissions(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.active_sources(), [FEED])
        backups = list(self.opkgdir.glob('ax6000-feed-backup.*'))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), result.stdout)
        for path, original in self.configs.items():
            saved = backups[0] / 'original' / path.relative_to(self.etc)
            self.assertEqual(saved.read_bytes(), original.encode())
            self.assertEqual(saved.stat().st_mode & 0o777, self.modes[path])
            self.assertEqual(path.stat().st_mode & 0o777, self.modes[path])
            for line in original.splitlines():
                if not line.startswith(('src ', 'src/gz ')):
                    self.assertIn(line + '\n', path.read_text())
        self.assertIn('Verified matching feed enabled', result.stdout)
        self.assertEqual((self.root / 'opkg.log').read_text(), 'update\n')

    def test_repeat_keeps_exactly_one_matching_active_feed(self):
        for _ in range(2):
            result = self.run_installer()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.active_sources(), [FEED])
        self.assertEqual(len(list(self.opkgdir.glob('ax6000-feed-backup.*'))), 2)

    def test_existing_matching_entries_across_files_are_deduplicated(self):
        for path in (self.etc / 'opkg.conf', self.opkgdir / 'vendor.conf'):
            path.write_text(path.read_text() + FEED + '\n' + FEED + '\n')
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.active_sources(), [FEED])

    def test_absent_customfeeds_is_created_after_preflight(self):
        (self.opkgdir / 'customfeeds.conf').unlink()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.active_sources(), [FEED])
        self.assertEqual((self.opkgdir / 'customfeeds.conf').stat().st_mode & 0o777, 0o644)

    def test_symlink_key_directory_refuses_before_configuration_mutation(self):
        outside = self.root / 'outside-keys'
        outside.mkdir()
        (self.opkgdir / 'keys').symlink_to(outside, target_is_directory=True)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('keys', result.stderr)
        for path, original in self.configs.items():
            self.assertEqual(path.read_text(), original)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(self.opkgdir.glob('ax6000-feed-backup.*')), [])

    def test_update_failure_prints_backup_but_never_success(self):
        self.env['MOCK_UPDATE_FAIL'] = '4'
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.active_sources(), [FEED])
        self.assertIn('Backup:', result.stdout)
        self.assertNotIn('Verified matching feed enabled', result.stdout)

    def test_late_symlink_configuration_refuses_before_any_mutation(self):
        outside = self.root / 'outside'
        outside.write_text('src/gz unsafe https://outside.example\n')
        (self.opkgdir / 'zz-symlink.conf').symlink_to(outside)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('symlink', result.stderr)
        self.assertEqual(outside.read_text(), 'src/gz unsafe https://outside.example\n')
        self.assert_unmodified()


if __name__ == '__main__':
    unittest.main()
