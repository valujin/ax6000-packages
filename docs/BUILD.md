# Ограниченная сборка без изменения сервисов сервера

Полная сборка требует большого объёма диска и нескольких часов. Основной вариант
для текущего сервера — отдельный Docker-контейнер. Зависимости устанавливаются
в контейнер, без apt-обновления хостовой системы. Хостовые исходники форка не
изменяются. Рабочий каталог и результат находятся отдельно.

Рекомендуемые ограничения для сервера8CPU/23GiB RAM:

```sh
mkdir -p ../ax6000-packages-build/{work,output} ../ax6000-build-global
python3 scripts/monitor.py --profile profiles/redmi-ax6000-new.json --output state/new-lock.json
python3 - <<'PY'
import json
from pathlib import Path
profile = json.loads(Path('profiles/redmi-ax6000-new.json').read_text())
path = Path('state/new-lock.json')
lock = json.loads(path.read_text())
lock['feeds'] = {feed['name']: feed['commit'] for feed in profile['feeds']}
path.write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n')
PY
docker run -d --name ax6000-firmware-build \
  --cpus 4 --memory 10g --memory-swap 10g --pids-limit 2048 \
  --oom-score-adj 500 --security-opt no-new-privileges \
  -e AX6000_BUILD_LOCK=/global/.build.lock \
  --mount "type=bind,src=$PWD,dst=/workspace,readonly" \
  --mount "type=bind,src=$(realpath ../ax6000-packages-build/work),dst=/build" \
  --mount "type=bind,src=$(realpath ../ax6000-packages-build/output),dst=/output" \
  --mount "type=bind,src=$(realpath ../ax6000-build-global),dst=/global" \
  ubuntu:24.04 nice -n 19 ionice -c 3 bash /workspace/scripts/container-entrypoint.sh
```

Компиляторы запускаются с nice19, idle I/O и affinity не более половины CPU.
Квота Docker ограничивает контейнер4CPU, память10GiB, swap отключён. Потоки Go
также учитываются настройками ограничителя. Для другой машины выбирать лимиты
не более половины её ресурсов, оставляя память и диск работающим сервисам.
Обе линии 6.6/6.12 должны использовать один каталог `ax6000-build-global`
для общего lock. В полной сборке feeds закреплены на проверенных commits
профиля; ежедневные SDK-сборки разрешают свежие версии отдельно.

```sh
docker logs --tail 80 ax6000-firmware-build
docker stats --no-stream ax6000-firmware-build
```

Скрипт полной сборки проверяет свободный диск, точные pins, корректный stock-device,
наличие запрошенных пакетов после Kconfig и модульные зависимости. Результат:
образ для ручного обновления, SDK, IPK, профиль ABI, provenance и список модулей.
Повторный запуск не обновляет base source без изменения профиля.

Stock `initramfs-factory.ubi` ограничен 30720 KiB — размером `ubi_kernel`
в закреплённом DTS. Проверка после `ubinize-kernel` удаляет слишком большой
артефакт. Граница проверяется настоящим GNU make на размере 30 MiB и 30 MiB+1.
В новом source-профиле Tailscale собирается отдельным IPK
(`CONFIG_PACKAGE_tailscale=m`) и исключён из sysupgrade, RAM initramfs и stock
factory. Проверка готовых образов контролирует отсутствие его файлов,
метаданных и службы. Официальные бинарники в IPK упаковываются без strip;
обновления пакета выпускаются из SDK без пересборки установленной прошивки.

У IPK 1.104.1-r2 установленный размер 72 448 000 байт, а stock-раздел UBI
занимает 80 MiB. После размещения нового SquashFS свободного места для штатной
проверки opkg недостаточно. Отдельная установка этого пакета требует разметки
с большим доступным overlay; переход на другую разметку выполняется отдельно
владельцем. Проверку места не отключать.

GitHub-hosted runner обычно предоставляет недостаточно диска для полной сборки
всех модулей. Manual workflow проверяет диск и выдаёт причину отказа. Ежедневные
сборки по готовому SDK существенно меньше. Сборка никогда автоматически не
переносится на self-hosted runner сервера.
