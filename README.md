# Redmi AX6000: прошивка и подписанные пакеты

Прошивка для **Xiaomi Redmi Router AX6000, stock layout** на базе
[PadavanOnly ImmortalWrt 24.10 / Linux 6.6](https://github.com/padavanonly/immortalwrt-mt798x-6.6).
Vendor Wi-Fi: `mt_wifi 7.6.6.1`; формат пакетов — **OPKG/IPK, база 24.10**.
Прошивка, SDK и модули выпускаются одним набором; ежедневные обновления
используют SDK этого набора. Установка прошивки выполняется владельцем вручную.

## Скачать

[Новый Release: прошивка, SDK и ImageBuilder](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-940de134c620) ·
[Подписанный репозиторий пакетов](https://valujin.github.io/ax6000-packages/).

| Файл stock layout | Назначение |
| --- | --- |
| [initramfs-factory.ubi](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-940de134c620/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-initramfs-factory.ubi) | Нативный factory/recovery UBI, **20 316 160 байт**, меньше лимита 30 MiB |
| [squashfs-sysupgrade.bin](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-940de134c620/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin) | Обновление уже установленной совместимой OpenWrt/ImmortalWrt |
| [initramfs-kernel.bin](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-940de134c620/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-initramfs-kernel.bin) | FIT для загрузки в RAM по подходящей процедуре восстановления |
| [SHA256SUMS](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-940de134c620/SHA256SUMS) | Контрольные суммы файлов этого набора |

Эти форматы имеют разное назначение. Наличие factory UBI не разрешает
записывать произвольный FIT в раздел kernel. Перед установкой сверить
разметку, контрольную сумму и процедуру загрузчика: [инструкция](docs/FLASH.md).

**Tailscale исключён из initramfs и постоянного rootfs.** Он остаётся отдельным
IPK в подписанном feed и участвует в обновлениях. Остальные запрошенные
пакеты образа сохранены, включая AmneziaWG, WireGuard, PBR, LuCI и русский
интерфейс. Tailscale собирается из upstream-исходников в один ARM64
`tailscale.multicall`, сжимается UPX и предоставляет команды `tailscale` и
`tailscaled` через ссылки. Функции клиента сохранены.

В feed опубликован **Tailscale 1.104.2-r3**: IPK **11 911 941 байт**,
примерно на **67,7% меньше r2**; установленный пакет занимает около **11,7 MiB**.
На stock раздел UBI root имеет 80 MiB; перед установкой проверьте свободное
место через `df -h /overlay` и размер зависимостей в `opkg info tailscale`.
[Снимок пакетов](https://github.com/valujin/ax6000-packages/releases/tag/packages-66-5fafd84a3bd02cb0b267cc44276f7c67391781c738bca206947ddc48b18981f0)
прошёл сборку в штатном SDK, ARM64 QEMU smoke, симуляции OPKG на полной
нативной базе и проверку опубликованного IPK и подписей по HTTPS.

## Точный набор и проверки

Исходники: `ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8`.
Build ID: `redmi-ax6000-ec9ef10efc65-940de134c620`.

```text
kernel ABI: 6.6.133~6ea4b96ad26e951cc0b4fa9296a521d3-r1
SDK SHA256: 940de134c620a168e5cb71be59cdbaef32e4fb5e9a1946ec6af10445b8067624
```

Нативный банк содержит **1385 IPK**, включая **1029 пакетов kmod**.
В дополнительном feed опубликованы **1380 IPK**: `kernel`, `libc`, `base-files`,
`busybox` и `opkg` исключены, чтобы их установка не подменяла основу прошивки.
Банк содержит применимые модули target; исключения указаны в `build-selection.json`.

Новая сборка SDK экспортировала 1382 пакета; ядро и все 1029 kmods совпали
с нативным банком побайтно, включая заново собранный AmneziaWG. При публикации
защищённые зависимости сохранены из нативной сборки. Проверены три подписи,
все файлы подписанного снимка и скачивание всех 1380 пакетов настоящим opkg
на базе пакетов нативного образа. Отдельный ImageBuilder успешно выполнил
`make info`, `make manifest` и `make image`; проверены ядро, rootfs и все
408 установленных пакетов. Tailscale в стандартном образе отсутствует.

Проверки артефактов не подтверждают загрузку, Ethernet, Wi-Fi или VPN на
физическом роутере. Конфигурация выбранного vendor-стека сохранена;
аппаратное ускорение на устройстве отдельно не проверялось.

## Подключение пакетов

После ручной установки соответствующей прошивки проверить сеть и выполнить
**на самом роутере от root**:

```sh
opkg status kernel
# Version: 6.6.133~6ea4b96ad26e951cc0b4fa9296a521d3-r1
ax6000-install-feed
```

Встроенная команда не принимает аргументов. Она сверяет точный ABI,
архитектуру, закреплённый ключ и подпись индекса; сохраняет настройки opkg,
отключает другие активные feeds и выполняет `opkg update`. Пакеты не
устанавливаются. Путь резервной копии выводится как `Backup:`.

Модули 6.6.133 несовместимы с прошивкой 6.6.95 и с линиями 6.12.
Не подменять зависимость `kernel` и не использовать принудительную установку
модулей из другого набора.

## Обновления

Ежедневно в 03:17 UTC монитор проверяет feeds, официальный stable Tailscale,
согласованную стабильную пару PBR/LuCI автора MOSSDeF и feed-рецепт AmneziaWG
из `2Grey/awg-openwrt`. Версия AWG зависит от обновления этого рецепта;
прямого отслеживания всех релизов Amnezia нет. При изменениях пакеты
собираются из точного SDK, штатные модули сохраняются из нативного банка.
Неизменившийся набор пропускается; ошибочная сборка не заменяет публикацию.

Обновление feed не продвигает каталог **Attended Sysupgrade** автоматически.
ASU использует отдельно проверенный и закреплённый снимок; новое поколение
и его каталог активирует оператор. В ASU все kmods, включая AmneziaWG,
сохраняются из нативного банка: для нового AWG сейчас требуется новое
проверенное нативное поколение, но версия ядра может остаться прежней.
Tailscale отсутствует в обычных образах; ASU сохраняет его только если он
уже установлен на запрашивающем роутере. Аппаратные испытания выполняются отдельно.

Страна Wi-Fi `DB` доступна в LuCI и выбирается вручную для каждого радио.
Она записывает буквальное `country=DB` в выбранную секцию. Добавление пункта
не меняет страну по умолчанию, не форсирует мощность и не отключает DFS.

Активный профиль — `profiles/redmi-ax6000.json`; source-профиль полной сборки —
`profiles/redmi-ax6000-new.json`. Старый профиль 6.6.95 выключен.
[Предыдущий неизменяемый Release](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516)
сохранён для истории; его образы и SDK не заменяются файлами нового набора.

[CI и подпись](docs/CI.md) · [Локальная сборка](docs/BUILD.md) ·
[Публикация](docs/PUBLISH.md) · [VPN и маршрутизация](docs/ROUTING.md) ·
[Линии 6.12](https://github.com/valujin/ax6000-packages-6.12).
Linux 6.18 в этой серии не поставляется.
