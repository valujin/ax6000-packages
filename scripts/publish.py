#!/usr/bin/env python3
"""Validate IPKs and publish an opkg feed. Never rewrite kernel dependencies."""
import argparse
import base64
import gzip
import hashlib
import html
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import urlsplit


def sha256_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def firmware_build_id(profile):
    commit = profile['firmware']['commit']
    if not re.fullmatch(r'[a-fA-F0-9]{40}', commit):
        raise ValueError('Invalid firmware source commit')
    prefix = profile['id'] + '-' + commit[:12]
    build_id = profile.get('build_id', prefix)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', build_id):
        raise ValueError('Invalid firmware build id')
    if 'build_id' in profile:
        digest = profile.get('sdk', {}).get('sha256', '')
        if not re.fullmatch(r'[a-f0-9]{64}', digest) or build_id != prefix + '-' + digest[:12]:
            raise ValueError('Firmware build id must identify the exact SDK checksum')
    return build_id


def read_control(path):
    # OpenWrt ipkg-build uses a gzipped tar, rather than Debian's ar container.
    with tarfile.open(path, 'r:*') as package:
        members = [m for m in package.getmembers() if m.name.lstrip('./') == 'control.tar.gz']
        if len(members) != 1 or not members[0].isfile() or members[0].size > 4 * 1024 * 1024:
            raise ValueError(f'Invalid control archive: {path}')
        data = package.extractfile(members[0]).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.name.lstrip('./') == 'control']
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
            raise ValueError(f'Invalid control: {path}')
        text = archive.extractfile(members[0]).read().decode('utf-8')
    fields = {}
    previous = None
    for line in text.splitlines():
        if line.startswith((' ', '\t')) and previous:
            fields[previous] += '\n' + line
        elif ':' in line:
            key, value = line.split(':', 1)
            if key in fields:
                raise ValueError(f'Duplicate control field: {key}')
            fields[key] = value.strip()
            previous = key
        elif line:
            raise ValueError('Malformed package control')
    return fields


def validate_package(control, profile):
    for field in ('Package', 'Version', 'Architecture'):
        if not control.get(field):
            raise ValueError(f'Missing {field}')
    if not re.fullmatch(r'[A-Za-z0-9_.+\-]+', control['Package']):
        raise ValueError('Invalid package name')
    firmware = profile['firmware']
    if control['Architecture'] not in (firmware['architecture'], 'all', 'noarch'):
        raise ValueError(f"Wrong architecture for {control['Package']}: {control['Architecture']}")
    deps = control.get('Depends', '')
    kernel = re.findall(r'(?:^|,)\s*kernel\s*\(\s*=\s*([^\s)]+)\s*\)', deps)
    if control['Package'].startswith('kmod-') or re.search(r'(?:^|,)\s*kernel\b', deps):
        expected = firmware['kernel_version']
        if kernel != [expected]:
            raise ValueError(f"kernel ABI mismatch for {control['Package']}: {kernel!r}; expected {expected}")
        if control['Architecture'] != firmware['architecture']:
            raise ValueError('Kernel modules require target architecture')


def stage_feed(raw, output, profile):
    if profile.get('enabled') is not True:
        raise ValueError('Profile disabled; supply matching firmware SDK first')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', profile['id']):
        raise ValueError('Invalid profile id')
    expected = profile['firmware']['kernel_version']
    if not re.fullmatch(r'[A-Za-z0-9.~+_-]+', expected):
        raise ValueError('Invalid kernel version')
    validated = []
    names = set()
    for path in sorted(Path(raw).rglob('*.ipk')):
        control = read_control(path)
        # A supplemental feed must never offer to replace the firmware/kernel/C library.
        if control.get('Package') in ('kernel', 'libc', 'base-files', 'busybox', 'opkg'):
            continue
        validate_package(control, profile)
        if control['Package'] in names:
            raise ValueError(f"Duplicate package: {control['Package']}")
        names.add(control['Package'])
        if not re.fullmatch(r'[A-Za-z0-9_.+~\-]+\.ipk', path.name):
            raise ValueError(f'Invalid IPK filename: {path.name}')
        validated.append((path, control))
    if not validated:
        raise ValueError('Build has no packages (empty feed)')
    # Validate every archive before touching output; deployment is handled atomically by Pages.
    feed = Path(output) / 'feeds' / profile['id'] / expected
    if feed.exists():
        raise ValueError('Output feed already exists; use a fresh staging directory')
    feed.mkdir(parents=True)
    paragraphs = []
    for path, control in validated:
        target = feed / path.name
        shutil.copy2(path, target)
        fields = {k: v for k, v in control.items() if k not in ('Filename', 'Size', 'SHA256sum')}
        fields.update(Filename=path.name, Size=str(target.stat().st_size),
                      SHA256sum=sha256_file(target))
        paragraphs.append('\n'.join(f'{key}: {value}' for key, value in fields.items()))
    index = ('\n\n'.join(paragraphs) + '\n\n').encode()
    (feed / 'Packages').write_bytes(index)
    (feed / 'Packages.gz').write_bytes(gzip.compress(index, mtime=0))
    return feed


def firmware_files(raw, profile):
    """Validate the entire firmware plan before any feed/output is created."""
    raw = Path(raw)
    policy = profile.get('package_only', [])
    if not isinstance(policy, list):
        raise ValueError('Invalid firmware package_only policy')
    factory_required = 'tailscale' in policy
    if not (Path(raw) / 'bin').is_dir():
        if factory_required:
            raise ValueError('Stock factory UBI is required for package_only Tailscale')
        return None
    targets = Path(raw) / 'bin' / 'targets' / profile['firmware']['target'] / profile['firmware']['subtarget']
    if not targets.is_dir():
        if factory_required:
            raise ValueError('Stock factory UBI is required for package_only Tailscale')
        return None
    if targets.is_symlink() or not targets.resolve().is_relative_to(raw.resolve()) or any(
            p.is_symlink() for p in targets.parents if p.is_relative_to(raw)):
        raise ValueError('Firmware target directory is outside raw or a symlink')
    build_id = firmware_build_id(profile)
    sdk = profile.get('sdk', {})
    sdk_name = Path(urlsplit(sdk.get('url', '')).path).name
    # RAM BL2 variants and generic kernel components share BIN_DIR with device
    # images. Offer only complete stock sysupgrade/recovery images here.
    device = profile['firmware'].get('device')
    images = []
    if device == 'xiaomi_redmi-router-ax6000-stock':
        marker = '-' + device + '-'
        images = [p for p in targets.iterdir() if marker in p.name
                  and (p.name.endswith(marker + 'squashfs-sysupgrade.bin')
                       or p.name.endswith(marker + 'initramfs-kernel.bin')
                       or p.name.endswith(marker + 'initramfs-factory.ubi'))]
    factories = [p for p in images if p.name.endswith('-initramfs-factory.ubi')]
    if len(factories) > 1 or (factory_required and len(factories) != 1):
        raise ValueError('Exactly one native stock factory UBI is required')
    metadata = {'config.buildinfo', 'feeds.buildinfo', 'version.buildinfo', 'profiles.json'}
    for image in images:
        prefix = image.name.split('-' + device + '-', 1)[0]
        metadata.update({prefix + '.manifest', prefix + '-' + device + '.manifest'})
    image_names = {p.name for p in images}
    files = [p for p in targets.iterdir() if (p.is_file() or p.is_symlink() or p.name in image_names) and
             (p.name in image_names or p.name in metadata or (sdk_name and p.name == sdk_name))]
    if not files:
        return None
    for source in files:
        if not source.is_file() or source.is_symlink() or any(
                p.is_symlink() for p in source.parents if p.is_relative_to(raw)):
            raise ValueError('Firmware assets must be regular files inside raw')
        if source.name == sdk_name and sha256_file(source) != sdk.get('sha256'):
            raise ValueError('Firmware SDK archive does not match the published profile checksum')
    if factories:
        factory = factories[0]
        ram = factory.with_name(factory.name.removesuffix('initramfs-factory.ubi') + 'initramfs-kernel.bin')
        if ram not in files or not 0 < factory.stat().st_size <= 30 * 1024**2:
            raise ValueError('Stock factory UBI requires its RAM FIT and must fit the 30 MiB partition')
        from stock_factory import verify_native_factory
        verify_native_factory(ram, factory, package_only=policy, kernel_inputs=None)
    return build_id, files


def stage_firmware(raw, output, profile):
    planned = firmware_files(raw, profile)
    if planned is None:
        return None
    build_id, files = planned
    destination = Path(output) / 'firmware' / build_id
    destination.mkdir(parents=True)
    for source in files:
        shutil.copy2(source, destination / source.name)
    checksums = ''.join(sha256_file(p) + '  ' + p.name + '\n' for p in sorted(destination.iterdir()))
    for filename in ('sha256sums', 'SHA256SUMS'):
        (destination / filename).write_text(checksums)
    return destination


def public_from_secret(key):
    """Decode OpenWrt usign's unencrypted Ed25519 key container.

    Format: openwrt/usign main.c struct seckey (104 bytes); the generated
    private container includes its public key. usign verifies the final index.
    """
    lines = Path(key).read_text().splitlines()
    if len(lines) != 2 or not lines[0].startswith('untrusted comment:'):
        raise ValueError('Invalid usign secret key format')
    data = base64.b64decode(lines[1], validate=True)
    if len(data) != 104 or data[:4] != b'EdBK' or data[4:8] != bytes(4):
        raise ValueError('Only unencrypted OpenWrt usign Ed25519 keys are supported')
    if hashlib.sha512(data[40:104]).digest()[:8] != data[24:32]:
        raise ValueError('Corrupted usign secret key')
    public = data[:2] + data[32:40] + data[72:104]
    return 'untrusted comment: ax6000-packages public key\n' + base64.b64encode(public).decode() + '\n'


def public_key_payload(text):
    """Compare the usign key payload, whose comment is intentionally untrusted."""
    lines = text.splitlines()
    if len(lines) != 2 or not lines[0].startswith('untrusted comment:'):
        raise ValueError('Invalid pinned public key')
    try:
        data = base64.b64decode(lines[1], validate=True)
    except ValueError as error:
        raise ValueError('Invalid pinned public key') from error
    if len(data) != 42 or data[:2] != b'Ed':
        raise ValueError('Invalid pinned public key')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--public-key', type=Path,
                        help='Require the signing secret to match this pinned usign public key')
    parser.add_argument('--usign', required=True, type=Path)
    parser.add_argument('--lock', required=True, type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    if not args.key.is_file() or not args.usign.is_file():
        raise ValueError('Signing key or SDK usign is missing')
    if args.output.exists():
        raise ValueError('Publication output must be a fresh directory')
    lock = json.loads(args.lock.read_text())
    if lock.get('profile_sha256') != hashlib.sha256(args.profile.read_bytes()).hexdigest():
        raise ValueError('Lock does not match profile')
    derived_public = public_from_secret(args.key)
    if args.public_key is not None:
        try:
            pinned_public = args.public_key.read_text()
        except (OSError, UnicodeError) as error:
            raise ValueError('Invalid pinned public key') from error
        if public_key_payload(pinned_public) != public_key_payload(derived_public):
            raise ValueError('Signing key differs from pinned public key')
    # A malformed/missing mandatory factory must not leave a partial signed feed.
    firmware_files(args.input, profile)
    feed = stage_feed(args.input, args.output, profile)
    public_key = args.output / 'repository.pub'
    usign = str(args.usign.resolve())
    public_key.write_text(derived_public)
    subprocess.run([usign, '-S', '-m', str(feed / 'Packages'), '-s', str(args.key.resolve()),
                    '-x', str(feed / 'Packages.sig')], check=True)
    subprocess.run([usign, '-V', '-m', str(feed / 'Packages'), '-p', str(public_key),
                    '-x', str(feed / 'Packages.sig')], check=True)
    if args.public_key is not None:
        subprocess.run([usign, '-V', '-m', str(feed / 'Packages'),
                        '-p', str(args.public_key.resolve()),
                        '-x', str(feed / 'Packages.sig')], check=True)
    firmware_dir = stage_firmware(args.input, args.output, profile)
    state = args.output / 'state'
    state.mkdir()
    shutil.copy2(args.lock, state / 'lock.json')
    shutil.copyfile(args.profile, args.output / 'profile.json')
    for name in ('build-selection.json', 'build-provenance.json', 'firmware-manifest.json', 'build.config'):
        if (args.input / name).is_file():
            shutil.copy2(args.input / name, args.output / name)
    (args.output / '.nojekyll').touch()
    downloads = ''
    if firmware_dir:
        downloads = '<ul>' + ''.join(
            f'<li><a href="{html.escape(p.relative_to(args.output).as_posix(), quote=True)}">'
            f'{html.escape(p.name)}</a></li>'
            for p in sorted(firmware_dir.iterdir()) if p.is_file()
        ) + '</ul>'
    (args.output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>AX6000 packages</title>'
        '<h1>AX6000 packages</h1><p>Signed supplemental opkg repository.</p>'
        '<p><a href="profile.json">Firmware compatibility</a> · '
        '<a href="repository.pub">Signing public key</a> · '
        '<a href="state/lock.json">Source versions</a></p>'
        f'<p><a href="{feed.relative_to(args.output).as_posix()}/Packages">Package index</a></p>' +
        (f'<p>Kernel ABI: <code>{html.escape(profile["firmware"]["kernel_version"])}</code></p>'
         '<p>Firmware and SDK for Redmi AX6000 stock layout. Install the matching firmware '
         'before using these kernel modules.</p>' + downloads if firmware_dir else ''))
    print(f'Validated and signed feed: {feed}')


if __name__ == '__main__':
    main()
