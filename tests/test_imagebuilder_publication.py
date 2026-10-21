"""Native imports require the IB; daily publication retains immutable references."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import imagebuilder
import publish
SPEC = importlib.util.spec_from_file_location('ib_release', ROOT / 'scripts/import-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class NativeAndDailyPublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'raw'
        self.target = self.raw / 'bin/targets/mediatek/filogic'
        self.target.mkdir(parents=True)
        sdk = self.target / 'immortalwrt-sdk-mediatek-filogic.tar.zst'
        sdk.write_bytes(b'native sdk fixture')
        sdkhash = hashlib.sha256(sdk.read_bytes()).hexdigest()
        build = 'redmi-ax6000-' + 'a' * 12 + '-' + sdkhash[:12]
        self.profile = {'id': 'redmi-ax6000', 'asu': {'enabled': True}, 'build_id': build,
            'firmware': {'commit': 'a' * 40, 'kernel_version': '6.6.133~' + 'b' * 32 + '-r1',
                         'target': 'mediatek', 'subtarget': 'filogic', 'architecture': 'aarch64_cortex-a53',
                         'device': 'xiaomi_redmi-router-ax6000-stock'},
            'sdk': {'url': 'https://github.com/valujin/ax6000-packages/releases/download/firmware-' + build + '/' + sdk.name,
                    'sha256': sdkhash}}
        (self.target / 'immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin').write_bytes(b'sysupgrade fixture')
        self.ib = self.target / 'immortalwrt-imagebuilder-mediatek-filogic.Linux-x86_64.tar.zst'
        self.ib.write_bytes(b'actual immutable native IB fixture')
        self.profile['imagebuilder'] = {'url': 'https://github.com/valujin/ax6000-packages/releases/download/firmware-' + build + '/' + self.ib.name,
            'sha256': hashlib.sha256(self.ib.read_bytes()).hexdigest(), 'size': self.ib.stat().st_size}

    def inventory(self):
        return {'files': [{'path': p.relative_to(self.raw).as_posix(), 'size': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in self.target.iterdir()]}

    def test_native_import_requires_physical_archive_but_daily_signed_catalog_can_reference_it(self):
        release.validate_inventory(self.raw, self.profile, self.inventory())
        self.ib.unlink()
        with self.assertRaisesRegex(ValueError, 'ImageBuilder archive missing'):
            release.validate_inventory(self.raw, self.profile, self.inventory())
        output = self.root / 'daily'
        destination = publish.stage_firmware(self.raw, output, self.profile)
        self.assertFalse((destination / self.ib.name).exists())
        catalog = publish.imagebuilder_download_catalog(output, self.profile)
        self.assertEqual(catalog['imagebuilder'], self.profile['imagebuilder'])
        self.assertEqual([x for x in catalog['assets'] if x['filename'] == self.ib.name],
                         [{'filename': self.ib.name, **self.profile['imagebuilder']}])
        self.assertTrue(all('/releases/download/firmware-' + self.profile['build_id'] + '/' in x['url'] for x in catalog['assets']))

    def test_native_archive_is_included_when_present(self):
        output = self.root / 'native'
        destination = publish.stage_firmware(self.raw, output, self.profile)
        self.assertEqual((destination / self.ib.name).read_bytes(), self.ib.read_bytes())
        self.assertEqual(sum(x['filename'] == self.ib.name for x in publish.imagebuilder_download_catalog(output, self.profile)['assets']), 1)

    def test_signed_catalog_names_are_casefold_unique_with_one_uppercase_checksum(self):
        for native in (True, False):
            with self.subTest(native=native):
                if not native:
                    self.ib.unlink()
                output = self.root / ('native-canonical' if native else 'daily-canonical')
                destination = publish.stage_firmware(self.raw, output, self.profile)
                upper = destination / 'SHA256SUMS'
                lower = destination / 'sha256sums'
                self.assertEqual(upper.read_bytes(), lower.read_bytes())
                catalog = publish.imagebuilder_download_catalog(output, self.profile)
                names = [asset['filename'] for asset in catalog['assets']]
                self.assertEqual(len(names), len({name.casefold() for name in names}))
                self.assertEqual(names.count('SHA256SUMS'), 1)
                self.assertNotIn('sha256sums', names)
                checksum = next(asset for asset in catalog['assets'] if asset['filename'] == 'SHA256SUMS')
                self.assertEqual(checksum['sha256'], hashlib.sha256(upper.read_bytes()).hexdigest())
                self.assertTrue(checksum['url'].endswith('/SHA256SUMS'))
                # Native export/staging aliases remain available to existing tools.
                self.assertEqual(upper.read_bytes(), lower.read_bytes())

    def test_mismatched_checksum_aliases_rejected_without_rewriting_either_file(self):
        for same_size in (False, True):
            with self.subTest(same_size=same_size):
                output = self.root / ('different-bytes' if same_size else 'different-size')
                destination = publish.stage_firmware(self.raw, output, self.profile)
                upper = destination / 'SHA256SUMS'
                lower = destination / 'sha256sums'
                canonical = upper.read_bytes()
                changed = bytes([canonical[0] ^ 1]) + canonical[1:] if same_size else canonical + b'altered'
                lower.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, 'checksum aliases differ'):
                    publish.imagebuilder_download_catalog(output, self.profile)
                self.assertEqual(upper.read_bytes(), canonical)
                self.assertEqual(lower.read_bytes(), changed)

    def test_checksum_alias_bytes_are_checked_even_if_metadata_matches(self):
        output = self.root / 'equal-metadata'
        destination = publish.stage_firmware(self.raw, output, self.profile)
        lower = destination / 'sha256sums'
        canonical = lower.read_bytes()
        lower.write_bytes(bytes([canonical[0] ^ 1]) + canonical[1:])
        # Metadata equality alone must not authorize alias removal.
        with patch.object(publish, 'sha256_file', return_value='a' * 64):
            with self.assertRaisesRegex(ValueError, 'checksum aliases differ'):
                publish.imagebuilder_download_catalog(output, self.profile)

    def test_other_casefold_collisions_fail_closed(self):
        output = self.root / 'other-collision'
        destination = publish.stage_firmware(self.raw, output, self.profile)
        (destination / 'README').write_bytes(b'first')
        (destination / 'readme').write_bytes(b'second')
        with self.assertRaisesRegex(ValueError, 'collide ignoring case'):
            publish.imagebuilder_download_catalog(output, self.profile)

    def test_foreign_repo_tag_name_hash_size_or_unknown_extra_archive_rejected(self):
        for problem in ['repo', 'tag', 'name', 'hash', 'size', 'extra', 'legacy']:
            with self.subTest(problem=problem):
                profile = copy.deepcopy(self.profile)
                if problem == 'repo':
                    profile['imagebuilder']['url'] = profile['imagebuilder']['url'].replace('valujin/', 'foreign/')
                elif problem == 'tag':
                    profile['imagebuilder']['url'] = profile['imagebuilder']['url'].replace('firmware-' + profile['build_id'], 'latest')
                elif problem == 'name':
                    profile['imagebuilder']['url'] = profile['imagebuilder']['url'].replace(self.ib.name, 'evil-imagebuilder.tar.zst')
                elif problem == 'hash':
                    profile['imagebuilder']['sha256'] = '0' * 64
                elif problem == 'size':
                    profile['imagebuilder']['size'] += 1
                elif problem == 'extra':
                    (self.target / 'unknown-imagebuilder.tar.gz').write_bytes(b'extra')
                else:
                    del profile['asu']
                with self.assertRaises(ValueError):
                    publish.firmware_files(self.raw, profile)
                extra = self.target / 'unknown-imagebuilder.tar.gz'
                if extra.exists():
                    extra.unlink()

    def test_legacy_generations_remain_unchanged(self):
        self.ib.unlink()
        del self.profile['asu']
        del self.profile['imagebuilder']
        release.validate_inventory(self.raw, self.profile, self.inventory())
        output = self.root / 'legacy'
        publish.stage_firmware(self.raw, output, self.profile)
        self.assertIsNone(publish.imagebuilder_download_catalog(output, self.profile))


if __name__ == '__main__':
    unittest.main()
