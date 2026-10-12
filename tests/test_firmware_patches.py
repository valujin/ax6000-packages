"""Compatibility patches must preserve source identity and never change kernel config."""
import ctypes
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
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
        if spec.get('operation') == 'add':
            continue
        fixture = spec.get('fixture', Path(spec['path']).name.replace('_', '-') + '.gz')
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

    def add_patch_fixture(self):
        self.target.unlink()
        self.patch.write_text('diff --git a/driver.c b/driver.c\nnew file mode 100644\n'
                              '--- /dev/null\n+++ b/driver.c\n@@ -0,0 +1,3 @@\n'
                              '+int map(void) {\n'
                              '+\treturn iommu_map(domain, addr, addr, size, prot, GFP_KERNEL);\n'
                              '+}\n')
        self.spec.update(operation='add', before_sha256=None,
                         sha256=hashlib.sha256(self.patch.read_bytes()).hexdigest())

    def test_new_patch_file_requires_apply_and_is_idempotent_without_config_changes(self):
        self.add_patch_fixture()
        config = (self.source / '.config').read_bytes()
        with self.assertRaisesRegex(ValueError, 'not applied'):
            firmware.apply_source_patch(self.source, self.spec, self.patch, check_only=True)
        firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertEqual(self.target.read_bytes(), self.after)
        firmware.apply_source_patch(self.source, self.spec, self.patch)
        firmware.apply_source_patch(self.source, self.spec, self.patch, check_only=True)
        self.assertEqual((self.source / '.config').read_bytes(), config)

    def test_new_patch_file_refuses_unexpected_existing_file(self):
        self.add_patch_fixture()
        self.target.write_bytes(b'foreign patch\n')
        with self.assertRaisesRegex(ValueError, 'source hash'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertEqual(self.target.read_bytes(), b'foreign patch\n')

    def test_new_patch_file_refuses_symlink_target_and_parent(self):
        self.add_patch_fixture()
        self.target.symlink_to(self.source / 'missing')
        with self.assertRaisesRegex(ValueError, 'outside source|symlink'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.target.unlink()
        directory = self.source / 'real'
        directory.mkdir()
        (self.source / 'linked').symlink_to(directory, target_is_directory=True)
        self.spec['path'] = 'linked/driver.c'
        with self.assertRaisesRegex(ValueError, 'outside source|symlink'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertFalse((directory / 'driver.c').exists())

    def test_new_patch_file_refuses_path_escape_and_nonnull_before_hash(self):
        self.add_patch_fixture()
        self.spec['path'] = '../driver.c'
        with self.assertRaisesRegex(ValueError, 'outside source'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.spec['path'] = 'driver.c'
        self.spec['before_sha256'] = hashlib.sha256(self.before).hexdigest()
        with self.assertRaisesRegex(ValueError, 'before hash'):
            firmware.apply_source_patch(self.source, self.spec, self.patch)
        self.assertFalse(self.target.exists())

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

    @unittest.skipUnless(shutil.which('cc') and shutil.which('perl'), 'host C compiler and Perl are required')
    def test_openvswitch_real_metadata_and_kconfig_exclude_unsupported_intree_only(self):
        vendor_source_fixture(self.source)
        recipe = self.source / 'feeds/packages/net/openvswitch/Makefile'
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_bytes(gzip.decompress((ROOT / 'tests/fixtures/openvswitch-packages-110d325-Makefile.gz').read_bytes()))
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        config_before = (self.source / '.config').read_bytes()
        firmware.vendor_fixes(profile, self.source)
        self.assertEqual((self.source / '.config').read_bytes(), config_before)
        tools = self.source / 'fixture-tools'
        with tarfile.open(ROOT / 'tests/fixtures/openwrt-kconfig-tools-ec9ef10.tar.gz', 'r:gz') as archive:
            manifest = json.load(archive.extractfile('fixture-manifest.json'))
            self.assertEqual(manifest['source_commit'], profile['firmware']['commit'])
            for name, digest in manifest['files'].items():
                data = archive.extractfile(name).read()
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                target = tools / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        config_sources = tools / 'scripts/config'
        conf = tools / 'conf'
        subprocess.run(['cc', '-O0', '-I', str(config_sources), '-o', str(conf)] +
                       [str(config_sources / name) for name in (
                           'conf.c', 'confdata.c', 'expr.c', 'lexer.lex.c', 'menu.c',
                           'parser.tab.c', 'preprocess.c', 'symbol.c', 'util.c')],
                       check=True, capture_output=True, timeout=30)
        text = recipe.read_text()
        section = text[text.index('ovs_kmod_packages:='):text.index('# Dependency review')]
        kernel = (tools / 'include/kernel.mk').read_text()
        comparison = kernel[kernel.index('version_field='):]
        metadata = tools / 'packages.info'
        metadata.write_text('Source-Makefile: feeds/packages/net/openvswitch/Makefile\n')
        # Run the actual recipe template with the pinned kernel.mk DUMP comparators.
        # Only the final dump writer replaces OpenWrt's larger package build machinery.
        makefile = f'DUMP:=1\nKERNEL_PATCHVER:=6.6\nOUT:={metadata}\n' + comparison + section + '''
define emit
$(eval $(call KernelPackage/$(1)))
$(file >>$(OUT),Package: kmod-$(1))
$(file >>$(OUT),Submenu: $(SUBMENU))
$(file >>$(OUT),Category: $(CATEGORY))
$(file >>$(OUT),Title: $(TITLE))
$(file >>$(OUT),Depends: $(DEPENDS))
$(file >>$(OUT),Provides: $(PROVIDES))
$(file >>$(OUT),Type: ipkg)
$(file >>$(OUT),Description: $(TITLE))
$(file >>$(OUT),@@)
$(file >>$(OUT), )
endef
$(foreach p,$(ovs_kmod_packages),$(eval $(call emit,$(p))))
all:
\t@:
'''
        subprocess.run(['make', '--no-print-directory', '-f', '-'], input=makefile,
                       text=True, check=True, capture_output=True, timeout=5)
        dump = metadata.read_text()
        names = set(re.findall(r'^Package: (.+)$', dump, re.M))
        dependencies = {token.split(':')[-1].lstrip('+')
                        for line in re.findall(r'^Depends: (.*)$', dump, re.M)
                        for token in line.split() if token.startswith('+')}
        with metadata.open('a') as output:
            for name in sorted(dependencies - names):
                output.write(f'\nSource-Makefile: package/support/{name}/Makefile\n'
                             f'Package: {name}\nCategory: Kernel modules\nTitle: {name}\n'
                             f'Type: ipkg\nDescription: dependency fixture\n@@\n')
        parsed = subprocess.run(['perl', str(tools / 'scripts/package-metadata.pl'), 'config', str(metadata)],
                                text=True, check=True, capture_output=True, cwd=tools, timeout=5)
        (tools / 'packages.in').write_text(parsed.stdout)
        (tools / 'package/fixture').mkdir(parents=True)
        (tools / 'package/fixture/image-config.in').touch()
        header = 'mainmenu "Real OpenWrt metadata regression"\nconfig MODULES\n\tmodules\n\tbool\n\tdefault y\n'
        header += ''.join(f'config {symbol}\n\tbool\n\tdefault y\n'
                          for symbol in ('ALL_KMODS', 'DEVEL', 'IPV6', 'LINUX_6_6'))
        (tools / 'Kconfig').write_text(header + 'source "packages.in"\n')
        defaults = tools / 'defaults'
        defaults.write_text('CONFIG_ALL_KMODS=y\nCONFIG_LINUX_6_6=y\n')
        subprocess.run([str(conf), '--defconfig=' + str(defaults), 'Kconfig'], cwd=tools,
                       env={**os.environ, 'KCONFIG_CONFIG': str(tools / 'result.config')},
                       text=True, check=True, capture_output=True, timeout=5)
        result = (tools / 'result.config').read_text()
        selected = dict(re.findall(r'^CONFIG_([^=]+)=([my])$', result, re.M))
        for suffix in ('', '-gre', '-vxlan', '-geneve', '-lisp', '-stt'):
            self.assertNotIn('PACKAGE_kmod-openvswitch' + suffix + '-intree', selected)
        for suffix in ('', '-gre', '-vxlan', '-geneve'):
            self.assertEqual(selected.get('PACKAGE_kmod-openvswitch' + suffix), 'm')
        self.assertEqual(selected['LINUX_6_6'], 'y')
        self.assertEqual(selected['ALL_KMODS'], 'y')
        # Re-run the same real toolchain on the original pinned recipe. The only
        # normalized change must be the six unsupported package selections.
        support = metadata.read_text()[len(dump):]
        original = gzip.decompress((ROOT / 'tests/fixtures/openvswitch-packages-110d325-Makefile.gz').read_bytes()).decode()
        original_section = original[original.index('ovs_kmod_packages:='):original.index('# Dependency review')]
        metadata.write_text('Source-Makefile: feeds/packages/net/openvswitch/Makefile\n')
        subprocess.run(['make', '--no-print-directory', '-f', '-'],
                       input=makefile.replace(section, original_section), text=True,
                       check=True, capture_output=True, timeout=5)
        with metadata.open('a') as output:
            output.write(support)
        parsed = subprocess.run(['perl', str(tools / 'scripts/package-metadata.pl'), 'config', str(metadata)],
                                text=True, check=True, capture_output=True, cwd=tools, timeout=5)
        (tools / 'packages.in').write_text(parsed.stdout)
        subprocess.run([str(conf), '--defconfig=' + str(defaults), 'Kconfig'], cwd=tools,
                       env={**os.environ, 'KCONFIG_CONFIG': str(tools / 'original.config')},
                       text=True, check=True, capture_output=True, timeout=5)
        old = dict(re.findall(r'^CONFIG_([^=]+)=([my])$', (tools / 'original.config').read_text(), re.M))
        removed = {'PACKAGE_kmod-openvswitch' + suffix + '-intree'
                   for suffix in ('', '-gre', '-vxlan', '-geneve', '-lisp', '-stt')}
        self.assertEqual({name: old[name] for name in removed}, dict.fromkeys(removed, 'm'))
        self.assertEqual(set(old) - set(selected), removed)
        self.assertEqual({name: value for name, value in old.items() if name not in removed}, selected)

    def test_openvswitch_feed_pin_drift_refuses_before_applying_any_vendor_patch(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        next(feed for feed in profile['feeds'] if feed['name'] == 'packages')['commit'] = '0' * 40
        original = {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'feed.*pin'):
            firmware.vendor_fixes(profile, self.source)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    def test_scheduler_policies_and_priorities_are_preserved(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        base = self.source / 'package/mtk/applications/5g-modem/quectel_SRPD_PCIE/src'
        workers = {'sipc/sbuf.c': ('SCHED_FIFO', 10, 1),
                   'sipc/sblock.c': ('SCHED_RR', 11, 3),
                   'sipa/sipa_skb_send.c': ('SCHED_RR', 90, 1),
                   'sipa/sipa_skb_recv.c': ('SCHED_RR', 92, 1)}
        for path, (policy, priority, count) in workers.items():
            with self.subTest(worker=path):
                text = (base / path).read_text()
                self.assertEqual(len(re.findall(r'\.sched_policy\s*=\s*' + policy, text)), count)
                self.assertEqual(len(re.findall(r'\.sched_priority\s*=\s*' + str(priority) + r'\b', text)), count)
                self.assertEqual(text.count('sched_setattr_nocheck('), count)
                self.assertEqual(text.count('.size = sizeof(struct sched_attr)'), count)
                self.assertNotIn('sched_setscheduler(', text)

    def test_fibocom_sequence_generation_preserves_masks_and_fallbacks(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/fibocom_MHI/src/core/mhi_boot.c'
        self.assertTrue(target.is_file(), 'Fibocom random API compatibility patch is required')
        before = gzip.decompress((ROOT / 'tests/fixtures/mhi-boot.c.gz').read_bytes())
        patched = target.read_bytes()
        self.assertEqual(patched.count(b'get_random_u32()'), 4)
        self.assertEqual(patched.replace(b'get_random_u32()', b'prandom_u32()'), before)

    def test_fibocom_packaging_and_autoload_use_actual_module_name(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/fibocom_MHI/Makefile'
        self.assertTrue(target.is_file(), 'Fibocom module packaging patch is required')
        text = target.read_text()
        self.assertIn('FILES:=$(PKG_BUILD_DIR)/fibo_mhi.ko', text)
        self.assertIn('AUTOLOAD:=$(call AutoLoad,90,fibo_mhi)', text)
        self.assertIn('define KernelPackage/pcie_mhi_fb\n', text)
        self.assertIn('CONFLICTS:=kmod-mhi-bus\n', text,
                      'the private Fibocom bus and native MHI bus share their runtime name')

    def test_fibocom_legacy_termios_copy_preserves_extended_speeds_and_fault_atomicity(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/fibocom_MHI/src/devices/mhi_uci.c'
        text = target.read_text()
        ioctl = text[text.index('static long mhi_uci_ioctl('):text.index('static int mhi_uci_release(')]
        getter = ioctl[ioctl.index('case TCGETS:'):ioctl.index('case TCSETSF:')]
        setter = ioctl[ioctl.index('case TCSETSF:'):ioctl.index('case TIOCMSET:')]
        self.assertRegex(getter, r'copy_to_user\(\(struct termios __user \*\)arg,\s*'
                         r'&uci_dev->termios, sizeof\(struct termios\)\) \? -EFAULT : 0;')
        self.assertRegex(setter, r'struct ktermios termios = uci_dev->termios;\s*'
                         r'if \(copy_from_user\(&termios, \(struct termios __user \*\)arg,\s*'
                         r'sizeof\(struct termios\)\)\) \{\s*ret = -EFAULT;\s*break;\s*\}\s*'
                         r'uci_dev->termios = termios;\s*ret = 0;')
        self.assertNotIn('return', setter, 'copy faults must reach the shared mutex unlock')
        self.assertRegex(ioctl, r'mutex_unlock\(&uci_dev->mutex\);\s*return ret;')
        self.assertNotIn('termios_to_', ioctl, 'module cannot call unexported tty helpers')
        self.assertNotIn('case TCGETS2:', ioctl, 'legacy ioctl must not copy the extended ABI')
        self.assertNotIn('sizeof(struct ktermios)', getter + setter)

        # Exact asm-generic UAPI layout used by ARM64 Linux 6.6. Speed fields
        # are outside the legacy prefix, so neither getter nor setter copies them.
        fields = [(name, ctypes.c_uint32) for name in
                  ('c_iflag', 'c_oflag', 'c_cflag', 'c_lflag')]
        fields += [('c_line', ctypes.c_uint8), ('c_cc', ctypes.c_uint8 * 19)]
        class Termios(ctypes.Structure):
            _fields_ = fields
        class Ktermios(ctypes.Structure):
            _fields_ = fields + [('c_ispeed', ctypes.c_uint32), ('c_ospeed', ctypes.c_uint32)]
        self.assertEqual(ctypes.sizeof(Termios), 36)
        self.assertEqual(ctypes.sizeof(Ktermios), 44)
        self.assertEqual(Ktermios.c_ispeed.offset, 36)
        self.assertEqual(Ktermios.c_ospeed.offset, 40)
        for name, _ in fields:
            self.assertEqual(getattr(Termios, name).offset, getattr(Ktermios, name).offset)
        current = Ktermios(c_ispeed=115200, c_ospeed=921600)
        legacy = bytes(range(ctypes.sizeof(Termios)))
        candidate = Ktermios.from_buffer_copy(bytes(current))
        ctypes.memmove(ctypes.addressof(candidate), legacy, len(legacy))
        self.assertEqual(bytes(candidate)[:36], legacy)
        self.assertEqual((candidate.c_ispeed, candidate.c_ospeed), (115200, 921600))
        self.assertEqual(bytes(current)[:36], bytes(36), 'temporary copy must not mutate stored state')

    def test_fibocom_nonblocking_read_and_write_use_open_flags(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/fibocom_MHI/src/devices/mhi_uci.c'
        text = target.read_text()
        self.assertEqual(text.count('file->f_flags & O_NONBLOCK'), 2)
        self.assertNotIn('FMODE_NDELAY', text)
        self.assertRegex(text, r'if \(mhi_get_no_free_descriptors\(mhi_dev, DMA_TO_DEVICE\) == 0 '
                         r'&& \(file->f_flags & O_NONBLOCK\)\)\s*break;')
        self.assertRegex(text, r'if \(file->f_flags & O_NONBLOCK\)\s*return -EAGAIN;')

    def test_r8101_package_patch_preserves_original_and_selects_complete_backported_eee(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        patch = self.source / 'package/kernel/r8101/patches/110-r8101-print-link-speed-and-duplex-mode.patch'
        original_patch = gzip.decompress((ROOT / 'tests/fixtures/110-r8101-print-link-speed-and-duplex-mode.patch.gz').read_bytes())
        self.assertTrue(patch.read_bytes().startswith(original_patch), 'existing link-speed patch must remain byte-identical')
        original_patch_path = self.source / 'original-r8101.patch'
        original_patch_path.write_bytes(original_patch)
        outputs = []
        for name, applied in [('before-eee-fix', original_patch_path), ('after-eee-fix', patch)]:
            driver = self.source / name
            (driver / 'src').mkdir(parents=True)
            for path, fixture in [('r8101_n.c', 'r8101-original-n.c.gz'), ('r8101.h', 'r8101-original.h.gz')]:
                (driver / 'src' / path).write_bytes(gzip.decompress((ROOT / 'tests/fixtures' / fixture).read_bytes()))
            result = subprocess.run(['patch', '--batch', '-p1', '-i', str(applied)], cwd=driver,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            outputs.append(((driver / 'src/r8101_n.c').read_bytes(), (driver / 'src/r8101.h').read_bytes()))
        before, after = outputs
        self.assertEqual(after[1], before[1], 'the existing driver header patch must remain unchanged')
        self.assertEqual(after[0], before[0].replace(b'KERNEL_VERSION(6,9,0)', b'KERNEL_VERSION(6,6,0)'),
                         'only the EEE implementation guard and its closing comment may change')
        text = after[0].decode()
        match = re.search(r'#if LINUX_VERSION_CODE >= KERNEL_VERSION\((\d+),(\d+),(\d+)\)\s*'
                          r'static int\s*rtl_ethtool_get_eee\(struct net_device \*net, struct ethtool_keee \*edata\)', text)
        self.assertIsNotNone(match)
        threshold = tuple(map(int, match.groups()))
        self.assertGreaterEqual((6, 6, 133), threshold)
        self.assertLess((6, 5, 0), threshold)
        bitmap_branch = text[match.start():text.index('\n#else', match.start())]
        for conversion in ['mii_eee_cap1_mod_linkmode_t(edata->supported, val)',
                           'mii_eee_cap1_mod_linkmode_t(edata->advertised, val)',
                           'mii_eee_cap1_mod_linkmode_t(edata->lp_advertised, val)',
                           'linkmode_and(common, edata->advertised, edata->lp_advertised)',
                           'linkmode_to_mii_eee_cap1_t(eee->advertised)']:
            self.assertIn(conversion, bitmap_branch)
        self.assertIn('rtl_ethtool_get_eee(struct net_device *net, struct ethtool_eee *eee)', text)

    def test_selected_realtek_package_patches_keep_existing_patches_and_eee_bodies(self):
        manifest = vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        metadata = json.loads((ROOT / 'tests/fixtures/realtek-eee-sources.json').read_text())
        for package in metadata:
            with self.subTest(package=package['name']):
                spec = next(s for s in manifest['patches'] if s['patch'] == package['spec'])
                augmented = self.source / spec['path']
                previous = next((p for p in package['original_patches']
                                 if p['name'] == package['augmented_patch']), None)
                if previous:
                    original = gzip.decompress((ROOT / 'tests/fixtures' / previous['fixture']).read_bytes())
                    self.assertTrue(augmented.read_bytes().startswith(original))
                else:
                    self.assertEqual(spec['operation'], 'add')
                    self.assertIsNone(spec['before_sha256'])
                outputs = []
                for version in ['before', 'after']:
                    driver = self.source / (package['name'] + '-' + version)
                    for path, data in package['sources'].items():
                        original = gzip.decompress((ROOT / 'tests/fixtures' / data['fixture']).read_bytes())
                        self.assertEqual(hashlib.sha256(original).hexdigest(), data['sha256'])
                        target = driver / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(original)
                    patches = {}
                    for old in package['original_patches']:
                        path = self.source / (package['name'] + '-' + old['name'])
                        path.write_bytes(gzip.decompress((ROOT / 'tests/fixtures' / old['fixture']).read_bytes()))
                        patches[old['name']] = path
                    if version == 'after':
                        patches[package['augmented_patch']] = augmented
                    for _, path in sorted(patches.items()):
                        result = subprocess.run(['patch', '--batch', '-p1', '-i', str(path)], cwd=driver,
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    outputs.append({path: (driver / path).read_bytes() for path in package['sources']})
                before, after = outputs
                for path, original in before.items():
                    if package['name'] == 'r8152' and path == 'compatibility.h':
                        needle = b'\t#define ethtool_keee ethtool_eee\n'
                        expected = original.replace(needle,
                            b'#if LINUX_VERSION_CODE < KERNEL_VERSION(6,6,0)\n' + needle +
                            b'#endif /* EEE bitmap backport in this Linux 6.6 source */\n')
                        self.assertEqual(after[path].count(b'KERNEL_VERSION(6,9,0)'), 2,
                                         'unrelated outer compatibility guard must remain intact')
                    else:
                        expected = original.replace(b'KERNEL_VERSION(6,9,0)', b'KERNEL_VERSION(6,6,0)')
                    self.assertEqual(after[path], expected,
                                     'implementation bodies and original package fixes must remain unchanged')
                c_path = 'r8152.c' if package['name'] == 'r8152' else 'src/' + package['name'] + '_n.c'
                header_path = 'compatibility.h' if package['name'] == 'r8152' else 'src/' + package['name'] + '.h'
                text = after[c_path].decode()
                self.assertRegex(text, r'#if LINUX_VERSION_CODE >= KERNEL_VERSION\(6,6,0\)')
                self.assertIn('struct ethtool_keee *', text)
                self.assertIn('mii_eee_cap1_mod_linkmode_t(', text)
                self.assertIn('linkmode_and(', text)
                self.assertIn('#if LINUX_VERSION_CODE < KERNEL_VERSION(6,6,0)', after[header_path].decode())
                if package['name'] != 'r8152':
                    self.assertIn('linkmode_set_bit(ETHTOOL_LINK_MODE_100baseT_Full_BIT, eee->supported)', text)

    def test_meig_composite_module_preserves_packaged_name_and_uses_existing_source(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        base = self.source / 'package/mtk/applications/5g-modem/meig_QMI_WWAN/src'
        before = gzip.decompress((ROOT / 'tests/fixtures/meig-qmi-Makefile.gz').read_bytes())
        after = (base / 'Makefile').read_bytes()
        self.assertEqual(after.replace(b'qmi_wwan_m-y := qmi_wwan.o', b'GobiNet-objs := qmi_wwan_m.o'), before)
        self.assertIn(b'obj-m := qmi_wwan_m.o\n', after)
        self.assertIn(b'cp -f qmi_wwan_m.ko $(OUTPUTDIR)', after)
        self.assertTrue((base / 'qmi_wwan.c').is_file(), 'composite object must map to an existing source')
        self.assertFalse((base / 'qmi_wwan_m.c').exists())

    def test_meig_mac_update_preserves_condition_bits_and_other_driver_bytes(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/meig_QMI_WWAN/src/qmi_wwan.c'
        original = gzip.decompress((ROOT / 'tests/fixtures/meig-qmi-qmi-wwan.c.gz').read_bytes()).decode()
        patched = target.read_text()
        old_block = re.search(r'\tif \(possibly_iphdr\(dev->net->dev_addr\)\) \{\n.*?\n\t\}', original, re.S).group()
        new_block = re.search(r'\tif \(possibly_iphdr\(dev->net->dev_addr\)\) \{\n.*?\n\t\}', patched, re.S).group()
        restored = patched.replace(new_block, old_block)
        restored = restored.replace('4096, WWAN_PORT_QMI, &qmi_wwan_cdc_wdm_manage_power);',
                                    '4096, &qmi_wwan_cdc_wdm_manage_power);')
        self.assertEqual(restored, original)
        self.assertIn('u8 addr[ETH_ALEN];', new_block)
        self.assertIn('ether_addr_copy(addr, dev->net->dev_addr);', new_block)
        self.assertIn('eth_hw_addr_set(dev->net, addr);', new_block)
        self.assertNotRegex(new_block, r'dev->net->dev_addr\[\d+\]\s*[|&]?=')
        old_masks = tuple(int(re.search(op + r'=\s*(0x[0-9a-f]+)', old_block).group(1), 16)
                          for op in [r'\|', '&'])
        new_masks = tuple(int(re.search(op + r'=\s*(0x[0-9a-f]+)', new_block).group(1), 16)
                          for op in [r'\|', '&'])
        for first_byte in range(256):
            self.assertEqual((first_byte | old_masks[0]) & old_masks[1],
                             (first_byte | new_masks[0]) & new_masks[1])

    def test_meig_cdc_wdm_registration_uses_qmi_type_and_keeps_existing_callback(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/meig_QMI_WWAN/src/qmi_wwan.c'
        text = target.read_text()
        calls = re.findall(r'subdriver = usb_cdc_wdm_register\((.*?)\);', text, re.S)
        self.assertEqual(len(calls), 1)
        self.assertEqual(re.sub(r'\s+', ' ', calls[0]),
                         'info->control, &dev->status->desc, 4096, WWAN_PORT_QMI, &qmi_wwan_cdc_wdm_manage_power')
        self.assertIn('#include <linux/usb/cdc-wdm.h>', text)

    def test_fibocom_private_symbol_aliases_preserve_header_and_expand_kernel_exports(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        target = self.source / 'package/mtk/applications/5g-modem/fibocom_MHI/src/core/mhi.h'
        before = gzip.decompress((ROOT / 'tests/fixtures/fibocom-mhi-private-header.h.gz').read_bytes()).decode()
        text = target.read_text()
        aliases = re.findall(r'^#define (mhi_\w+) (fb_mhi_\w+)$', text, re.M)
        expected = {'mhi_alloc_controller', 'mhi_async_power_up', 'mhi_bdf_to_controller',
                    'mhi_device_get', 'mhi_device_get_sync', 'mhi_device_put',
                    'mhi_download_rddm_img', 'mhi_driver_register', 'mhi_driver_unregister',
                    'mhi_force_rddm_mode', 'mhi_get_no_free_descriptors', 'mhi_ioctl',
                    'mhi_pm_suspend', 'mhi_poll', 'mhi_power_down', 'mhi_prepare_for_power_up',
                    'mhi_prepare_for_transfer', 'mhi_sync_power_up', 'mhi_unprepare_from_transfer'}
        self.assertEqual({old for old, _ in aliases}, expected)
        self.assertEqual(len(aliases), 19)
        self.assertTrue(all(new == 'fb_' + old for old, new in aliases))
        block = re.search(r'/\* This vendor stack has private MHI types\..*?\*/\n'
                          r'(?:#define mhi_\w+ fb_mhi_\w+\n)+', text, re.S).group()
        self.assertEqual(text.replace('\n' + block, ''), before,
                         'private types, prototypes, and inline function bodies must remain byte-identical')
        self.assertGreater(text.index(block), text.index('#include <linux/miscdevice.h>'))
        compiler = shutil.which('gcc')
        self.assertIsNotNone(compiler, 'host GCC preprocessor is required to verify kernel export aliases')
        include = self.source / 'preprocessor/include/linux'
        include.mkdir(parents=True)
        (include / 'compiler.h').write_text('')
        (include / 'linkage.h').write_text('#define ASM_NL ;\n')
        (include / 'stringify.h').write_text('#define __stringify_1(x...) #x\n#define __stringify(x...) __stringify_1(x)\n')
        export = gzip.decompress((ROOT / 'tests/fixtures/linux-export-6.6.133.h.gz').read_bytes())
        self.assertEqual(hashlib.sha256(export).hexdigest(),
                         '7641f650097b4fbe1c67e26d21157c3fec9f683ebd42149b1916092f23520158')
        (include / 'export.h').write_bytes(export)
        declarations = ''.join(f'int {old}(void) {{ return 1; }}\n'
                               f'int caller_{i}(void) {{ return {old}(); }}\nEXPORT_SYMBOL({old});\n'
                               for i, (old, _) in enumerate(aliases))
        for private in [True, False]:
            result = subprocess.run([compiler, '-E', '-P', '-x', 'c', '-I', str(include.parent), '-'],
                                    input='#define CONFIG_64BIT 1\n#include <linux/export.h>\n' +
                                          (block if private else '') + declarations,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for old, new in aliases:
                symbol = new if private else old
                self.assertIn(f'int {symbol}(void)', result.stdout)
                self.assertIn(f'return {symbol}();', result.stdout)
                self.assertIn(f'__export_symbol_{symbol}:', result.stdout)
                if private:
                    self.assertNotRegex(result.stdout, r'\b' + re.escape(old) + r'\b')

    def test_gobi_and_qmi_use_native_linux66_network_apis(self):
        vendor_source_fixture(self.source)
        profile = json.loads((ROOT / 'profiles/redmi-ax6000-new.json').read_text())
        firmware.vendor_fixes(profile, self.source)
        base = self.source / 'package/mtk/applications/5g-modem'
        gobi = base / 'quectel_Gobinet/src/GobiUSBNet.c'
        self.assertTrue(gobi.is_file(), 'Gobi native Linux 6.6 API patch is required')
        text = gobi.read_text()
        self.assertIn('class_create( "GobiQMI" )', text)
        self.assertIn('netif_rx(reply)', text)
        self.assertIn('eth_hw_addr_set(pDev->net, node_id)', text)
        self.assertNotIn('pDev->net->dev_addr[0] |=', text)
        for path in ['fibocom_QMI_WWAN/src/qmi_wwan_f.c', 'quectel_QMI_WWAN/src/qmi_wwan_q.c']:
            with self.subTest(driver=path):
                text = (base / path).read_text()
                self.assertIn('u64_stats_fetch_begin(&stats64->syncp)', text)
                self.assertIn('u64_stats_fetch_retry(&stats64->syncp, start)', text)
                if path.startswith('fibocom_'):
                    self.assertIn('eth_hw_addr_set(qmap_net, real_dev->dev_addr)', text)
                else:
                    before = gzip.decompress((ROOT / 'tests/fixtures/qmi-wwan-q.c.gz').read_bytes())
                    restored = text.replace('u64_stats_fetch_begin(', 'u64_stats_fetch_begin_irq(')
                    restored = restored.replace('u64_stats_fetch_retry(', 'u64_stats_fetch_retry_irq(')
                    self.assertEqual(restored.encode(), before)
                self.assertNotIn('u64_stats_fetch_begin_irq(', text)
                self.assertNotIn('u64_stats_fetch_retry_irq(', text)


if __name__ == '__main__':
    unittest.main()
