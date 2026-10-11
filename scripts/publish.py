#!/usr/bin/env python3
"""Validate IPKs and publish an opkg feed. Never rewrite kernel dependencies."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


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
                      SHA256sum=hashlib.sha256(target.read_bytes()).hexdigest())
        paragraphs.append('\n'.join(f'{key}: {value}' for key, value in fields.items()))
    index = ('\n\n'.join(paragraphs) + '\n\n').encode()
    (feed / 'Packages').write_bytes(index)
    (feed / 'Packages.gz').write_bytes(gzip.compress(index, mtime=0))
    return feed


def stage_firmware(raw, output, profile):
    if not (Path(raw) / 'bin').is_dir():
        return None
    targets = Path(raw) / 'bin' / 'targets' / profile['firmware']['target'] / profile['firmware']['subtarget']
    if not targets.is_dir():
        return None
    build_id = profile['id'] + '-' + profile['firmware']['commit'][:12]
    destination = Path(output) / 'firmware' / build_id
    files = [p for p in targets.iterdir() if p.is_file() and
             (p.name.endswith(('.bin', '.itb', '.tar.zst', '.tar.xz', '.manifest', '.json')) or
              p.name in ('config.buildinfo', 'feeds.buildinfo', 'version.buildinfo', 'sha256sums'))]
    if not files:
        return None
    destination.mkdir(parents=True)
    for source in files:
        shutil.copy2(source, destination / source.name)
    checksums = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                        for p in sorted(destination.iterdir()) if p.name != 'SHA256SUMS')
    (destination / 'SHA256SUMS').write_text(checksums)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--key', required=True, type=Path)
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
    feed = stage_feed(args.input, args.output, profile)
    public_key = args.output / 'repository.pub'
    usign = str(args.usign.resolve())
    public_key.write_text(public_from_secret(args.key))
    subprocess.run([usign, '-S', '-m', str(feed / 'Packages'), '-s', str(args.key.resolve()),
                    '-x', str(feed / 'Packages.sig')], check=True)
    subprocess.run([usign, '-V', '-m', str(feed / 'Packages'), '-p', str(public_key),
                    '-x', str(feed / 'Packages.sig')], check=True)
    firmware_dir = stage_firmware(args.input, args.output, profile)
    state = args.output / 'state'
    state.mkdir()
    shutil.copy2(args.lock, state / 'lock.json')
    (args.output / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    for name in ('build-selection.json', 'build-provenance.json'):
        if (args.input / name).is_file():
            shutil.copy2(args.input / name, args.output / name)
    (args.output / '.nojekyll').touch()
    (args.output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>AX6000 packages</title>'
        '<h1>AX6000 packages</h1><p>Signed supplemental opkg repository.</p>'
        '<p><a href="profile.json">Firmware compatibility</a> · '
        '<a href="repository.pub">Signing public key</a> · '
        '<a href="state/lock.json">Source versions</a></p>'
        f'<p><a href="{feed.relative_to(args.output).as_posix()}/Packages">Package index</a></p>' +
        (f'<p>Firmware/SDK: <a href="{firmware_dir.relative_to(args.output).as_posix()}/SHA256SUMS">checksums</a></p>' if firmware_dir else ''))
    print(f'Validated and signed feed: {feed}')


if __name__ == '__main__':
    main()
