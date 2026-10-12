"""Pinned stock-layout factory size guard, executed with native GNU make."""
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
SPEC = importlib.util.spec_from_file_location('stock_factory_prepare', ROOT / 'scripts/prepare-firmware.py')
firmware = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firmware)


class StockFactorySizeGuard(unittest.TestCase):
    def test_pinned_patch_changes_only_stock_factory_and_is_idempotent(self):
        manifest = json.loads((ROOT / 'patches/firmware/manifest.json').read_text())
        specs = [s for s in manifest['patches'] if s['path'] == 'target/linux/mediatek/image/filogic.mk']
        self.assertEqual(len(specs), 1, 'stock factory needs a reviewed 30 MiB size guard')
        spec = specs[0]
        before = gzip.decompress((ROOT / 'tests/fixtures' / spec['fixture']).read_bytes())
        self.assertEqual(hashlib.sha256(before).hexdigest(), spec['before_sha256'])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            target = source / spec['path']
            target.parent.mkdir(parents=True)
            target.write_bytes(before)
            config = source / '.config'
            config.write_bytes(b'CONFIG_ALL_KMODS=y\nCONFIG_PACKAGE_tailscale=y\n')
            firmware.apply_source_patch(source, spec, ROOT / 'patches/firmware' / spec['patch'])
            after = target.read_bytes()
            stock = re.search(rb'define Device/xiaomi_redmi-router-ax6000-stock\n.*?\nendef', before, re.S).group()
            original = b'ARTIFACT/initramfs-factory.ubi := append-image-stage initramfs-kernel.bin | ubinize-kernel'
            self.assertEqual(stock.count(original), 1)
            self.assertEqual(after, before.replace(stock, stock.replace(original, original + b' | check-size 30720k')))
            firmware.apply_source_patch(source, spec, ROOT / 'patches/firmware' / spec['patch'])
            firmware.apply_source_patch(source, spec, ROOT / 'patches/firmware' / spec['patch'], check_only=True)
            self.assertEqual(config.read_bytes(), b'CONFIG_ALL_KMODS=y\nCONFIG_PACKAGE_tailscale=y\n')
            self.assertEqual(target.read_bytes(), after)
            self.run_actual_image_check(source, after.decode())

    def run_actual_image_check(self, source, image):
        provenance = json.loads((ROOT / 'tests/fixtures/stock-factory-ec9ef10.json').read_text())
        commands = gzip.decompress((ROOT / 'tests/fixtures/image-commands-ec9ef10.mk.gz').read_bytes())
        self.assertEqual(hashlib.sha256(commands).hexdigest(), provenance['image_commands_sha256'])
        check = re.search(r'define Build/check-size\n.*?\nendef', commands.decode(), re.S).group()
        device = re.search(r'define Device/xiaomi_redmi-router-ax6000-stock\n.*?\nendef', image, re.S).group()
        makefile = source / 'Makefile'
        makefile.write_text('CONFIG_TARGET_ROOTFS_INITRAMFS:=y\n'
                           'exp_units = $(subst k, * 1024,$(subst m, * 1024k,$(subst g, * 1024m,$(1))))\n'
                           'ERROR_MESSAGE = printf "%s\\n" "$(1)" >&2\n' + device + '\n'
                           '$(eval $(call Device/xiaomi_redmi-router-ax6000-stock))\n' + check + '\n'
                           '.PHONY: factory.ubi\nfactory.ubi:\n'
                           '\t$(call Build/check-size,$(lastword $(ARTIFACT/initramfs-factory.ubi)))\n')
        artifact = source / 'factory.ubi'
        for size, survives in ((30 * 1024**2 - 1, True), (30 * 1024**2, True), (30 * 1024**2 + 1, False)):
            with artifact.open('wb') as stream:
                stream.truncate(size)
            subprocess.run(['make', '-r', '-s', '-j1', 'factory.ubi'], cwd=source,
                           check=True, capture_output=True, timeout=10)
            self.assertEqual(artifact.exists(), survives)


if __name__ == '__main__':
    unittest.main()
