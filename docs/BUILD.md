# Ограниченная сборка без изменения сервисов сервера

Полная сборка требует большого объёма диска и нескольких часов. Основной вариант
для текущего сервера — отдельный Docker-контейнер. Зависимости устанавливаются
в контейнер, без apt-обновления хостовой системы. Хостовые исходники форка не
изменяются. Рабочий каталог и результат находятся отдельно.

Рекомендуемые ограничения для сервера8CPU/23GiB RAM:

```sh
mkdir -p ../ax6000-packages-build/{work,output}
python3 scripts/monitor.py --profile profiles/redmi-ax6000-new.json --output state/new-lock.json
docker run -d --name ax6000-firmware-build \
  --cpus 4 --memory 10g --memory-swap 10g --pids-limit 2048 \
  --oom-score-adj 500 --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD,dst=/workspace,readonly" \
  --mount "type=bind,src=$(realpath ../ax6000-packages-build/work),dst=/build" \
  --mount "type=bind,src=$(realpath ../ax6000-packages-build/output),dst=/output" \
  ubuntu:24.04 nice -n 19 ionice -c 3 bash /workspace/scripts/container-entrypoint.sh
```

Компиляторы запускаются с nice19, idle I/O и affinity не более половины CPU.
Квота Docker ограничивает контейнер4CPU, память10GiB, swap отключён. Потоки Go
также учитываются настройками ограничителя. Для другой машины выбирать лимиты
не более половины её ресурсов, оставляя память и диск работающим сервисам.

```sh
docker logs --tail 80 ax6000-firmware-build
docker stats --no-stream ax6000-firmware-build
```

Скрипт полной сборки проверяет свободный диск, точные pins, корректный stock-device,
наличие запрошенных пакетов после Kconfig и модульные зависимости. Результат:
образ для ручного обновления, SDK, IPK, профиль ABI, provenance и список модулей.
Повторный запуск не обновляет base source без изменения профиля.

GitHub-hosted runner обычно предоставляет недостаточно диска для полной сборки
всех модулей. Manual workflow проверяет диск и выдаёт причину отказа. Ежедневные
сборки по готовому SDK существенно меньше. Сборка никогда автоматически не
переносится на self-hosted runner сервера.
