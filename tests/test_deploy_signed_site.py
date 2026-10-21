"""Signed public transport must never rebuild, re-sign, or trust imported executables."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish
SPEC = importlib.util.spec_from_file_location('deploy_signed_site', ROOT / 'scripts/deploy-signed-site.py')
site = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site)
USIGN = os.environ.get('USIGN')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def metadata(tag='firmware-redmi-ax6000-' + 'a' * 12 + '-' + 'b' * 12):
    base = 'https://github.com/valujin/ax6000-packages/releases/'
    return {'id': 42, 'url': 'https://api.github.com/repos/valujin/ax6000-packages/releases/42',
            'html_url': base + 'tag/' + tag, 'tag_name': tag, 'draft': False, 'immutable': True,
            'assets': [{'id': n, 'name': name, 'size': 123, 'state': 'uploaded',
                        'updated_at': '2026-10-21T00:00:00Z',
                        'browser_download_url': base + 'download/' + tag + '/' + name,
                        'digest': 'sha256:' + 'a' * 64}
                       for n, name in enumerate(('public.tar.zst', 'public.tar.zst.sha256'), 1)]}


class ReleaseTests(unittest.TestCase):
    def test_immutable_same_repository_identity_and_expected_hash_are_required(self):
        release = metadata()
        identity = site.release_identity(release, release['tag_name'])
        self.assertEqual(identity, site.release_identity(copy.deepcopy(release), release['tag_name']))
        for mutate in ('mutable', 'draft', 'foreign', 'asset-source', 'tag', 'duplicate', 'case-alias', 'oversized'):
            bad = copy.deepcopy(release)
            if mutate == 'mutable': bad['immutable'] = False
            if mutate == 'draft': bad['draft'] = True
            if mutate == 'foreign': bad['url'] = bad['url'].replace('valujin', 'foreign')
            if mutate == 'asset-source':
                bad['assets'][0]['browser_download_url'] = bad['assets'][0]['browser_download_url'].replace('valujin', 'foreign')
            if mutate == 'tag': bad['tag_name'] += '-other'
            if mutate == 'duplicate': bad['assets'].append(copy.deepcopy(bad['assets'][0]))
            if mutate == 'case-alias':
                bad['assets'].append(dict(bad['assets'][0], name='PUBLIC.TAR.ZST'))
            if mutate == 'oversized': bad['assets'][0]['size'] = 1_000_000_000
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                site.release_identity(bad, release['tag_name'])
        changed = copy.deepcopy(release); changed['assets'][0]['id'] += 100
        self.assertNotEqual(identity, site.release_identity(changed, release['tag_name']))


@unittest.skipUnless(USIGN and shutil.which('zstd'), 'Pinned usign and zstd required')
class SignedImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.public = self.root / 'signed'; self.public.mkdir()
        self.key = self.root / 'secret'; self.trusted = self.root / 'trusted.pub'
        subprocess.run([USIGN, '-G', '-s', str(self.key), '-p', str(self.trusted)], check=True, capture_output=True)
        self.profile = json.loads((ROOT / 'tests/fixtures/legacy66-source-profile.json').read_text())
        self.expected = self.root / 'expected.json'; self.expected.write_text(json.dumps(self.profile))
        self.profile['firmware']['kernel_version'] = '6.6.133~' + 'a' * 32 + '-r1'
        self.profile['sdk'] = {'url': 'https://example.com/sdk.tar.zst', 'sha256': 'b' * 64}
        self.profile['build_id'] = 'redmi-ax6000-' + self.profile['firmware']['commit'][:12] + '-' + 'b' * 12
        self.tag = 'firmware-' + self.profile['build_id']
        base = 'https://github.com/valujin/ax6000-packages/releases/download/' + self.tag + '/'
        self.profile['imagebuilder'] = {'url': base + 'immortalwrt-imagebuilder-mediatek-filogic.Linux-x86_64.tar.zst',
                                      'sha256': 'c' * 64, 'size': 913_000_000}
        self.profile['asu'] = {'enabled': True, 'cohort': '66', 'server_url': 'https://asu.ezon.by/66', 'seed': 'd' * 64}
        self.profile['feeds'].insert(0, {'name': 'base', 'url': self.profile['firmware']['repository'],
                                      'commit': self.profile['firmware']['commit']})
        self.feed = self.public / 'feeds/redmi-ax6000' / self.profile['firmware']['kernel_version']
        self.feed.mkdir(parents=True)
        payload = b'package bytes fixture'
        (self.feed / 'fixture.ipk').write_bytes(payload)
        index = ('Package: fixture\nVersion: 1.0-r1\nArchitecture: all\nFilename: fixture.ipk\n'
                 f'Size: {len(payload)}\nSHA256sum: {sha(payload)}\n\n').encode()
        (self.feed / 'Packages').write_bytes(index)
        import gzip
        (self.feed / 'Packages.gz').write_bytes(gzip.compress(index, mtime=0))
        self.sign(self.feed / 'Packages', self.feed / 'Packages.sig')
        (self.public / 'repository.pub').write_bytes(self.trusted.read_bytes())
        firmware = self.public / 'firmware' / self.profile['build_id']; firmware.mkdir(parents=True)
        (firmware / 'fixture-sysupgrade.bin').write_bytes(b'firmware fixture')
        (self.public / '.nojekyll').touch()
        (self.public / 'index.html').write_text('fixture')
        self.refresh_metadata()

    def sign(self, path, signature):
        signature.unlink(missing_ok=True)
        subprocess.run([USIGN, '-S', '-m', str(path), '-s', str(self.key), '-x', str(signature)],
                       check=True, capture_output=True)

    def refresh_metadata(self):
        encoded = (json.dumps(self.profile) + '\n').encode()
        (self.public / 'profile.json').write_bytes(encoded)
        lock = {'schema_version': 1, 'profile_id': self.profile['id'], 'profile_sha256': sha(encoded),
                'firmware': self.profile['firmware'], 'sdk': self.profile['sdk'],
                'feeds': {f['name']: f['commit'] for f in self.profile['feeds']}}
        (self.public / 'state').mkdir(exist_ok=True)
        (self.public / 'state/lock.json').write_text(json.dumps(lock))
        catalog = publish.imagebuilder_download_catalog(self.public, self.profile)
        (self.public / 'downloads.json').write_text(json.dumps(catalog))
        self.sign(self.public / 'downloads.json', self.public / 'downloads.sig')
        self.snapshot()

    def snapshot(self):
        for name in ('snapshot-manifest.json', 'snapshot-manifest.sig'):
            (self.public / name).unlink(missing_ok=True)
        publish.sign_snapshot_manifest(self.public, self.profile, self.key, self.trusted, USIGN)
        self.snapshot_sha = sha((self.public / 'snapshot-manifest.json').read_bytes())

    def bundle(self, extras=()):
        plain = self.root / 'public.tar'
        with tarfile.open(plain, 'w') as archive:
            for path in sorted(self.public.rglob('*')):
                if path.is_file(): archive.add(path, arcname=path.relative_to(self.public).as_posix(), recursive=False)
            for member in extras:
                archive.addfile(member, io.BytesIO(b'x' * member.size) if member.isfile() else None)
        packed = self.root / 'public.tar.zst'
        subprocess.run(['zstd', '-q', '-f', str(plain), '-o', str(packed)], check=True)
        digest = sha(packed.read_bytes())
        checksum = self.root / 'public.tar.zst.sha256'; checksum.write_text(digest + '  public.tar.zst\n')
        return packed, checksum, digest

    def run_import(self, extra=(), **kwargs):
        archive, checksum, archive_sha = self.bundle(extra)
        return site.import_site(archive, checksum, self.root / 'result', self.expected,
                                self.trusted, USIGN, archive_sha, self.snapshot_sha, self.tag, **kwargs)

    def test_valid_complete_site_preserves_bytes_and_external_imagebuilder(self):
        (self.public / 'usign').write_text('#!/bin/sh\ntouch SHOULD_NOT_EXECUTE\nexit 99\n')
        self.snapshot(); self.run_import()
        for path in self.public.rglob('*'):
            if path.is_file():
                copy_path = self.root / 'result' / path.relative_to(self.public)
                self.assertEqual(copy_path.read_bytes(), path.read_bytes())
                self.assertEqual(copy_path.stat().st_mode & 0o111, 0)
        self.assertFalse((self.root / 'SHOULD_NOT_EXECUTE').exists())
        self.assertFalse(list((self.root / 'result').rglob('*imagebuilder*.tar.zst')))

    def test_signature_inventory_and_manifest_pin_fail_closed(self):
        for problem in ('signature', 'changed', 'extra', 'missing', 'manifest-sha'):
            with self.subTest(problem=problem):
                original = {p: p.read_bytes() for p in self.public.rglob('*') if p.is_file()}
                pin = self.snapshot_sha
                if problem == 'signature': (self.public / 'snapshot-manifest.sig').write_text('invalid')
                if problem == 'changed': (self.feed / 'fixture.ipk').write_bytes(b'changed')
                if problem == 'extra': (self.public / 'unlisted').write_bytes(b'extra')
                if problem == 'missing': (self.feed / 'fixture.ipk').unlink()
                if problem == 'manifest-sha': self.snapshot_sha = '0' * 64
                with self.assertRaises((ValueError, subprocess.CalledProcessError)): self.run_import()
                self.assertFalse((self.root / 'result').exists())
                for path in self.public.rglob('*'):
                    if path.is_file() and path not in original: path.unlink()
                for path, data in original.items(): path.write_bytes(data)
                self.snapshot_sha = pin

    def test_signed_feed_signature_gzip_and_package_reference_must_match(self):
        for problem in ('signature', 'gzip', 'package'):
            with self.subTest(problem=problem):
                target = self.feed / {'signature': 'Packages.sig', 'gzip': 'Packages.gz', 'package': 'fixture.ipk'}[problem]
                original = target.read_bytes(); target.write_bytes(b'changed'); self.snapshot()
                with self.assertRaises((ValueError, subprocess.CalledProcessError)): self.run_import()
                self.assertFalse((self.root / 'result').exists()); target.write_bytes(original)

    def test_even_authentic_signed_foreign_source_or_cohort_is_rejected(self):
        for problem in ('source', 'cohort', 'manager', 'lock'):
            with self.subTest(problem=problem):
                if problem == 'source':
                    expected = json.loads(self.expected.read_text()); expected['firmware']['commit'] = 'f' * 40
                    self.expected.write_text(json.dumps(expected))
                if problem == 'cohort': self.profile['asu']['cohort'] = '612-latest'
                if problem == 'manager': self.profile['package_manager'] = 'apk'
                if problem in ('cohort', 'manager'): self.refresh_metadata()
                if problem == 'lock':
                    lock = self.public / 'state/lock.json'; obj = json.loads(lock.read_text())
                    obj['profile_sha256'] = 'f' * 64; lock.write_text(json.dumps(obj)); self.snapshot()
                with self.assertRaises(ValueError): self.run_import()
                self.assertFalse((self.root / 'result').exists())
                self.profile.pop('package_manager', None); self.profile['asu']['cohort'] = '66'
                expected = copy.deepcopy(self.profile); expected['firmware']['kernel_version'] = None
                self.expected.write_text(json.dumps(expected)); self.refresh_metadata()

    def test_links_specials_escapes_alias_duplicates_and_size_limit_rejected(self):
        for name, kind in (('../escaped', tarfile.REGTYPE), ('/escaped', tarfile.REGTYPE),
                           ('x\\escape', tarfile.REGTYPE), ('./profile.json', tarfile.REGTYPE),
                           ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE),
                           ('device', tarfile.CHRTYPE), ('fifo', tarfile.FIFOTYPE),
                           ('.github/omitted', tarfile.REGTYPE)):
            member = tarfile.TarInfo(name); member.type = kind; member.linkname = 'profile.json'
            member.size = 1 if member.isfile() else 0
            with self.subTest(name=name), self.assertRaises(ValueError): self.run_import([member])
            self.assertFalse((self.root / 'result').exists())
        with self.assertRaisesRegex(ValueError, 'size limit'): self.run_import(max_bytes=20)

    def test_exact_checksum_and_pinned_archive_are_required(self):
        archive, checksum, digest = self.bundle()
        for text, expected in ((digest + '  other.tar.zst\n', digest),
                               (digest + '  public.tar.zst\n', 'f' * 64),
                               (digest.upper() + '  public.tar.zst\n', digest)):
            checksum.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                site.import_site(archive, checksum, self.root / 'result', self.expected,
                                 self.trusted, USIGN, expected, self.snapshot_sha, self.tag)

    def test_authentic_malformed_inventory_and_changed_catalog_are_rejected(self):
        original = (self.public / 'snapshot-manifest.json').read_bytes()
        for problem in ('duplicate', 'traversal', 'size-bool', 'unknown', 'duplicate-json'):
            manifest = json.loads(original)
            if problem == 'duplicate': manifest['files'].append(copy.deepcopy(manifest['files'][0]))
            if problem == 'traversal': manifest['files'][0]['path'] = '../escaped'
            if problem == 'size-bool': manifest['files'][0]['size'] = True
            if problem == 'unknown': manifest['unexpected'] = 1
            message = self.public / 'snapshot-manifest.json'
            text = json.dumps(manifest)
            if problem == 'duplicate-json': text = text.replace('"cohort": "66"', '"cohort": "66", "cohort": "66"')
            message.write_text(text); self.sign(message, self.public / 'snapshot-manifest.sig')
            self.snapshot_sha = sha(message.read_bytes())
            with self.subTest(problem=problem), self.assertRaises(ValueError): self.run_import()
            self.assertFalse((self.root / 'result').exists())
        self.snapshot()
        catalog = self.public / 'downloads.json'; data = json.loads(catalog.read_text())
        data['assets'][0]['url'] = 'https://github.com/foreign/repository/releases/download/other/file.bin'
        catalog.write_text(json.dumps(data)); self.sign(catalog, self.public / 'downloads.sig'); self.snapshot()
        with self.assertRaisesRegex(ValueError, 'catalog'): self.run_import()

    def test_cli_checks_before_after_metadata_and_preserves_verified_site(self):
        archive, checksum, digest = self.bundle()
        release = metadata(self.tag)
        for asset, path in zip(release['assets'], (archive, checksum)):
            asset['size'] = path.stat().st_size; asset['digest'] = 'sha256:' + sha(path.read_bytes())
        catalog = json.loads((self.public / 'downloads.json').read_text())
        for number, asset in enumerate(catalog['assets'], 100):
            release['assets'].append({'id': number, 'name': asset['filename'], 'size': asset['size'],
                                      'state': 'uploaded', 'updated_at': '2026-10-21T00:00:00Z',
                                      'browser_download_url': asset['url'], 'digest': 'sha256:' + asset['sha256']})
        before, after = self.root / 'before.json', self.root / 'after.json'
        before.write_text(json.dumps(release)); after.write_text(json.dumps(release))
        command = [sys.executable, str(ROOT / 'scripts/deploy-signed-site.py'),
                   '--release-tag', self.tag, '--release-metadata', str(after),
                   '--previous-release-metadata', str(before), '--archive', str(archive),
                   '--checksum', str(checksum), '--output', str(self.root / 'result'),
                   '--expected-profile', str(self.expected), '--public-key', str(self.trusted),
                   '--usign', USIGN, '--expected-archive-sha256', digest,
                   '--expected-snapshot-sha256', self.snapshot_sha]
        release['assets'][0]['id'] += 100; after.write_text(json.dumps(release))
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0); self.assertIn('identity changed', result.stderr)
        self.assertFalse((self.root / 'result').exists())
        after.write_bytes(before.read_bytes())
        complete = before.read_bytes()
        missing = json.loads(complete)
        missing['assets'] = [x for x in missing['assets'] if 'imagebuilder' not in x['name']]
        before.write_text(json.dumps(missing)); after.write_text(json.dumps(missing))
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0); self.assertIn('canonical signed catalog asset', result.stderr)
        self.assertFalse((self.root / 'result').exists())
        before.write_bytes(complete); after.write_bytes(complete)
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'result/profile.json').read_bytes(), (self.public / 'profile.json').read_bytes())

    def test_release_must_contain_exact_external_imagebuilder_and_all_catalog_assets(self):
        release = metadata(self.tag)
        catalog = json.loads((self.public / 'downloads.json').read_text())
        for number, asset in enumerate(catalog['assets'], 100):
            release['assets'].append({'id': number, 'name': asset['filename'], 'size': asset['size'],
                                      'state': 'uploaded', 'updated_at': '2026-10-21T00:00:00Z',
                                      'browser_download_url': asset['url'], 'digest': 'sha256:' + asset['sha256']})
        archive, checksum, archive_sha = self.bundle()
        for problem in ('missing-ib', 'digest', 'size', 'case-alias', 'case-only', 'missing-firmware', 'no-digest'):
            wrong = copy.deepcopy(release)
            ib = next(x for x in wrong['assets'] if 'imagebuilder' in x['name'])
            if problem == 'missing-ib': wrong['assets'].remove(ib)
            if problem == 'digest': ib['digest'] = 'sha256:' + 'f' * 64
            if problem == 'size': ib['size'] += 1
            if problem == 'case-alias': wrong['assets'].append(dict(ib, name=ib['name'].upper(), id=999))
            if problem == 'case-only': ib['name'] = ib['name'].upper()
            if problem == 'missing-firmware':
                wrong['assets'] = [x for x in wrong['assets'] if x['name'] != 'fixture-sysupgrade.bin']
            if problem == 'no-digest': ib.pop('digest')
            with self.subTest(problem=problem), self.assertRaisesRegex(ValueError, 'catalog|Release'):
                site.import_site(archive, checksum, self.root / 'result', self.expected, self.trusted, USIGN,
                                 archive_sha, self.snapshot_sha, self.tag, release_metadata=wrong)
            self.assertFalse((self.root / 'result').exists())
        site.import_site(archive, checksum, self.root / 'result', self.expected, self.trusted, USIGN,
                         archive_sha, self.snapshot_sha, self.tag, release_metadata=release)
        self.assertTrue((self.root / 'result/downloads.json').is_file())

    def test_foreign_trust_key_and_signed_feed_kernel_abi_are_rejected(self):
        wrong_key, wrong_public = self.root / 'wrong-secret', self.root / 'wrong.pub'
        subprocess.run([USIGN, '-G', '-s', str(wrong_key), '-p', str(wrong_public)],
                       check=True, capture_output=True)
        with self.assertRaises(subprocess.CalledProcessError):
            site.verify_site(self.public, self.expected, wrong_public, USIGN, self.snapshot_sha, self.tag)
        index = self.feed / 'Packages'
        text = index.read_text().replace('Package: fixture', 'Package: kmod-fixture')
        text = text.replace('Architecture: all', 'Architecture: aarch64_cortex-a53\nDepends: kernel (= 6.6.1~wrong-r1)')
        index.write_text(text)
        import gzip
        (self.feed / 'Packages.gz').write_bytes(gzip.compress(index.read_bytes(), mtime=0))
        self.sign(index, self.feed / 'Packages.sig'); self.snapshot()
        with self.assertRaisesRegex(ValueError, 'kernel ABI mismatch'): self.run_import()


class WorkflowTests(unittest.TestCase):
    def test_manual_default_branch_atomic_pages_without_private_key_or_build(self):
        import re
        text = (ROOT / '.github/workflows/deploy-signed-site.yml').read_text()
        self.assertIn('on:\n  workflow_dispatch:\n    inputs:\n', text)
        self.assertNotRegex(text, r'^  (?:push|pull_request|schedule|workflow_run):')
        self.assertEqual(set(re.findall(r'^      ([a-z_0-9]+):$', text, re.MULTILINE)),
                         {'release_tag', 'archive_sha256', 'snapshot_sha256'})
        self.assertIn('  group: package-repository\n  cancel-in-progress: false', text)
        self.assertIn('github.event.repository.default_branch', text)
        self.assertIn('  deploy:\n    needs: verify\n', text)
        for action in re.findall(r'uses: (\S+)', text):
            self.assertRegex(action, r'^[^@]+@[0-9a-f]{40}$')
        self.assertNotIn('secrets.', text)
        self.assertNotIn('scripts/publish.py', text)
        self.assertIn('--previous-release-metadata', text)


if __name__ == '__main__': unittest.main()
