#!/usr/bin/env python3
"""Build a pinned, independent native opkg parser for disposable CI tests."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import resource
import shutil
import subprocess
import tarfile
import urllib.request

INPUTS = {
    'libubox': {'commit': '3868f47c8f6c6570e62a3cdf8a7f26ffb1a67e6a',
        'repository': 'https://git.openwrt.org/project/libubox.git',
        'name': 'libubox-2024.12.19~3868f47c', 'size': 81649,
        'sha256': 'e10144cfae155cf2ae5fdf167bd575dbf34c7751ddb112554b6962003f989b36'},
    'opkg': {'commit': '38eccbb1fd694d4798ac1baf88f9ba83d1eac616',
        'repository': 'https://git.openwrt.org/project/opkg-lede.git',
        'name': 'opkg-2024.10.16~38eccbb1', 'size': 122588,
        'sha256': 'de58ff1c99c14789f9ba8946623c8c1e58d022e7e2a659d6f97c6fde54f2c4f4'},
}
MEMORY = 2 * 1024**3


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def obtain(spec, downloads, cache):
    name = spec['name'] + '.tar.zst'
    target = downloads / name
    if cache is not None:
        original = cache / name
        require(original.is_file() and not original.is_symlink(), 'missing regular cached source archive')
        require(original.stat().st_size == spec['size'] and sha256(original) == spec['sha256'],
                'cached source archive SHA256/size mismatch')
        shutil.copyfile(original, target)
    else:
        url = 'https://sources.openwrt.org/' + name
        with urllib.request.urlopen(url, timeout=30) as source, target.open('xb') as output:
            size = 0
            while chunk := source.read(65536):
                size += len(chunk)
                require(size <= spec['size'], 'source download exceeds pinned size')
                output.write(chunk)
    require(target.stat().st_size == spec['size'] and sha256(target) == spec['sha256'],
            'source archive SHA256/size mismatch')
    return target


def extract_source(archive, top, destination):
    # Only verified small source archives reach the trusted host decompressor.
    data = subprocess.check_output(['zstd', '-d', '-c', '--', str(archive)], timeout=30)
    require(len(data) <= 4 * 1024**2, 'source tar exceeds size bound')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as bundle:
        members = bundle.getmembers()
        require(0 < len(members) <= 1000, 'source member count exceeds bound')
        names, total = set(), 0
        for member in members:
            path = PurePosixPath(member.name)
            require(path.parts and path.parts[0] == top and not path.is_absolute()
                    and '..' not in path.parts and '\\' not in member.name
                    and path.as_posix() not in names, 'unsafe or duplicate source member')
            require(member.isdir() or member.isfile(), 'source links and special files are forbidden')
            names.add(path.as_posix()); total += member.size
            require(0 <= member.size <= 2 * 1024**2 and total <= 4 * 1024**2,
                    'source member bytes exceed bound')
        require(top + '/CMakeLists.txt' in names, 'source CMake entrypoint missing')
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
    return destination / top


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--download-cache', type=Path)
    parser.add_argument('--require-cgroup', action='store_true')
    args = parser.parse_args()
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64',
            'host opkg CI bootstrap requires native Linux x86-64')
    require(os.getuid() != 0, 'host opkg sources and outputs must belong to the nonroot runner')
    work = args.work_dir.absolute()
    require(not work.exists() and not work.is_symlink(), 'host tool work directory must be new')
    cgroup_proof = {}
    if args.require_cgroup:
        require(len(os.sched_getaffinity(0)) == 1, 'host tool requires one effective allowed CPU')
        membership = Path('/proc/self/cgroup').read_text().strip().splitlines()
        unified = [line.removeprefix('0::') for line in membership if line.startswith('0::')]
        require(len(unified) == 1 and '..' not in PurePosixPath(unified[0]).parts,
                'verified cgroup v2 membership required')
        group = Path('/sys/fs/cgroup') / unified[0].lstrip('/')
        cgroup_proof = {key: (group / key).read_text().strip()
                        for key in ('memory.max', 'memory.swap.max', 'cpu.max')}
        quota, period = cgroup_proof['cpu.max'].split()
        require(cgroup_proof['memory.max'].isdigit() and 0 < int(cgroup_proof['memory.max']) <= MEMORY
                and cgroup_proof['memory.swap.max'] == '0'
                and quota.isdigit() and 0 < int(quota) <= int(period),
                'host tool requires aggregate CPU<=1, memory<=2GiB and zero swap')
    for executable in ('cmake', 'cc', 'make', 'zstd', 'taskset', 'nice', 'ionice'):
        require(shutil.which(executable), 'required host build command missing: ' + executable)
    # One compiler at a time, bound to one CPU. Disposable CI additionally uses
    # a 2GiB aggregate cgroup; this address-space ceiling covers each descendant.
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    ceiling = min(MEMORY, hard) if hard != resource.RLIM_INFINITY else MEMORY
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    os.nice(max(0, 19 - os.getpriority(os.PRIO_PROCESS, 0)))
    subprocess.run(['ionice', '-c', '3', '-p', str(os.getpid())], check=True)
    work.mkdir(parents=True)
    downloads = work / 'downloads'; downloads.mkdir()
    sources = work / 'source'; sources.mkdir()
    source_paths = {}
    for name, spec in INPUTS.items():
        archive = obtain(spec, downloads, args.download_cache)
        source_paths[name] = extract_source(archive, spec['name'], sources)
    helper = Path(__file__).resolve().with_name('resource-limit.sh')
    commands = []
    def run(*command):
        commands.append(list(map(str, command)))
        subprocess.run(['bash', str(helper), '--jobs', '1', '--', *map(str, command)], check=True)
    ubox = work / 'libubox-build'
    run('cmake', '-S', source_paths['libubox'], '-B', ubox,
        '-DBUILD_LUA=OFF', '-DBUILD_EXAMPLES=OFF', '-DUNIT_TESTING=OFF')
    run('cmake', '--build', ubox, '--target', 'ubox-static', '--parallel', '1')
    prefix = work / 'prefix'; (prefix / 'include/libubox').mkdir(parents=True)
    (prefix / 'lib').mkdir()
    for header in source_paths['libubox'].glob('*.h'):
        if not header.name.endswith('-priv.h'):
            shutil.copyfile(header, prefix / 'include/libubox' / header.name)
    shutil.copyfile(ubox / 'libubox.a', prefix / 'lib/libubox.a')
    build = work / 'opkg-build'
    run('cmake', '-S', source_paths['opkg'], '-B', build,
        '-DSTATIC_UBOX=ON', '-DBUILD_TESTS=OFF', '-DHOST_CPU=aarch64_cortex-a53',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
        '-DLOCK_FILE=/tmp/opkg.lock',
        '-DVERSION=38eccbb1fd694d4798ac1baf88f9ba83d1eac616 (2024-10-16)',
        '-Dubox:FILEPATH=' + str(prefix / 'lib/libubox.a'),
        '-DCMAKE_C_FLAGS=-I' + str(prefix / 'include'))
    run('cmake', '--build', build, '--target', 'opkg-cl', '--parallel', '1')
    binary = build / 'src/opkg-cl'
    header = binary.read_bytes()[:20]
    require(header[:7] == b'\x7fELF\x02\x01\x01' and header[18:20] == b'\x3e\x00',
            'compiled tool is not a native x86-64 ELF')
    version = subprocess.check_output([str(binary), '--version'], text=True).strip()
    require(INPUTS['opkg']['commit'] in version, 'compiled opkg source version differs')
    proof = {'schema_version': 1, 'kind': 'independent-host-opkg', 'sources': INPUTS,
        'commands': commands, 'binary': {'path': str(binary), 'sha256': sha256(binary),
        'size': binary.stat().st_size, 'version': version},
        'libubox_static_sha256': sha256(prefix / 'lib/libubox.a'),
        'limits': {'cpu_affinity': sorted(os.sched_getaffinity(0)),
                   'address_space_bytes': ceiling, 'nice': os.getpriority(os.PRIO_PROCESS, 0),
                   'cgroup': cgroup_proof, 'uid': os.getuid(), 'gid': os.getgid()}}
    (work / 'tool-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps(proof['binary']))


if __name__ == '__main__':
    main()
