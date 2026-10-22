# Ручная проверка новой прошивки AX6000 stock

Новый набор: `redmi-ax6000-ec9ef10efc65-940de134c620`.
[Release](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-940de134c620), [SHA256SUMS](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-940de134c620/SHA256SUMS).
Точный ABI: `6.6.133~6ea4b96ad26e951cc0b4fa9296a521d3-r1`.

Образы предназначены только для `xiaomi,redmi-router-ax6000-stock`.
Образы mtkuboot/ubootmod и их процедуры установки не подходят stock layout.
Выбранный vendor-стек MediaTek сохранён в конфигурации сборки.

## Выбрать правильный файл

| Файл | Формат и использование |
| --- | --- |
| `*-stock-initramfs-factory.ubi` | Нативный UBI для подходящей stock factory/recovery процедуры: **20 316 160 байт**, лимит раздела kernel **30 MiB** |
| `*-stock-initramfs-kernel.bin` | FIT для загрузки в RAM по подходящей процедуре загрузчика; это не готовый UBI для прямой записи в kernel |
| `*-stock-squashfs-sysupgrade.bin` | Обновление из уже установленной совместимой OpenWrt/ImmortalWrt с stock layout |

Factory UBI содержит initramfs, а не постоянный rootfs. После запуска
временной системы дальнейшая установка постоянной прошивки требует отдельного
ручного шага владельца по процедуре именно его загрузчика. Не загружать
sysupgrade как factory через интерфейс Xiaomi и не подменять один формат другим.
Наличие правильной геометрии и размера UBI не является испытанием на железе.

## Проверка перед установкой

Сохранить конфигурацию, текущий образ и доступные данные восстановления.
Архив настроек содержит секреты; не публиковать его. Проверить SHA256 файла
по каталогу и контрольным суммам **того же нового Release**, а не по суммам
предыдущего поколения. Прошивка и SDK должны относиться к одному build ID.

На роутере проверить модель/разметку и формат **sysupgrade**:

```sh
ubus call system board
sysupgrade -T /tmp/immortalwrt-...-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin
```

`sysupgrade -T` проверяет образ и не прошивает его. Перезапуск, прошивка,
выбор разделов и сохранение старых настроек выполняются владельцем вручную.
Команды записи flash здесь намеренно не задаются без проверки конкретного
загрузчика и состояния разделов. Проверка файлов не подтверждает загрузку,
Ethernet, Wi-Fi или аппаратный NAT на физическом устройстве.

## После ручной установки

Сначала проверить Ethernet, Wi-Fi и точный `opkg status kernel`, затем выполнить
**на самом роутере от root**:

```sh
ax6000-install-feed
```

Команда не принимает аргументов и не устанавливает пакеты. Она проверяет ABI,
архитектуру, закреплённый fingerprint ключа и подпись индекса, затем выполняет
`opkg update`. До подключения matching feed другие источники пакетов в образе
не активированы; доверенный ключ уже включён в образ.

Installer сохраняет `/etc/opkg.conf` и `/etc/opkg/*.conf` с permissions в
уникальном `/etc/opkg/ax6000-feed-backup.*`; путь выводится как `Backup:`.
Каталог backup не загружается opkg. Затем другие активные feeds отключаются;
настройки проверки подписей, архитектур и destinations сохраняются. Повторный
запуск не добавляет дубликатов. Symlink-конфигурации требуют ручного разбора:
installer откажется до изменения ключей или feeds.

Если `opkg update` завершился ошибкой, успешная настройка не заявляется.
Проверить сеть и matching feed; при необходимости исходные конфигурации
доступны в `original` указанного backup. Не подключать moving official feeds
параллельно: у opkg нет приоритета источников, и они могут предложить модули
с другим ABI или несовместимые библиотеки. Не подменять зависимость `kernel`
и не устанавливать модули принудительно.

## Tailscale и место в flash

Tailscale отсутствует **и в initramfs, и в постоянном rootfs** нового набора,
но IPK остаётся в подписанном feed и обновляется. Версия 1.104.2-r3 использует
один собранный из исходников `tailscale.multicall`, UPX и ссылки для обеих
команд; функции клиента сохранены.

На stock раздел UBI root имеет **80 MiB**. Компактный пакет 1.104.2-r3 занимает
около **11,7 MiB после установки**, IPK — 11 911 941 байт. Прежняя r2 занимала
около 69,2 MiB после распаковки. Для установки r3 проверьте доступное место
в overlay с учётом зависимостей и собственных данных.

Проверка на уже установленной постоянной системе:

```sh
df -h /overlay
opkg info tailscale
```

После проверки места пакет устанавливается через `opkg install tailscale`;
уже установленный пакет обновляется через `opkg upgrade tailscale`.

## Совместимость и история

Модули этого набора несовместимы с прошивкой 6.6.95 и линиями 6.12.
Актуальный SDK: `940de134c620a168e5cb71be59cdbaef32e4fb5e9a1946ec6af10445b8067624`.
Точный набор и результаты проверок приведены в [README](../README.md#точный-набор-и-проверки).
Предыдущий SDK `31f4781ff5168025ac387ae58dea716352190cb7d035dc97e8ab1a911aa8273d`
сохранён для истории: [нативные образы](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/native.json), [проверка SDK](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/sdk_after.json), [независимый аудит](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/sdk-independent-audit.json) и [публикация Pages](https://github.com/valujin/ax6000-packages/actions/runs/39521760493).

[Прежний Release](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-0b03a5907043)
сохранён неизменяемым; его прежний большой initramfs и отсутствие factory UBI
не описывают новый набор. Старые доказательства SDK не заменяют проверки нового.
