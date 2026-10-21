# Redmi AX6000: прошивка и подписанные пакеты

Прошивка для **Xiaomi Redmi Router AX6000, stock layout** на базе
[PadavanOnly ImmortalWrt 24.10 / Linux 6.6](https://github.com/padavanonly/immortalwrt-mt798x-6.6).
Прошивка, SDK и модули выпускаются одним набором; ежедневные обновления
используют SDK этого набора. Установка прошивки выполняется владельцем вручную.

## Скачать

[Новый Release: прошивка, SDK и доказательства проверок](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516) ·
[Подписанный репозиторий пакетов](https://valujin.github.io/ax6000-packages/).

| Файл stock layout | Назначение |
| --- | --- |
| [initramfs-factory.ubi](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-initramfs-factory.ubi) | Нативный factory/recovery UBI, **19 791 872 байта**, меньше лимита 30 MiB |
| [squashfs-sysupgrade.bin](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin) | Обновление уже установленной совместимой OpenWrt/ImmortalWrt |
| [initramfs-kernel.bin](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-initramfs-kernel.bin) | FIT для загрузки в RAM по подходящей процедуре восстановления |
| [SHA256SUMS](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/SHA256SUMS) | Контрольные суммы файлов этого набора |

Эти форматы имеют разное назначение. Наличие factory UBI не разрешает
записывать произвольный FIT в раздел kernel. Перед установкой сверить
разметку, контрольную сумму и процедуру загрузчика: [инструкция](docs/FLASH.md).

**Tailscale исключён из initramfs и постоянного rootfs.** Он остаётся отдельным
IPK в подписанном feed и участвует в обновлениях. Остальные запрошенные
пакеты образа сохранены, включая AmneziaWG, WireGuard, PBR, LuCI и русский
интерфейс. Официальные ARM64 ELF Tailscale не изменяются при упаковке.

На штатной разметке раздел UBI root имеет всего **80 MiB**. Установка
Tailscale 1.104.2-r2 требует около **69,2 MiB** для распакованного пакета,
помимо уже занятой системой памяти flash. В штатный overlay этот пакет
не помещается. Подключение feed не устанавливает Tailscale; не запускать его
установку без отдельно подготовленного достаточного хранилища.

## Точный набор и проверки

Исходники: `ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8`.
Build ID: `redmi-ax6000-ec9ef10efc65-31f4781ff516`.

```text
kernel ABI: 6.6.133~6ea4b96ad26e951cc0b4fa9296a521d3-r1
SDK SHA256: 31f4781ff5168025ac387ae58dea716352190cb7d035dc97e8ab1a911aa8273d
```

Нативная сборка содержит **1370 IPK**, включая **1029 пакетов kmod**.
Банк содержит применимые модули target; исключения указаны в
`build-selection.json`. Альтернативные драйверы могут конфликтовать при установке.
Проверки нативных образов подтверждают отсутствие Tailscale и корректную
структуру stock FIT/UBI. Результат отдельной сборки из нового SDK:
1367 IPK, все 26 запрошенных пакетов; AmneziaWG пересобран, 1028 штатных kmod сохранены побайтно ([отчёт](https://github.com/valujin/ax6000-packages/releases/download/firmware-redmi-ax6000-ec9ef10efc65-31f4781ff516/sdk_after.json)). Для первичной публикации проверены подпись индекса и все файлы: все 1365 IPK, 1387 файлов Pages и 28 файлов Release совпали с проверенным набором.

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

Ежедневный монитор отслеживает feeds, AmneziaWG и стабильный Tailscale.
При изменении входов или рецепта запрошенные пакеты и AmneziaWG собираются
из точного SDK; штатные модули сохраняются из нативного банка. Неизменившийся
набор пропускается. Обновление ядра и прошивки выполняется отдельно.

[Автоматическая сборка и публикация нового SDK](https://github.com/valujin/ax6000-packages/actions/runs/39522430725)
обновила Tailscale до **1.104.2-r2**. Повторная проверка скачиванием подтвердила
все 1365 IPK, 1385 файлов Pages и 28 файлов Release; 1028 штатных kmod,
образы, SDK и контрольные суммы сохранены побайтно.

Активный профиль — `profiles/redmi-ax6000.json`; source-профиль полной сборки —
`profiles/redmi-ax6000-new.json`. Старый профиль 6.6.95 выключен.
[Предыдущий неизменяемый Release](https://github.com/valujin/ax6000-packages/releases/tag/firmware-redmi-ax6000-ec9ef10efc65-0b03a5907043)
сохранён для истории; его образы и SDK не заменяются файлами нового набора.

[CI и подпись](docs/CI.md) · [Локальная сборка](docs/BUILD.md) ·
[Публикация](docs/PUBLISH.md) · [VPN и маршрутизация](docs/ROUTING.md) ·
[Линии 6.12](https://github.com/valujin/ax6000-packages-6.12).
Linux 6.18 в этой серии не поставляется.
