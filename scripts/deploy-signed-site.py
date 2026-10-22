#!/usr/bin/env python3
"""Import an operator-pinned, already signed Pages site; never execute its contents."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asu_client
import prepare
import publish

REPOSITORY = 'valujin/ax6000-packages'
ASSETS = ('public.tar.zst', 'public.tar.zst.sha256')
LIMIT = 1_000_000_000
SHA = re.compile(r'[0-9a-f]{64}\Z')
TAG = re.compile(r'firmware-redmi-ax6000-[0-9a-f]{12}-[0-9a-f]{12}\Z')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path, limit=8 * 1024**2):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit,
            'Missing or oversized metadata: ' + str(path))
    return json.loads(path.read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda value: require(False, 'Nonfinite JSON value: ' + value))


def release_identity(release, tag, *, package_snapshot_sha=None):
    package_tag = (isinstance(package_snapshot_sha, str) and SHA.fullmatch(package_snapshot_sha)
                   and tag == 'packages-66-' + package_snapshot_sha)
    require(isinstance(tag, str) and (TAG.fullmatch(tag) if package_snapshot_sha is None else package_tag), 'Invalid immutable release tag')
    require(isinstance(release, dict) and release.get('immutable') is True,
            'Source release must be immutable')
    require(release.get('draft') is False, 'Source release must not be a draft')
    number = release.get('id')
    require(type(number) is int and number > 0 and release.get('tag_name') == tag,
            'Release identity or tag differs')
    require(release.get('url') == f'https://api.github.com/repos/{REPOSITORY}/releases/{number}'
            and release.get('html_url') == f'https://github.com/{REPOSITORY}/releases/tag/{tag}',
            'Source release must belong to the exact repository')
    available = release.get('assets')
    require(isinstance(available, list) and all(isinstance(x, dict) for x in available),
            'Invalid release asset inventory')
    result = []
    for name in ASSETS:
        matches = [x for x in available if isinstance(x.get('name'), str)
                   and x['name'].casefold() == name.casefold()]
        require(len(matches) == 1 and matches[0]['name'] == name,
                'Release must contain exactly one canonical ' + name)
        asset = matches[0]
        require(asset.get('state') == 'uploaded' and type(asset.get('id')) is int and asset['id'] > 0,
                'Release asset is not uploaded')
        size = asset.get('size')
        maximum = LIMIT if name == ASSETS[0] else 1025
        require(type(size) is int and 0 < size < maximum, 'Release asset size limit exceeded')
        require(asset.get('browser_download_url') ==
                f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}',
                'Release asset download source differs')
        updated = asset.get('updated_at')
        require(isinstance(updated, str) and updated, 'Missing release asset timestamp')
        digest = asset.get('digest')
        require(digest is None or (isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest)),
                'Invalid release asset digest')
        result.append((asset['id'], name, size, updated, digest))
    require(result[0][0] != result[1][0], 'Release asset identities collide')
    return number, tag, False, True, tuple(result)


def safe_path(value, *, archive=False):
    require(isinstance(value, str) and len(value) <= 4096 and '\\' not in value
            and not any(ord(char) < 32 or ord(char) == 127 for char in value), 'Unsafe site path')
    if archive and value.startswith('./'):
        value = value[2:]
    if archive and value in ('', '.'):
        return None
    # Tar directory names may end with one slash.
    if archive and value.endswith('/'):
        value = value[:-1]
    path = PurePosixPath(value)
    require(path.parts and not path.is_absolute() and '..' not in path.parts
            and path.as_posix() == value and value != '.', 'Unsafe or noncanonical site path: ' + value)
    require(not {'.git', '.github'} & set(path.parts),
            'Site path would be omitted by the Pages artifact action: ' + value)
    return value


def signature(usign, public_key, message, signed):
    require(Path(message).is_file() and Path(signed).is_file(), 'Signed site message/signature missing')
    subprocess.run([str(Path(usign).resolve()), '-V', '-p', str(Path(public_key).resolve()),
                    '-m', str(message), '-x', str(signed)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)


def verify_feed(root, profile, public_key, usign):
    feed = root / 'feeds' / profile['id'] / profile['firmware']['kernel_version']
    index = feed / 'Packages'
    require(index.is_file() and index.stat().st_size <= 16 * 1024**2, 'Feed index missing or oversized')
    signature(usign, public_key, index, feed / 'Packages.sig')
    try:
        with gzip.open(feed / 'Packages.gz', 'rb') as stream:
            require(stream.read(16 * 1024**2 + 1) == index.read_bytes(), 'Compressed feed index differs')
    except (OSError, EOFError) as error:
        raise ValueError('Invalid compressed feed index') from error
    names, filenames = set(), set()
    for paragraph in index.read_text().strip().split('\n\n'):
        fields = {}; previous = None
        for line in paragraph.splitlines():
            if line.startswith((' ', '\t')) and previous:
                fields[previous] += '\n' + line
            else:
                require(':' in line, 'Malformed feed index')
                key, value = line.split(':', 1)
                require(key not in fields, 'Duplicate feed index field')
                fields[key] = value.strip(); previous = key
        publish.validate_package(fields, profile)
        name, filename = fields['Package'], fields.get('Filename', '')
        require(name not in names and filename not in filenames, 'Duplicate feed package')
        require(name not in {'kernel', 'libc', 'base-files', 'busybox', 'opkg'},
                'Protected base package in supplemental feed')
        require(re.fullmatch(r'[A-Za-z0-9_.+~\-]+\.ipk', filename), 'Unsafe feed package filename')
        package = feed / filename
        require(package.is_file() and fields.get('Size') == str(package.stat().st_size)
                and fields.get('SHA256sum') == publish.sha256_file(package), 'Feed package content differs')
        names.add(name); filenames.add(filename)
    require(names, 'Empty signed package feed')
    require({p.name for p in feed.glob('*.ipk')} == filenames, 'Unindexed feed package')
    require({p.parent for p in (root / 'feeds').rglob('Packages')} == {feed}, 'Foreign feed generation')


def verify_site(root, expected_profile, public_key, usign, snapshot_sha, release_tag):
    require(isinstance(snapshot_sha, str) and SHA.fullmatch(snapshot_sha), 'Invalid expected snapshot SHA256')
    manifest_path = root / 'snapshot-manifest.json'
    require(publish.sha256_file(manifest_path) == snapshot_sha, 'Signed snapshot SHA256 differs')
    signature(usign, public_key, manifest_path, root / 'snapshot-manifest.sig')
    manifest = read_json(manifest_path, 32 * 1024**2)
    require(isinstance(manifest, dict) and set(manifest) ==
            {'schema_version', 'cohort', 'profile_id', 'build_id', 'kernel_version', 'files'}
            and type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1
            and manifest.get('cohort') == '66', 'Invalid signed snapshot schema/cohort')
    entries = manifest['files']
    require(isinstance(entries, list) and 0 < len(entries) <= 100000, 'Invalid snapshot inventory size')
    inventory = {}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {'path', 'size', 'sha256'},
                'Malformed snapshot inventory entry')
        name = safe_path(entry['path'])
        require(name not in inventory and name not in {'snapshot-manifest.json', 'snapshot-manifest.sig'},
                'Duplicate or reserved snapshot inventory path')
        require(type(entry['size']) is int and 0 <= entry['size'] < LIMIT
                and isinstance(entry['sha256'], str) and SHA.fullmatch(entry['sha256']),
                'Invalid snapshot inventory size/checksum')
        inventory[name] = entry
    actual = {}
    for path in root.rglob('*'):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'Site contains links/special files')
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in {'snapshot-manifest.json', 'snapshot-manifest.sig'}:
                actual[relative] = path
    require(set(actual) == set(inventory), 'Signed snapshot inventory differs from actual site')
    for name, path in actual.items():
        require(path.stat().st_size == inventory[name]['size']
                and publish.sha256_file(path) == inventory[name]['sha256'], 'Snapshot file content differs: ' + name)
    profile_path = root / 'profile.json'
    profile, lock, expected = read_json(profile_path), read_json(root / 'state/lock.json'), read_json(expected_profile)
    prepare.validate(profile, lock)
    require(profile['firmware']['kernel_version'].startswith('6.6.'), 'Signed cohort66 requires kernel 6.6')
    require(profile.get('id') == expected.get('id') == 'redmi-ax6000', 'Foreign source profile id')
    for field in ('repository', 'commit', 'device', 'target', 'subtarget', 'architecture'):
        require(profile['firmware'].get(field) == expected['firmware'].get(field),
                'Imported source profile differs: ' + field)
    abi = expected['firmware'].get('kernel_version')
    require(abi is None or abi == profile['firmware']['kernel_version'], 'Imported source kernel ABI differs')
    policy = asu_client.policy(profile)
    require(policy is not None and policy['cohort'] == '66' and asu_client.package_manager(profile) == 'opkg',
            'Signed site requires ASU cohort66 and OPKG')
    build = publish.firmware_build_id(profile)
    require(release_tag == 'firmware-' + build, 'Release source generation differs from signed site')
    require(manifest['profile_id'] == profile['id'] and manifest['build_id'] == build
            and manifest['kernel_version'] == profile['firmware']['kernel_version'], 'Snapshot generation differs')
    require(lock.get('schema_version') == 1 and lock.get('profile_id') == profile['id']
            and lock.get('profile_sha256') == publish.sha256_file(profile_path)
            and lock.get('firmware') == profile['firmware'] and lock.get('sdk') == profile['sdk']
            and lock.get('feeds', {}).get('base') == profile['firmware']['commit'],
            'Signed lock source/profile identity differs')
    require(publish.public_key_payload((root / 'repository.pub').read_text()) ==
            publish.public_key_payload(Path(public_key).read_text()), 'Site public key differs from trusted key')
    verify_feed(root, profile, public_key, usign)
    signature(usign, public_key, root / 'downloads.json', root / 'downloads.sig')
    require(read_json(root / 'downloads.json') == publish.imagebuilder_download_catalog(root, profile),
            'Signed download catalog source/assets differ')
    require({p.name for p in (root / 'firmware').iterdir()} == {build}, 'Foreign firmware generation')
    return manifest


def verify_release_catalog(root, release, release_tag):
    """Bind every signed download, including the external IB, to frozen GH bytes."""
    release_identity(release, release_tag)
    catalog = read_json(root / 'downloads.json')
    for entry in catalog['assets']:
        filename = entry['filename']
        matches = [asset for asset in release['assets'] if isinstance(asset.get('name'), str)
                   and asset['name'].casefold() == filename.casefold()]
        require(len(matches) == 1 and matches[0]['name'] == filename,
                'Release must contain exactly one canonical signed catalog asset: ' + filename)
        asset = matches[0]
        require(asset.get('state') == 'uploaded' and type(asset.get('id')) is int and asset['id'] > 0,
                'Release catalog asset is not uploaded: ' + filename)
        require(type(asset.get('size')) is int and asset['size'] == entry['size'],
                'Release catalog asset size differs: ' + filename)
        require(asset.get('digest') == 'sha256:' + entry['sha256'],
                'Release catalog asset SHA256 digest differs or is absent: ' + filename)
        require(asset.get('browser_download_url') == entry['url'],
                'Release catalog asset URL differs: ' + filename)


def verify_package_update(root, manifest, context, public_key, usign):
    """Allow only the reviewed optional Tail 1.104.2 r2->r3 supplement."""
    require(isinstance(context, dict) and set(context) == {
        'predecessor_manifest', 'predecessor_signature', 'predecessor_sha256', 'predecessor_index',
        'firmware_tag', 'firmware_before', 'firmware_after'}, 'Incomplete package update evidence')
    path = Path(context['predecessor_manifest'])
    expected = context['predecessor_sha256']
    signed = Path(context['predecessor_signature'])
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 32 * 1024**2
            and signed.is_file() and not signed.is_symlink() and 0 < signed.stat().st_size <= 1024,
            'Missing, linked or oversized predecessor metadata')
    require(isinstance(expected, str) and SHA.fullmatch(expected)
            and publish.sha256_file(path) == expected, 'Signed predecessor SHA256 differs')
    signature(usign, public_key, path, context['predecessor_signature'])
    previous = read_json(path, 32 * 1024**2)
    require(isinstance(previous, dict) and set(previous) == set(manifest)
            and all(previous.get(k) == v for k, v in manifest.items() if k != 'files'),
            'Package update changes native snapshot identity')
    rows = previous.get('files')
    require(isinstance(rows, list) and 0 < len(rows) <= 100000, 'Invalid predecessor inventory')
    before = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'path', 'size', 'sha256'}, 'Malformed predecessor entry')
        name = safe_path(row['path'])
        require(name not in before and name not in {'snapshot-manifest.json', 'snapshot-manifest.sig'}
                and type(row['size']) is int and 0 <= row['size'] < LIMIT
                and isinstance(row['sha256'], str) and SHA.fullmatch(row['sha256']), 'Invalid predecessor entry')
        before[name] = row
    after = {row['path']: row for row in manifest['files']}
    profile = read_json(root / 'profile.json')
    feed = 'feeds/' + profile['id'] + '/' + profile['firmware']['kernel_version']
    arch = profile['firmware']['architecture']
    old = feed + '/tailscale_1.104.2-r2_' + arch + '.ipk'
    new = feed + '/tailscale_1.104.2-r3_' + arch + '.ipk'
    indexes = {feed + '/' + name for name in ('Packages', 'Packages.gz', 'Packages.sig')}
    require(set(before) - set(after) == {old} and set(after) - set(before) == {new}
            and {name for name in set(before) & set(after) if before[name] != after[name]} == indexes,
            'Package update must change only Tail r2->r3 and its three indexes')
    previous_index = Path(context['predecessor_index'])
    index_entry = before[feed + '/Packages']
    require(previous_index.is_file() and not previous_index.is_symlink()
            and previous_index.stat().st_size == index_entry['size'] <= 16 * 1024**2
            and publish.sha256_file(previous_index) == index_entry['sha256'], 'Signed predecessor index differs')
    def paragraphs(path):
        result = {}
        for paragraph in path.read_text().strip().split('\n\n'):
            names = [line[9:] for line in paragraph.splitlines() if line.startswith('Package: ')]
            require(len(names) == 1 and names[0] not in result, 'Duplicate or missing index package')
            result[names[0]] = paragraph
        return result
    old_rows, new_rows = paragraphs(previous_index), paragraphs(root / feed / 'Packages')
    require(set(old_rows) == set(new_rows) and 'tailscale' in new_rows
            and all(old_rows[name] == new_rows[name] for name in old_rows if name != 'tailscale'),
            'Package update changes non-Tail index metadata')
    require('\nVersion: 1.104.2-r2\n' in '\n' + old_rows['tailscale'] + '\n'
            and '\nFilename: ' + Path(old).name + '\n' in '\n' + old_rows['tailscale'] + '\n',
            'Predecessor index Tail identity differs')
    control = publish.read_control(root / new)
    require(all(control.get(k) == v for k, v in {'Package': 'tailscale', 'Version': '1.104.2-r3',
            'Architecture': arch, 'Provides': 'tailscaled'}.items()), 'Replacement Tail control/provider differs')
    publish.validate_package(control, profile)
    require(all('\n' + key + ': ' + control[key] + '\n' in '\n' + new_rows['tailscale'] + '\n'
                for key in ('Package', 'Version', 'Architecture', 'Provides')),
            'Signed Tail index/control identity differs')
    tag = context['firmware_tag']
    require(release_identity(context['firmware_before'], tag) == release_identity(context['firmware_after'], tag),
            'Original immutable firmware release changed')
    for release in (context['firmware_before'], context['firmware_after']):
        verify_release_catalog(root, release, tag)


class BoundedReader:
    """Bound tar headers/PAX metadata as well as regular-file payload bytes."""
    def __init__(self, stream, limit):
        self.stream, self.limit, self.total = stream, limit, 0

    def read(self, size=-1):
        if size < 0:
            size = self.limit - self.total + 1
        data = self.stream.read(min(size, self.limit - self.total + 1))
        self.total += len(data)
        require(self.total <= self.limit, 'Archive stream size limit exceeded')
        return data


def import_site(archive, checksum, output, expected_profile, public_key, usign,
                archive_sha, snapshot_sha, release_tag, *, max_bytes=LIMIT, release_metadata=None, package_update=None):
    archive, checksum, output = Path(archive), Path(checksum), Path(output)
    require(isinstance(archive_sha, str) and SHA.fullmatch(archive_sha), 'Invalid expected archive SHA256')
    package_mode = package_update is not None
    if package_mode:
        require(isinstance(package_update, dict) and release_tag == 'packages-66-' + snapshot_sha,
                'Package transport tag must bind exact signed snapshot')
        require(TAG.fullmatch(package_update.get('firmware_tag', '')), 'Invalid original firmware release tag')
        firmware_tag = package_update['firmware_tag']
    else:
        require(TAG.fullmatch(release_tag), 'Invalid source release tag')
        firmware_tag = release_tag
    require(not output.exists() and not output.is_symlink(), 'Import output must be fresh')
    require(archive.is_file() and not archive.is_symlink() and 0 < archive.stat().st_size < LIMIT,
            'Archive compressed size limit exceeded')
    require(checksum.is_file() and not checksum.is_symlink() and checksum.stat().st_size <= 1024
            and checksum.read_bytes() == (archive_sha + '  public.tar.zst\n').encode(),
            'Release checksum must exactly match expected public archive SHA256')
    require(publish.sha256_file(archive) == archive_sha, 'Archive SHA256 differs')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.signed-site-', dir=output.parent))
    process = None
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(['zstd', '--decompress', '--memory=128MB', '--stdout', '--', str(archive)],
                                       stdout=subprocess.PIPE, stderr=errors)
            stream = BoundedReader(process.stdout, max_bytes + 64 * 1024**2)
            seen, total = set(), 0
            with tarfile.open(fileobj=stream, mode='r|') as bundle:
                for count, member in enumerate(bundle, 1):
                    require(count <= 100000, 'Archive member limit exceeded')
                    require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE),
                            'Archive links and special files are forbidden')
                    name = safe_path(member.name, archive=True)
                    if name is None:
                        require(member.isdir() and member.size == 0, 'Archive root must be an empty directory entry')
                        continue
                    require(name not in seen, 'Archive duplicate path: ' + name)
                    seen.add(name)
                    require(member.size >= 0 and (not member.isdir() or member.size == 0), 'Invalid archive member size')
                    total += member.size
                    require(total < max_bytes, 'Archive unpacked size limit exceeded')
                    destination = temporary / name
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open('xb') as target, bundle.extractfile(member) as source:
                            shutil.copyfileobj(source, target, 1024 * 1024)
                        require(destination.stat().st_size == member.size, 'Truncated archive file')
                        destination.chmod(0o644)
            while stream.read(1024 * 1024):
                pass
            require(process.wait(timeout=20) == 0, 'Invalid zstd archive')
        manifest = verify_site(temporary, expected_profile, public_key, usign, snapshot_sha, firmware_tag)
        if package_mode:
            verify_package_update(temporary, manifest, package_update, public_key, usign)
        elif release_metadata is not None:
            verify_release_catalog(temporary, release_metadata, release_tag)
        temporary.rename(output)
        return manifest
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--release-metadata', type=Path, required=True)
    parser.add_argument('--previous-release-metadata', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--checksum', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-profile', type=Path)
    parser.add_argument('--public-key', type=Path)
    parser.add_argument('--usign', type=Path)
    parser.add_argument('--expected-archive-sha256')
    parser.add_argument('--expected-snapshot-sha256')
    parser.add_argument('--package-predecessor', type=Path)
    parser.add_argument('--package-predecessor-signature', type=Path)
    parser.add_argument('--package-predecessor-index', type=Path)
    parser.add_argument('--expected-predecessor-sha256')
    parser.add_argument('--firmware-release-tag')
    parser.add_argument('--firmware-release-metadata', type=Path)
    parser.add_argument('--previous-firmware-release-metadata', type=Path)
    args = parser.parse_args()
    package_mode = args.release_tag.startswith('packages-66-')
    if package_mode:
        require(args.expected_snapshot_sha256 and SHA.fullmatch(args.expected_snapshot_sha256)
                and args.expected_predecessor_sha256 and SHA.fullmatch(args.expected_predecessor_sha256)
                and args.firmware_release_tag and TAG.fullmatch(args.firmware_release_tag),
                'Package-only mode requires exact snapshot/predecessor pins and original firmware tag')
    else:
        require(not any((args.package_predecessor, args.package_predecessor_signature, args.package_predecessor_index,
                         args.expected_predecessor_sha256, args.firmware_release_tag,
                         args.firmware_release_metadata, args.previous_firmware_release_metadata)),
                'Package-only evidence requires explicit package transport mode')
    transport_args = {'package_snapshot_sha': args.expected_snapshot_sha256} if package_mode else {}
    release = read_json(args.release_metadata)
    identity = release_identity(release, args.release_tag, **transport_args)
    for value in (args.expected_archive_sha256, args.expected_snapshot_sha256):
        require(value is None or SHA.fullmatch(value), 'Expected SHA256 must be 64 lowercase hexadecimal characters')
    if args.expected_archive_sha256:
        asset = next(x for x in release['assets'] if x['name'] == ASSETS[0])
        require(asset.get('digest') in (None, 'sha256:' + args.expected_archive_sha256),
                'Release asset digest differs from operator-pinned archive SHA256')
    if args.previous_release_metadata:
        require(identity == release_identity(read_json(args.previous_release_metadata), args.release_tag, **transport_args),
                'Immutable release identity changed during download')
    if args.archive is not None:
        require(all((args.checksum, args.output, args.expected_profile, args.public_key, args.usign,
                     args.expected_archive_sha256, args.expected_snapshot_sha256,
                     args.previous_release_metadata)), 'Import requires all verification inputs and before/after release metadata')
        for filename, path in ((ASSETS[0], args.archive), (ASSETS[1], args.checksum)):
            asset = next(x for x in release['assets'] if x['name'] == filename)
            require(path.stat().st_size == asset['size'], 'Downloaded release asset size differs')
            require(asset.get('digest') in (None, 'sha256:' + publish.sha256_file(path)),
                    'Downloaded release asset digest differs')
        context = None
        if package_mode:
            require(all((args.package_predecessor, args.package_predecessor_signature, args.package_predecessor_index,
                         args.firmware_release_metadata, args.previous_firmware_release_metadata)),
                    'Package import requires signed predecessor and original firmware before/after metadata')
            context = {'predecessor_manifest': args.package_predecessor,
                       'predecessor_signature': args.package_predecessor_signature,
                       'predecessor_index': args.package_predecessor_index,
                       'predecessor_sha256': args.expected_predecessor_sha256,
                       'firmware_tag': args.firmware_release_tag,
                       'firmware_before': read_json(args.previous_firmware_release_metadata),
                       'firmware_after': read_json(args.firmware_release_metadata)}
        result = import_site(args.archive, args.checksum, args.output, args.expected_profile,
                             args.public_key, args.usign, args.expected_archive_sha256,
                             args.expected_snapshot_sha256, args.release_tag, release_metadata=release, package_update=context)
        print('Verified signed public site:', result['build_id'])
    else:
        print('Verified immutable release transport identity:', identity[0])


if __name__ == '__main__':
    main()
