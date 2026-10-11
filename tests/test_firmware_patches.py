"""Compatibility patches must preserve source identity and never change kernel config."""
import ctypes
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
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
