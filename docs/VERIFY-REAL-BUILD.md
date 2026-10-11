# Проверка реальной сборки и ежедневного обновления

Эти команды выполняются **после успешного завершения текущего world и export**. Они не прошивают роутер. Проверка SDK ниже действительно компилирует пакеты; наличие SDK-архива и прохождение unit tests её не заменяют. Текущая полная сборка не должна работать одновременно с SDK-проверкой.

## 1. Пути и проверка raw

Использовать новый каталог проверки. Если фактические paths отличаются, изменить переменные. Подготовленный source нужен для сравнения контекста ядра и извлечения image metadata.

```bash
export AX6000_REPO=/home/valuxin/dev/owrt/ax6000-packages
export AX6000_SOURCE=/home/valuxin/dev/owrt/ax6000-packages-build/work/retry1/source
export AX6000_RAW=/home/valuxin/dev/owrt/ax6000-packages-build/output/raw
export AX6000_VERIFY=/home/valuxin/dev/owrt/ax6000-packages-build/verify-real
export AX6000_BUILDER_IMAGE=$(docker image inspect ax6000-builder:20261012 --format '{{.Id}}')
test -f "$AX6000_RAW/profile.json"
test ! -e "$AX6000_VERIFY"
mkdir -p "$AX6000_VERIFY/sdk-work"
export PYTHONPATH="$AX6000_REPO/scripts"
python3 - <<'PY'
from pathlib import Path
import hashlib, json, os, shutil
from urllib.parse import urlsplit
import prepare
from publish import read_control, validate_package, firmware_build_id

raw = Path(os.environ['AX6000_RAW'])
repo = Path(os.environ['AX6000_REPO'])
verify = Path(os.environ['AX6000_VERIFY'])
profile = json.loads((raw / 'profile.json').read_text())
lock = json.loads((raw / 'lock.json').read_text())
expected = json.loads((repo / 'profiles/redmi-ax6000-new.json').read_text())
prepare.validate(profile, lock)
assert profile['id'] == expected['id']
for field in ('repository', 'commit', 'device', 'target', 'subtarget', 'architecture'):
    assert profile['firmware'][field] == expected['firmware'][field], field
assert profile['packages'] == expected['packages']
assert profile['all_kmods'] is False
assert lock['firmware'] == profile['firmware'] and lock['sdk'] == profile['sdk']
assert lock['feeds']['base'] == profile['firmware']['commit']
assert lock['profile_sha256'] == hashlib.sha256((raw / 'profile.json').read_bytes()).hexdigest()
assert firmware_build_id(profile) == profile['build_id']
manifest = json.loads((raw / 'firmware-manifest.json').read_text())
assert manifest['build_id'] == profile['build_id']
assert manifest['source_commit'] == profile['firmware']['commit']
assert manifest['kernel_version'] == profile['firmware']['kernel_version']
recorded = set()
for entry in manifest['files']:
    path = raw / entry['path']
    assert path.resolve().is_relative_to(raw.resolve()), entry['path']
    assert entry['path'] not in recorded
    recorded.add(entry['path'])
    assert path.stat().st_size == entry['size']
    with path.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256'], path
assert recorded == {str(p.relative_to(raw)) for p in (raw / 'bin').rglob('*') if p.is_file()}
controls = {}
for path in raw.rglob('*.ipk'):
    control = read_control(path)
    validate_package(control, profile)
    name = control['Package']
    assert name not in controls, ('duplicate Package', name)
    controls[name] = control
assert controls['kernel']['Version'] == profile['firmware']['kernel_version']
assert set(profile['packages']) <= controls.keys()
report = json.loads((raw / 'build-selection.json').read_text())
kmods = {name for name in controls if name.startswith('kmod-')}
assert set(report['selected_kmods']) <= kmods
assert not set(report['selected_kmods']) & set(report['incompatible_kmods'])
assert set(report['candidate_kmods']) == set(report['selected_kmods']) | set(report['incompatible_kmods'])
config = (raw / 'build.config').read_text()
for symbol in ('SDK', 'ALL_KMODS', 'SIGNED_PACKAGES', 'SIGNATURE_CHECK'):
    assert f'CONFIG_{symbol}=y\n' in config, symbol
for name in ('kmod-mt_wifi', 'kmod-conninfra', 'kmod-warp', 'kmod-mediatek_hnat'):
    assert f'CONFIG_PACKAGE_{name}=y\n' in config, name
target = raw / 'bin/targets/mediatek/filogic'
sdk_name = Path(urlsplit(profile['sdk']['url']).path).name
sdk = target / sdk_name
with sdk.open('rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == profile['sdk']['sha256']
shutil.copy2(sdk, verify / 'sdk-work/sdk.archive')
(verify / 'sdk-name.txt').write_text(sdk_name + '\n')
print('ABI:', profile['firmware']['kernel_version'])
print('Packages:', len(controls), 'kmods:', len(kmods), 'selected:', len(report['selected_kmods']))
print('SDK:', sdk_name, sdk.stat().st_size, 'bytes')
PY
```

Для текущего подготовленного `.config` ожидается 1035 selected kmods. Окончательный report и реальные IPK — источник истины; при их расхождении публикацию остановить. Отдельно просмотреть список incompatible: он объясняет, какие архитектурно недоступные kmods не построены.

## 2. Stock image и состав прошивки

```bash
docker run --rm --entrypoint bash \
  --mount "type=bind,source=$AX6000_SOURCE,target=/source,readonly" \
  --mount "type=bind,source=$AX6000_RAW,target=/raw,readonly" \
  --mount "type=bind,source=$AX6000_VERIFY,target=/verify" \
  "$AX6000_BUILDER_IMAGE" -euo pipefail -c '
    images=(/raw/bin/targets/mediatek/filogic/*xiaomi_redmi-router-ax6000-stock*squashfs*sysupgrade.bin)
    test "${#images[@]}" = 1
    test -f "${images[0]}"
    /source/staging_dir/host/bin/fwtool -i /verify/image-metadata.json "${images[0]}"
    tar -tf "${images[0]}" > /verify/image-members.txt
  '
python3 - <<'PY'
from pathlib import Path
import json, os, tarfile
raw, verify = Path(os.environ['AX6000_RAW']), Path(os.environ['AX6000_VERIFY'])
data = json.loads((verify / 'image-metadata.json').read_text())
assert set(data.get('new_supported_devices', data['supported_devices'])) == {'xiaomi,redmi-router-ax6000-stock'}
assert data['version']['target'] == 'mediatek/filogic'
target = raw / 'bin/targets/mediatek/filogic'
image, = target.glob('*xiaomi_redmi-router-ax6000-stock*squashfs*sysupgrade.bin')
with tarfile.open(image) as archive:
    payloads = {Path(p.name).name: p.size for p in archive if p.isfile()}
assert 0 < payloads['kernel'] < 0x1e00000
assert 0 < payloads['root'] < 0x5000000
manifests = list(target.glob('*xiaomi_redmi-router-ax6000-stock*.manifest')) or list(target.glob('*.manifest'))
assert len(manifests) == 1, manifests
installed = {line.split()[0] for line in manifests[0].read_text().splitlines() if line.strip()}
profile = json.loads((raw / 'profile.json').read_text())
assert set(profile.get('image_packages', [])) <= installed
assert {'kmod-mt_wifi', 'kmod-conninfra', 'kmod-warp', 'kmod-mediatek_hnat', 'mtwifi-cfg', 'opkg'} <= installed
assert not {'kmod-mt7915e', 'kmod-mt7986-firmware'} & installed
print('Stock metadata and image manifest verified; payload bytes:', payloads)
PY
```

Размер payload проверяется относительно исходных разделов stock: kernel 30 MiB, root 80 MiB. Это необходимая верхняя граница, а не доказательство наличия свободных UBI-блоков конкретного роутера. Эта проверка не оценивает работу Wi-Fi/HNAT на железе и не запускает flash.

## 3. Подготовленный SDK и полный baseline

Для реального SDK `.tar.zst` текущего дерева:

```bash
docker run --rm --entrypoint bash \
  --mount "type=bind,source=$AX6000_REPO,target=/workspace,readonly" \
  --mount "type=bind,source=$AX6000_VERIFY,target=/verify" \
  "$AX6000_BUILDER_IMAGE" -euo pipefail -c '
    zstd -d -c /verify/sdk-work/sdk.archive > /verify/sdk.tar
    python3 /workspace/scripts/prepare.py --extract /verify/sdk.tar \
      --extract-dir /verify/sdk-inspection > /verify/sdk-root.txt
  '
python3 - <<'PY'
from pathlib import Path
import json, os
import prepare
raw, source, verify = (Path(os.environ[name]) for name in ('AX6000_RAW', 'AX6000_SOURCE', 'AX6000_VERIFY'))
sdk_name = Path((verify / 'sdk-root.txt').read_text().strip()).name
sdk = verify / 'sdk-inspection' / sdk_name
profile = json.loads((raw / 'profile.json').read_text())
prepare.check_kernel(profile, sdk)
baseline = prepare.baseline_packages(profile, sdk)
manifest = json.loads((sdk / 'ax6000-baseline/manifest.json').read_text())
assert set(manifest['frozen_packages']) == baseline.keys()
assert 'kmod-amneziawg' not in baseline
assert {'kmod-wireguard', 'kmod-tun'} <= baseline.keys()
kernel, = source.glob('build_dir/target-*/linux-*/linux-*/.vermagic')
relative = kernel.parent.relative_to(source)
for name in ('.config', '.config.set', '.vermagic', 'Module.symvers', 'modules.builtin.modinfo', 'include/config/kernel.release'):
    assert (source / relative / name).read_bytes() == (sdk / relative / name).read_bytes(), name
protected = {'kernel', 'libc', 'base-files', 'busybox', 'opkg'}
from publish import read_control
offered = {read_control(path)['Package'] for path in raw.rglob('*.ipk')} - protected
assert offered <= baseline.keys() | set(profile['packages'])
print('Verified baseline packages:', len(baseline), 'frozen modules:', len(manifest['frozen_kmods']))
PY
```

Архив SDK нельзя считать рабочим только потому, что его hash/manifest верны. Следующий этап обязателен.

## 4. Реальная компиляция в SDK с тем же ABI

Используется отдельный fresh SDK work, локальная уже проверенная копия архива и **экспортированные** profile/lock. SDK URL может ещё не быть опубликован: build.sh использует существующий проверенный `sdk.archive`, не скачивая его повторно. Feeds берутся по тем же lock pins. Выполняется package-only build, включая новую компиляцию AWG и userland; все frozen packages добавляются при export.

```bash
docker run --rm --cpus=4 --memory=10g --memory-swap=10g --entrypoint bash \
  --mount "type=bind,source=$AX6000_REPO,target=/workspace,readonly" \
  --mount "type=bind,source=$AX6000_RAW,target=/raw,readonly" \
  --mount "type=bind,source=$AX6000_VERIFY,target=/verify" \
  --env AX6000_BUILD_LOCK=/verify/.build.lock \
  "$AX6000_BUILDER_IMAGE" -euo pipefail -c '
    bash /workspace/scripts/build.sh --profile /raw/profile.json --lock /raw/lock.json \
      --work-dir /verify/sdk-work --output-dir /verify/daily-raw
  ' > "$AX6000_VERIFY/sdk-build.log" 2>&1
```

Число `4` соответствует текущему серверу с 8 CPU; resource-limit.sh дополнительно ограничивает affinity половиной разрешённых CPU и снижает priority. Критерии: exit 0, AWG IPK появился именно в `sdk-work/extracted/*/bin`, requested userland построен, сохранён исходный prepared kernel context. Нельзя вручную исправлять vermagic, Depends или source .config ради прохождения проверки.

## 5. Полнота daily export и неизменность SDK/прошивки

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib, json, os
from publish import read_control, validate_package
import prepare
raw, verify = Path(os.environ['AX6000_RAW']), Path(os.environ['AX6000_VERIFY'])
daily = verify / 'daily-raw'
profile = json.loads((raw / 'profile.json').read_text())
assert json.loads((daily / 'profile.json').read_text()) == profile
def inventory(directory):
    result = {}
    for path in directory.rglob('*.ipk'):
        control = read_control(path)
        validate_package(control, profile)
        assert control['Package'] not in result, control['Package']
        result[control['Package']] = path
    return result
first, later = inventory(raw), inventory(daily)
protected = {'kernel', 'libc', 'base-files', 'busybox', 'opkg'}
assert first.keys() - protected <= later.keys() - protected, sorted(first.keys() - later.keys() - protected)
assert {name for name in first if name.startswith('kmod-')} <= later.keys()
sdk, = (verify / 'sdk-work/extracted').iterdir()
compiled = inventory(sdk / 'bin')
assert 'kmod-amneziawg' in compiled
assert (sdk / 'bin').is_dir() and set(profile['packages']) <= later.keys()
baseline = prepare.baseline_packages(profile, sdk)
needs_compile = set(profile['packages']) - {name for name in baseline if name.startswith('kmod-')}
assert needs_compile <= compiled.keys(), sorted(needs_compile - compiled.keys())
source = Path(os.environ['AX6000_SOURCE'])
kernel, = source.glob('build_dir/target-*/linux-*/linux-*/.vermagic')
relative = kernel.parent.relative_to(source)
for name in ('.config', '.config.set', '.vermagic', 'Module.symvers', 'modules.builtin.modinfo'):
    assert (source / relative / name).read_bytes() == (sdk / relative / name).read_bytes(), name
for name, frozen in baseline.items():
    if name not in compiled:
        assert later[name].read_bytes() == frozen.read_bytes(), name
for original in (raw / 'bin/targets/mediatek/filogic').iterdir():
    if original.is_file() and original.name not in {'sha256sums', 'SHA256SUMS'}:
        copy = daily / 'bin/targets/mediatek/filogic' / original.name
        assert copy.is_file(), original.name
        with original.open('rb') as a, copy.open('rb') as b:
            assert hashlib.file_digest(a, 'sha256').digest() == hashlib.file_digest(b, 'sha256').digest(), original.name
print('All offered packages retained; kmod ABI and frozen firmware/SDK verified')
PY
```

До и после SDK compile дополнительно сравнить `.config`, `.config.set`, `.vermagic`, Module.symvers и modules.builtin.modinfo из раздела 3 с новым extracted SDK. `prepare.py --phase check-kernel` подтверждает ABI hash, но byte comparison остальных файлов отдельно проверяет неизменность compile context.

## 6. Подпись и объём публикации

Существующий private signing key задаётся переменной `AX6000_SIGNING_KEY` с абсолютным путём; ключ не копируется в raw или public. Команды используют доверенный usign собственноручно собранного local raw. При импорте Release использовать pinned host usign из publish-existing.yml, а не исполняемый файл из импортированного архива.

```bash
test -f "$AX6000_SIGNING_KEY"
python3 "$AX6000_REPO/scripts/publish.py" --profile "$AX6000_RAW/profile.json" \
  --input "$AX6000_RAW" --output "$AX6000_VERIFY/public-first" \
  --key "$AX6000_SIGNING_KEY" --usign "$AX6000_RAW/usign" --lock "$AX6000_RAW/lock.json"
python3 "$AX6000_REPO/scripts/publish.py" --profile "$AX6000_VERIFY/daily-raw/profile.json" \
  --input "$AX6000_VERIFY/daily-raw" --output "$AX6000_VERIFY/public-daily" \
  --key "$AX6000_SIGNING_KEY" --usign "$AX6000_RAW/usign" --lock "$AX6000_VERIFY/daily-raw/lock.json"
python3 - <<'PY'
from pathlib import Path
import gzip, json, os, subprocess
verify = Path(os.environ['AX6000_VERIFY'])
usign = str(Path(os.environ['AX6000_RAW']) / 'usign')
for site in (verify / 'public-first', verify / 'public-daily'):
    profile = json.loads((site / 'profile.json').read_text())
    feed = site / 'feeds' / profile['id'] / profile['firmware']['kernel_version']
    data = gzip.decompress((feed / 'Packages.gz').read_bytes())
    assert data == (feed / 'Packages').read_bytes()
    message = verify / (site.name + '-decompressed-Packages')
    message.write_bytes(data)
    subprocess.run([usign, '-V', '-m', str(message), '-p', str(site / 'repository.pub'),
                    '-x', str(feed / 'Packages.sig')], check=True)
    size = sum(path.stat().st_size for path in site.rglob('*') if path.is_file())
    assert size <= 1_000_000_000, ('Pages limit', size)
    print(site.name, size, 'bytes; signature valid')
PY
```

После деплоя скачать именно профиль, Packages.gz/Packages.sig/repository.pub и URL SDK из опубликованного профиля; повторить SHA256 и подпись. SDK по URL должен иметь точно тот hash, который зафиксирован профилем. При превышении Pages 1 GB оставить первоначальные assets в immutable Release и сначала реализовать явное Release-хранение SDK/образов; не публиковать feed с недоступным SDK URL.

## 7. Transport bundle и publish-existing

Следовать [PUBLISH.md](PUBLISH.md). Внешний `raw.tar.zst` должен содержать contents raw, только regular files/directories, без duplicate directory aliases. **Не применять `tar --dereference`** ко всему raw: alias `bin/targets/.../packages` может повторить те же IPK, и publisher отвергнет duplicate Package. Например, собрать transport tar без symlinks:

```bash
python3 - <<'PY'
from pathlib import Path
import os, tarfile
raw, verify = Path(os.environ['AX6000_RAW']), Path(os.environ['AX6000_VERIFY'])
with tarfile.open(verify / 'raw.tar', 'w') as archive:
    for path in sorted(raw.rglob('*')):
        if path.is_symlink():
            print('Omit alias:', path.relative_to(raw))
            continue
        assert path.is_file() or path.is_dir(), path
        archive.add(path, arcname=path.relative_to(raw).as_posix(), recursive=False)
PY
docker run --rm --entrypoint zstd \
  --mount "type=bind,source=$AX6000_VERIFY,target=/verify" \
  "$AX6000_BUILDER_IMAGE" -T1 -3 /verify/raw.tar -o /verify/raw.tar.zst
(cd "$AX6000_VERIFY" && sha256sum raw.tar.zst > raw.tar.zst.sha256)
python3 - <<'PY'
from pathlib import Path
import os
archive = Path(os.environ['AX6000_VERIFY']) / 'raw.tar.zst'
assert archive.stat().st_size < 2 * 1024**3, 'Release asset must be under 2 GiB'
PY
docker run --rm --entrypoint python3 \
  --mount "type=bind,source=$AX6000_REPO,target=/workspace,readonly" \
  --mount "type=bind,source=$AX6000_VERIFY,target=/verify" \
  "$AX6000_BUILDER_IMAGE" /workspace/scripts/import-release.py \
    --archive /verify/raw.tar.zst --checksum /verify/raw.tar.zst.sha256 \
    --output /verify/imported-raw --expected-profile /workspace/profiles/redmi-ax6000-new.json
python3 - <<'PY'
from pathlib import Path
import os
from publish import read_control
raw = Path(os.environ['AX6000_RAW'])
imported = Path(os.environ['AX6000_VERIFY']) / 'imported-raw'
def names(directory):
    values = [read_control(path)['Package'] for path in directory.rglob('*.ipk')]
    assert len(values) == len(set(values)), 'Duplicate transported package'
    return set(values)
assert names(raw) == names(imported), 'Transport lost actual built packages'
print('Imported release has the complete original package inventory')
PY
```

Importer дополнительно ограничивает распакованный bundle 8 GiB и 100000 members. До загрузки выполнить import-release.py на полученном bundle в fresh directory через тот же builder container, затем сравнить импортированные Package names с raw. Если исходный kernel module IPK существует только как file symlink, нельзя просто потерять его: сначала определить единственный canonical regular artifact и проверить inventory. Сам SDK — отдельный archive file, его внутренние корректные SDK symlinks не затрагиваются правилами внешнего transport.

Release workflow должен увидеть `immutable=true`, `draft=false`, ровно два uploaded transport assets и неизменную release identity до/после download. Успех публикации фиксировать только после скачивания и независимой проверки публичных assets. Прошивка и установка пакетов на роутер остаются отдельным этапом.
