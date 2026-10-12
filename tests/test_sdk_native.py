"""Frozen native module providers must be selected for SDK repacking, then reused exactly."""
import hashlib
import io
import json
import shutil
import sys
import tarfile
import unittest
from pathlib import Path

import test_sdk_selection as fixture
prepare = fixture.prepare
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def ipk(path, package, abi, depends='', provides='', marker=b''):
    version = abi if package == 'kernel' else '1-r1'
    text = f'Package: {package}\nVersion: {version}\nArchitecture: aarch64_cortex-a53\n'
    if package.startswith('kmod-'):
        depends = f'kernel (= {abi})' + (', ' + depends if depends else '')
    if depends:
        text += 'Depends: ' + depends + '\n'
    if provides:
        text += 'Provides: ' + provides + '\n'
    control = io.BytesIO()
    with tarfile.open(fileobj=control, mode='w:gz') as tar:
        member = tarfile.TarInfo('./control'); member.size = len(text.encode())
        tar.addfile(member, io.BytesIO(text.encode()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode='w:gz') as tar:
        for name, data in [('control.tar.gz', control.getvalue()), ('fixture-marker', marker)]:
            member = tarfile.TarInfo('./' + name); member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return path


@unittest.skipUnless(shutil.which('cc'), 'host C compiler is required')
class NativeClosureTest(unittest.TestCase):
    setUpClass = classmethod(fixture.SdkKconfigSelectionTest.setUpClass.__func__)
    normalize = fixture.SdkKconfigSelectionTest.normalize
    configure_top = fixture.SdkKconfigSelectionTest.configure_top

    def setUp(self):
        fixture.SdkKconfigSelectionTest.setUp(self)
        self.abi = '6.6.133~6ea4b96ad26e951cc0b4fa9296a521d3-r1'
        self.profile['firmware'] = {'commit': 'a' * 40, 'kernel_version': self.abi,
                                    'architecture': 'aarch64_cortex-a53'}
        self.profile['packages'] = ['kmod-amneziawg']
        self.baseline = self.sdk / 'ax6000-baseline'
        self.paths = {}

    def add_baseline(self, name, depends='', provides=''):
        path = ipk(self.baseline / 'ipks' / (name + '.ipk'), name, self.abi,
                   depends, provides, marker=b'verified frozen original')
        self.paths[name] = path
        files = [{'package': package, 'version': self.abi if package == 'kernel' else '1-r1', 'path': 'ipks/' + p.name,
                  'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                 for package, p in self.paths.items()]
        (self.baseline / 'manifest.json').write_text(json.dumps({
            'schema_version': 1, 'source_commit': 'a' * 40, 'architecture': 'aarch64_cortex-a53',
            'kernel_version': self.abi, 'frozen_kmods': sorted(n for n in self.paths if n.startswith('kmod-')),
            'frozen_packages': sorted(self.paths), 'files': files}))

    def metadata(self):
        (self.sdk / 'tmp/.packageinfo').write_text('Package: kmod-amneziawg\n')
        (self.sdk / 'tmp/.config-package.in').write_text(
            'config PACKAGE_kmod-amneziawg\n\ttristate "AmneziaWG"\n'
            '\tselect PACKAGE_kmod-combined\n'
            'config LUCI_LANG_ru\n\ttristate "Russian"\n')
        snapshot = ('config PACKAGE_kmod-amneziawg\n\tbool\n\tdefault y\n'
                    'config LUCI_LANG_ru\n\tbool\n\tdefault y\n')
        snapshot += ''.join(f'config PACKAGE_{name}\n\ttristate\n\tdefault m\n'
                            for name in self.paths)
        (self.sdk / 'Config-build.in').write_text(snapshot)
        self.context[self.sdk / 'Config-build.in'] = snapshot.encode()
        self.configure_top(True)
        prepare.select_packages(self.profile, self.sdk)
        self.normalize(True)

    def test_real_conf_restores_transitive_native_providers_and_stabilizes(self):
        self.add_baseline('kmod-combined', 'virtual-neon (>= 1) | kmod-alternative, kmod-poly1305')
        self.add_baseline('kmod-neon', 'kmod-crypto-hash', 'virtual-neon (= 1)')
        self.add_baseline('kmod-poly1305', 'kmod-crypto-hash')
        self.add_baseline('kmod-crypto-hash')
        self.add_baseline('kmod-unused-mac80211')
        self.metadata()
        before = (self.sdk / '.config').read_text()
        self.assertIn('CONFIG_PACKAGE_kmod-combined=y', before)
        self.assertNotIn('CONFIG_PACKAGE_kmod-neon=m', before)
        self.assertTrue(prepare.close_native(self.profile, self.sdk))
        self.normalize(True)
        self.assertFalse(prepare.close_native(self.profile, self.sdk))
        config = (self.sdk / '.config').read_text()
        for name in ['kmod-neon', 'kmod-poly1305', 'kmod-crypto-hash']:
            self.assertIn(f'CONFIG_PACKAGE_{name}=m\n', config)
        self.assertNotIn('CONFIG_PACKAGE_kmod-unused-mac80211=m', config)
        report = prepare.verify_selection(self.profile, self.sdk)
        self.assertEqual(set(report['required_native_kmods']), set(self.paths) - {'kmod-unused-mac80211'})
        for path, before in self.context.items():
            self.assertEqual(path.read_bytes(), before)

    def test_unresolved_native_provider_refuses_without_partial_changes(self):
        self.add_baseline('kmod-combined', 'kmod-missing (>= 1)')
        self.metadata()
        before = {p: p.read_bytes() for p in [self.sdk / '.config', self.sdk / 'Config.in',
                                              self.sdk / 'ax6000-refresh-defaults.in',
                                              self.sdk / 'build-selection.json']}
        with self.assertRaisesRegex(ValueError, 'native dependency unavailable'):
            prepare.close_native(self.profile, self.sdk)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_verify_refuses_omitted_transitive_native_provider(self):
        self.add_baseline('kmod-combined', 'kmod-neon')
        self.add_baseline('kmod-neon')
        self.metadata()
        with self.assertRaisesRegex(ValueError, 'native dependency selection'):
            prepare.verify_selection(self.profile, self.sdk)

    def test_export_prefers_exact_frozen_native_ipk_but_fresh_userland_dependency(self):
        self.add_baseline('kmod-combined')
        self.add_baseline('kernel')
        self.metadata()
        bin_dir = self.sdk / 'bin/packages'
        ipk(bin_dir / 'kernel-repacked.ipk', 'kernel', self.abi, marker=b'fresh kernel repack')
        ipk(bin_dir / 'kmod-combined-repacked.ipk', 'kmod-combined', self.abi,
            marker=b'fresh repack MUST NOT replace baseline')
        fresh_awg = ipk(bin_dir / 'kmod-amneziawg.ipk', 'kmod-amneziawg', self.abi,
                        depends='kmod-combined', marker=b'fresh AWG')
        # A userland package has a verified baseline entry and a fresh dependency build.
        old = ipk(self.baseline / 'ipks/libfixture.ipk', 'libfixture', self.abi, marker=b'old library')
        manifest_path = self.baseline / 'manifest.json'; manifest = json.loads(manifest_path.read_text())
        manifest['frozen_packages'].append('libfixture')
        manifest['files'].append({'package': 'libfixture', 'version': '1-r1',
                                 'path': 'ipks/libfixture.ipk',
                                 'sha256': hashlib.sha256(old.read_bytes()).hexdigest()})
        manifest_path.write_text(json.dumps(manifest))
        fresh_library = ipk(bin_dir / 'libfixture.ipk', 'libfixture', self.abi, marker=b'new library')
        signer = self.sdk / 'staging_dir/host/bin/usign'; signer.parent.mkdir(parents=True)
        signer.write_text('#!/bin/sh\nexit 0\n'); signer.chmod(0o755)
        output = self.sdk / 'exported'
        prepare.export_packages(self.profile, {}, self.sdk, output)
        files = output / 'bin/packages'
        self.assertEqual((files / self.paths['kmod-combined'].name).read_bytes(),
                         self.paths['kmod-combined'].read_bytes())
        self.assertFalse((files / 'kmod-combined-repacked.ipk').exists())
        self.assertFalse((files / 'kernel-repacked.ipk').exists())
        self.assertEqual((files / 'kernel.ipk').read_bytes(), self.paths['kernel'].read_bytes())
        self.assertEqual((files / fresh_awg.name).read_bytes(), fresh_awg.read_bytes())
        self.assertEqual((files / fresh_library.name).read_bytes(), fresh_library.read_bytes())


if __name__ == '__main__':
    unittest.main()
