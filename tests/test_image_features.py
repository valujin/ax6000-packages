"""Optional image checks use reviewed sources and reject actual payload drift."""
import copy
import gzip
from unittest.mock import patch
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import asu_client
import image_features as features
import pbr_sources
import vendor_country
from test_pbr_sources import archive


class ImageFeatureTests(unittest.TestCase):
    def test_native_asu_metadata_identity_runtime_and_no_auto_manager_migration(self):
        for cohort, manager in [('66', 'opkg'), ('612-recommended', 'apk'), ('612-latest', 'apk')]:
            profile = {'package_manager': manager, 'asu': {'enabled': True, 'cohort': cohort,
                       'seed': 'a' * 64, 'server_url': asu_client.URLS[cohort]}}
            expected = features.expected(profile, {})
            files = {path: value[0] for path, value in expected['files'].items()}
            files['www/luci-static/resources/i18n/attendedsysupgrade.ru.lmo'] = b'compiled LMO fixture' * 2
            installed = {name: {'Version': version} for name, version in expected['versions'].items()}
            installed['luci-app-attendedsysupgrade'] = {'Version': asu_client.APP_VERSION + '-r' + asu_client.release(profile)}
            installed['luci-i18n-attendedsysupgrade-ru'] = {'Version': '26.123'}
            def verify():
                return features.verify(profile, {}, installed, lambda path, cap: files[path], lambda path, mode: None)
            self.assertEqual(verify()['package_manager'], manager)
            local = json.loads(files['etc/asu-cohort.json'])
            self.assertEqual(local['package_manager'], manager)
            original = files['etc/asu-cohort.json']
            files['etc/asu-cohort.json'] = original.replace(b'"seed": "a', b'"seed": "b')
            with self.assertRaisesRegex(ValueError, 'feature bytes'):
                verify()
            files['etc/asu-cohort.json'] = original
            installed['attendedsysupgrade-common']['Version'] = 'old'
            with self.assertRaisesRegex(ValueError, 'package version'):
                verify()

    def test_locked_author_archive_drives_exact_pbr_runtime_and_version_substitution(self):
        selected = ['etc/init.d/pbr', 'etc/config/pbr', 'etc/uci-defaults/90-pbr',
                    'usr/share/pbr/.keep', 'usr/share/pbr/pbr.user.dnsprefetch',
                    'usr/share/pbr/pbr.user.aws.uc', 'usr/share/pbr/pbr.user.netflix.uc']
        selected += ['lib/pbr/' + name + '.uc' for name in
                     ('pbr', 'pkg', 'cli', 'sys', 'validators', 'output', 'config', 'platform', 'network', 'nft')]
        extra = {'files/' + path: 'runtime ' + path + '\n' for path in selected}
        extra['files/etc/init.d/pbr'] = "readonly initCompat='38'\n"
        extra['files/lib/pbr/pkg.uc'] = "const pkg = {compat: '38',\n\tversion: 'dev-test',\n};\n"
        sources = {'pbr': archive('pbr', 'a' * 40, extra=extra),
                   'luci-app-pbr': archive('luci-app-pbr', 'b' * 40)}
        pair = {'version': '1.2.4', 'release': 2, 'translations': {}, 'packages': {}}
        for name, char in [('pbr', 'a'), ('luci-app-pbr', 'b')]:
            pair['packages'][name] = {'repository': 'https://github.com/mossdef-org/' + name + '.git',
                'commit': char * 40, 'url': pbr_sources.archive_url(name, char * 40),
                'sha256': hashlib.sha256(sources[name]).hexdigest()}
        profile = {'pbr_source': 'mossdef-stable', 'packages': ['pbr', 'luci-app-pbr'],
                   'asu': {'enabled': True, 'cohort': '66', 'server_url': 'https://asu.ezon.by/66', 'seed': 'a' * 64},
                   'vendor_country_db': True, 'firmware': {'commit': 'ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8'}}
        lock = {'pbr': pair}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            for name, meta in pair['packages'].items():
                pbr_sources.cache_archive(source, name, meta, sources[name])
            with patch.object(features, 'minified_js', side_effect=lambda data, source: b'minified fixture:' + data) as minifier:
                expected = features.expected(profile, lock, source)
                self.assertIs(minifier.call_args[0][1], source)
            files = {path: data for path, (data, _) in expected['files'].items()}
            self.assertIn(b"version: '1.2.4-r2'", files['lib/pbr/pkg.uc'])
            files['usr/share/luci-app-mtwifi-cfg/wireless-mtk.js'] = vendor_country._patched_js(gzip.decompress(
                (ROOT / 'tests/fixtures/vendor-country/66-wireless-mtk.js.gz').read_bytes()))
            files['www/luci-static/resources/i18n/attendedsysupgrade.ru.lmo'] = b'compiled LMO fixture' * 2
            installed = {name: {'Version': version} for name, version in expected['versions'].items()}
            installed['luci-app-attendedsysupgrade'] = {'Version': asu_client.APP_VERSION + '-r' + asu_client.RELEASE}
            installed['luci-i18n-attendedsysupgrade-ru'] = {'Version': '26.123'}
            def verify():
                with patch.object(features, 'minified_js', side_effect=lambda data, source: b'minified fixture:' + data):
                    return features.verify(profile, lock, installed, lambda path, cap: files[path], lambda path, mode: None, source)
            self.assertGreaterEqual(len(verify()['files']), 18)
            files['lib/pbr/pkg.uc'] += b'corrupted runtime'
            with self.assertRaisesRegex(ValueError, 'feature bytes'):
                verify()
            cached = pbr_sources.cached_archive_path(source, 'pbr', pair['packages']['pbr'])
            cached.chmod(0o644); cached.write_bytes(cached.read_bytes() + b'bad archive')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                features.expected(profile, lock, source)


if __name__ == '__main__':
    unittest.main()
