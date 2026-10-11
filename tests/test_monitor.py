import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/monitor.py'


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'Release monitor must exist')
        spec = importlib.util.spec_from_file_location('monitor', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_stable_manifest_selects_official_arm64_release(self):
        value = self.module.tailscale_artifact({'TarballsVersion': '1.104.1',
            'Tarballs': {'arm64': 'tailscale_1.104.1_arm64.tgz'}},
            'a' * 64 + '\n')
        self.assertEqual(value, {'version':'1.104.1', 'artifact':'static',
            'url':'https://pkgs.tailscale.com/stable/tailscale_1.104.1_arm64.tgz', 'sha256':'a' * 64})

    def test_manifest_rejects_unexpected_archive(self):
        with self.assertRaises(ValueError):
            self.module.tailscale_artifact({'TarballsVersion':'1.104.1',
                'Tarballs':{'arm64':'https://other.example/unsafe.tgz'}}, 'a' * 64)

    def test_bad_checksum_rejected(self):
        with self.assertRaises(ValueError):
            self.module.tailscale_artifact({'TarballsVersion':'1.104.1',
                'Tarballs':{'arm64':'tailscale_1.104.1_arm64.tgz'}}, 'sha256: missing')

    def test_recipe_changes_require_rebuild_even_same_upstream(self):
        self.assertTrue(self.module.changed({'recipe_sha256':'old'}, {'recipe_sha256':'new'}))

    def test_identical_lock_skips_build_regardless_key_order(self):
        self.assertFalse(self.module.changed({'a':1, 'b':2}, {'b':2, 'a':1}))


if __name__ == '__main__':
    unittest.main()
