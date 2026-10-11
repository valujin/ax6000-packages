#!/usr/bin/env python3
"""Verify and import a raw build bundle without executing anything inside it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

SPEC = importlib.util.spec_from_file_location('release_prepare', Path(__file__).with_name('prepare.py'))
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def release_identity(release):
    if not isinstance(release, dict) or release.get('immutable') is not True:
        raise ValueError('The source release must be immutable')
    if release.get('draft') is not False:
        raise ValueError('Publish the draft release after all asset uploads finish')
    assets = []
    for name in ('raw.tar.zst', 'raw.tar.zst.sha256'):
        matches = [asset for asset in release.get('assets', []) if asset.get('name') == name]
        if len(matches) != 1 or matches[0].get('state') != 'uploaded':
            raise ValueError(f'Release must contain exactly one uploaded {name}')
        asset = matches[0]
        assets.append((asset['id'], asset['name'], asset['size'], asset['updated_at'], asset.get('digest')))
    return release['id'], release['tag_name'], release['draft'], release['immutable'], tuple(assets)


def read_json(path):
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError(f'Missing or oversized metadata: {path.name}')
    return json.loads(path.read_text())


def validate_metadata(raw, expected_profile):
    profile_path = raw / 'profile.json'
    profile = read_json(profile_path)
    lock = read_json(raw / 'lock.json')
    expected = read_json(expected_profile)
    prepare.validate(profile, lock)
    for field in ('repository', 'commit', 'device', 'target', 'subtarget', 'architecture'):
        if profile['firmware'].get(field) != expected['firmware'].get(field):
            raise ValueError(f'Imported firmware {field} differs from the checked-in source profile')
    if profile['id'] != expected['id']:
        raise ValueError('Imported profile id differs from source profile')
    expected_abi = expected['firmware'].get('kernel_version')
    if expected_abi is not None and profile['firmware']['kernel_version'] != expected_abi:
        raise ValueError('Imported firmware kernel ABI differs from source profile')
    if lock.get('schema_version') != 1 or lock.get('profile_id') != profile['id']:
        raise ValueError('Invalid release lock identity')
    if lock.get('profile_sha256') != hashlib.sha256(profile_path.read_bytes()).hexdigest():
        raise ValueError('Release lock does not match profile checksum')
    if lock.get('firmware') != profile['firmware']:
        raise ValueError('Release lock firmware differs from profile')
    if lock.get('sdk') != profile['sdk']:
        raise ValueError('Release lock SDK differs from profile')
    if lock.get('feeds', {}).get('base') != profile['firmware']['commit']:
        raise ValueError('Release lock base feed differs from firmware source commit')
    manifest = read_json(raw / 'firmware-manifest.json')
    if (manifest.get('source_commit') != profile['firmware']['commit'] or
            manifest.get('kernel_version') != profile['firmware']['kernel_version']):
        raise ValueError('Release firmware manifest source or kernel ABI differs from profile')


def import_bundle(archive, checksum, output, expected_profile, *, max_bytes=8 * 1024 ** 3):
    archive, checksum, output = Path(archive), Path(checksum), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Import output must be a fresh directory')
    if checksum.stat().st_size > 1024:
        raise ValueError('Invalid release checksum file')
    match = re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?raw\.tar\.zst\n?', checksum.read_text())
    if not match:
        raise ValueError('Release checksum must contain exactly one raw.tar.zst entry')
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != match[1].lower():
        raise ValueError('Release archive checksum mismatch')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.release-import-', dir=output.parent))
    process = None
    try:
        process = subprocess.Popen(['zstd', '--decompress', '--stdout', '--', str(archive)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        seen = set()
        total = 0
        with tarfile.open(fileobj=process.stdout, mode='r|') as bundle:
            for count, member in enumerate(bundle, 1):
                if count > 100000:
                    raise ValueError('Release archive exceeds member limit')
                name = PurePosixPath(member.name)
                if (name.is_absolute() or '..' in name.parts or '\\' in member.name or
                        '\x00' in member.name):
                    raise ValueError(f'Unsafe release archive path: {member.name}')
                if not member.isdir() and not member.isreg():
                    raise ValueError(f'Release archive links and special files are forbidden: {member.name}')
                if not name.parts:
                    if member.isdir():
                        continue
                    raise ValueError('Release archive root must be a directory')
                canonical = name.as_posix()
                if canonical in seen:
                    raise ValueError(f'Release archive has duplicate path: {canonical}')
                seen.add(canonical)
                total += member.size
                if member.size < 0 or total > max_bytes:
                    raise ValueError('Release archive exceeds unpacked size limit')
                destination = temporary.joinpath(*name.parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, destination.open('xb') as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                    destination.chmod(0o644)
        process.stdout.close()
        error = process.communicate()[1]
        if process.returncode:
            raise ValueError('Release zstd decompression failed: ' + error.decode(errors='replace')[:500])
        validate_metadata(temporary, Path(expected_profile))
        temporary.rename(output)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.communicate()
        if process is not None:
            process.stdout.close()
            process.stderr.close()
        if temporary.exists():
            shutil.rmtree(temporary)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--checksum', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-profile', type=Path)
    parser.add_argument('--release-metadata', type=Path)
    parser.add_argument('--previous-release-metadata', type=Path)
    args = parser.parse_args()
    try:
        if args.release_metadata:
            identity = release_identity(read_json(args.release_metadata))
            if (args.previous_release_metadata and
                    identity != release_identity(read_json(args.previous_release_metadata))):
                raise ValueError('Release changed during download; use a new immutable release')
            print('Verified immutable release and build assets')
            return
        if args.previous_release_metadata:
            parser.error('--previous-release-metadata requires --release-metadata')
        if not all((args.archive, args.checksum, args.output, args.expected_profile)):
            parser.error('import requires --archive, --checksum, --output and --expected-profile')
        import_bundle(args.archive, args.checksum, args.output, args.expected_profile)
    except (ValueError, OSError, tarfile.TarError) as error:
        parser.exit(1, f'import-release: {error}\n')
    print(f'Imported validated raw build into {args.output}')


if __name__ == '__main__':
    main()
