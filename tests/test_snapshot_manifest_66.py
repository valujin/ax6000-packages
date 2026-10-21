import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publish


class SnapshotManifest66(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / 'channel'; self.tree.mkdir()
        (self.tree / 'state').mkdir()
        (self.tree / 'state/lock.json').write_bytes(b'locked actual inputs\n')
        (self.tree / '.nojekyll').touch()
        self.profile = {'id': 'redmi-ax6000', 'build_id': 'redmi-ax6000-fixture',
                        'firmware': {'kernel_version': '6.6.133~' + 'a' * 32 + '-r1'},
                        'asu': {'enabled': True}}

    @unittest.skipUnless(os.environ.get('USIGN'), 'Supply independent host usign')
    def test_signed_manifest_covers_every_leaf_and_is_not_overwritten(self):
        key, public = self.root / 'secret', self.root / 'public'
        subprocess.run([os.environ['USIGN'], '-G', '-s', str(key), '-p', str(public)],
                       check=True, capture_output=True)
        result = publish.sign_snapshot_manifest(self.tree, self.profile, key, public, os.environ['USIGN'])
        self.assertEqual(result['cohort'], '66')
        self.assertEqual(result['files'], [
            {'path': '.nojekyll', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()},
            {'path': 'state/lock.json', 'size': 21, 'sha256': hashlib.sha256(b'locked actual inputs\n').hexdigest()}])
        message = self.tree / 'snapshot-manifest.json'
        self.assertEqual(json.loads(message.read_text()), result)
        subprocess.run([os.environ['USIGN'], '-V', '-m', str(message), '-p', str(public),
                        '-x', str(self.tree / 'snapshot-manifest.sig')], check=True, capture_output=True)
        before = message.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            publish.sign_snapshot_manifest(self.tree, self.profile, key, public, os.environ['USIGN'])
        self.assertEqual(message.read_bytes(), before)

    def test_legacy_is_unchanged_and_linked_inventory_is_rejected_before_signing(self):
        self.assertIsNone(publish.sign_snapshot_manifest(self.tree, {}, None, None, None))
        (self.tree / 'escape').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            publish.sign_snapshot_manifest(self.tree, self.profile, None, None, None)
        self.assertFalse((self.tree / 'snapshot-manifest.json').exists())
