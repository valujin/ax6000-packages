"""SDK baked boolean declarations must survive real Kconfig normalization."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sdk_selection', ROOT / 'scripts/prepare.py')
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


@unittest.skipUnless(shutil.which('cc'), 'host C compiler is required')
class SdkKconfigSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tools_temp.cleanup)
        tools = Path(cls.tools_temp.name)
        with tarfile.open(ROOT / 'tests/fixtures/openwrt-kconfig-tools-ec9ef10.tar.gz', 'r:gz') as archive:
            manifest = json.load(archive.extractfile('fixture-manifest.json'))
            assert manifest['source_commit'] == 'ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8'
            for name, digest in manifest['files'].items():
                data = archive.extractfile(name).read()
                assert hashlib.sha256(data).hexdigest() == digest
                path = tools / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        sources = tools / 'scripts/config'
        cls.conf = tools / 'conf'
        subprocess.run(['cc', '-O0', '-I', str(sources), '-o', str(cls.conf)] +
                       [str(sources / name) for name in ('conf.c', 'confdata.c', 'expr.c',
                        'lexer.lex.c', 'menu.c', 'parser.tab.c', 'preprocess.c', 'symbol.c', 'util.c')],
                       check=True, capture_output=True, timeout=30)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.sdk = Path(self.temporary.name)
        (self.sdk / 'tmp').mkdir()
        self.profile = {'id': 'router', 'packages': ['tailscale', 'wireguard-tools', 'kmod-amneziawg'],
                        'luci_languages': ['ru'], 'all_kmods': False, 'firmware': {}}
        metadata = ''.join(f'config PACKAGE_{name}\n\ttristate "{name}"\n'
                           for name in self.profile['packages'])
        metadata += 'config LUCI_LANG_ru\n\ttristate "Russian"\n'
        (self.sdk / 'tmp/.config-package.in').write_text(metadata)
        (self.sdk / 'tmp/.packageinfo').write_text(''.join(
            f'Package: {name}\n\n' for name in self.profile['packages']))
        (self.sdk / '.config').write_text('CONFIG_MODULES=y\nCONFIG_TARGET_mediatek=y\n')
        self.context = {}
        for name, data in [('build_dir/linux/.config', b'CONFIG_ARM64=y\n'),
                           ('build_dir/linux/.vermagic', b'6ea4b96ad26e951cc0b4fa9296a521d3\n'),
                           ('build_dir/linux/Module.symvers', b'0x123\tfixture\tvmlinux\n'),
                           ('build_dir/linux/include/generated/autoconf.h', b'#define CONFIG_ARM64 1\n')]:
            path = self.sdk / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.context[path] = data

    def normalize(self, baked):
        top = ('config MODULES\n\tbool\n\tmodules\n\tdefault y\n'
               'config SDK\n\tbool\nconfig TARGET_mediatek\n\tbool\n'
               'config ALL\n\tbool\nconfig ALL_KMODS\n\tbool\n'
               'config ALL_NONSHARED\n\tbool\nconfig SIGNED_PACKAGES\n\tbool\n')
        if baked:
            top += 'source "Config-build.in"\n'
        top += 'source "tmp/.config-package.in"\n'
        (self.sdk / 'Kconfig').write_text(top)
        result = subprocess.run([str(self.conf), '--olddefconfig', 'Kconfig'], cwd=self.sdk,
                                env={**os.environ, 'KCONFIG_CONFIG': '.config'},
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('symbol value', result.stderr)
        self.assertNotIn('invalid', result.stderr)

    def test_baked_sdk_booleans_are_selected_y_and_other_packages_remain_modules(self):
        baked = ('config PACKAGE_tailscale\n\tbool\n\tdefault y\n\n'
                 'config PACKAGE_wireguard-tools\n\tbool\n\tdefault y\n\n'
                 'config LUCI_LANG_ru\n\tbool\n\tdefault y\n\n'
                 'config PACKAGE_kmod-amneziawg\n\ttristate\n\tdefault m\n')
        snapshot = self.sdk / 'Config-build.in'
        snapshot.write_text(baked)
        self.context[snapshot] = baked.encode()
        prepare.select_packages(self.profile, self.sdk)
        selected = (self.sdk / '.config').read_text()
        self.assertIn('CONFIG_PACKAGE_tailscale=y\n', selected)
        self.assertIn('CONFIG_PACKAGE_wireguard-tools=y\n', selected)
        self.assertIn('CONFIG_LUCI_LANG_ru=y\n', selected)
        self.assertIn('CONFIG_PACKAGE_kmod-amneziawg=m\n', selected)
        self.normalize(True)
        normalized = (self.sdk / '.config').read_text()
        self.assertIn('CONFIG_PACKAGE_tailscale=y\n', normalized)
        self.assertIn('CONFIG_PACKAGE_wireguard-tools=y\n', normalized)
        self.assertIn('CONFIG_PACKAGE_kmod-amneziawg=m\n', normalized)
        report = prepare.verify_selection(self.profile, self.sdk)
        self.assertEqual(report['luci_languages'], {'ru': 'y'})
        self.assertEqual(set(report['selected_packages']), set(self.profile['packages']))
        for path, before in self.context.items():
            self.assertEqual(path.read_bytes(), before)

    def test_ordinary_sdk_uses_modules_and_reports_actual_language_selection(self):
        prepare.select_packages(self.profile, self.sdk)
        self.normalize(False)
        config = (self.sdk / '.config').read_text()
        for name in self.profile['packages']:
            self.assertIn(f'CONFIG_PACKAGE_{name}=m\n', config)
        self.assertIn('CONFIG_LUCI_LANG_ru=m\n', config)
        self.assertEqual(prepare.verify_selection(self.profile, self.sdk)['luci_languages'], {'ru': 'm'})
        for path, before in self.context.items():
            self.assertEqual(path.read_bytes(), before)

    def test_baked_language_still_requires_available_feed_language_metadata(self):
        (self.sdk / 'Config-build.in').write_text('config LUCI_LANG_ru\n\tbool\n\tdefault y\n')
        metadata = self.sdk / 'tmp/.config-package.in'
        metadata.write_text(metadata.read_text().replace('config LUCI_LANG_ru\n\ttristate "Russian"\n', ''))
        before = (self.sdk / '.config').read_bytes()
        with self.assertRaisesRegex(ValueError, 'LuCI languages absent'):
            prepare.select_packages(self.profile, self.sdk)
        self.assertEqual((self.sdk / '.config').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
