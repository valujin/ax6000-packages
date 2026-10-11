# Две сборки 6.12 для трёх разметок AX6000

Read-only проверка 2026-10-12. Сборки и изменения существующих профилей не выполнялись.

## Вывод

**Штатные правила выбранного fork позволяют одной сборкой каждого firmware channel получать stock, MTK U-Boot и OpenWrt U-Boot образы.** Рациональная схема — две полные сборки, recommended/latest, каждая с тремя выбранными AX6000 devices, общим vendor SKU, SDK и набором пакетов. Делать шесть независимых toolchain/kernel/package сборок только из-за разметки не требуется.

Это вывод из исходных правил, а не результат нашей компиляции. Общий ABI внутри channel следует подтвердить экспортом SDK, kernel package metadata и manifest всех трёх образов; ABI между recommended/latest объединять нельзя. Исходники firmware: `ae65e1de69971b5a2e3cf7e39390ee93f274d488` и `a99b0888b88f134f56bcb2a7d93027b5d20638a9`; feeds закрепляются отдельно, как в [FEEDS-6.12.md](FEEDS-6.12.md).

## Профили и реальные форматы

| Device ID | Разметка DTS | Основной образ | Дополнительные артефакты при выбранном initramfs |
| --- | --- | --- | --- |
| `xiaomi_redmi-router-ax6000-stock` | `ubi_kernel`: offset `0x600000`, size `0x1e00000`; `ubi`: offset `0x2400000`, size `0x5000000` | `*-stock-squashfs-sysupgrade.bin`, sysupgrade tar с metadata | `*-stock-initramfs-kernel.bin`, `*-stock-initramfs-factory.ubi`; новых bootloader artifacts профиль не объявляет |
| `xiaomi_redmi-router-ax6000-mtkuboot` | `ubi`: offset `0x600000`, size `0x6e00000`, NMBM | `*-mtkuboot-squashfs-sysupgrade.bin`, sysupgrade tar; `*-mtkuboot-squashfs-factory.bin`, UBI image с лимитом 112640k | `*-mtkuboot-initramfs-kernel.bin`; профиль не объявляет preloader/FIP |
| `xiaomi_redmi-router-ax6000-ubootmod` | `ubi`: offset `0x580000`, size `0x7a80000`; volume `fit`, root `/dev/fit0` | `*-ubootmod-squashfs-sysupgrade.itb`, FIT со встроенным rootfs | `*-ubootmod-initramfs-recovery.itb`, `*-ubootmod-initramfs-factory.ubi`, `*-ubootmod-preloader.bin`, `*-ubootmod-bl31-uboot.fip` |

MTK U-Boot profile находится в **`filogic-ext.mk`**, который image Makefile включает дополнительно к основному `filogic.mk`. Имена файлов выше описывают suffixes правил с distro/version prefix; окончательные имена берутся из `profiles.json` и каталога результата. Выбран только squashfs, чтобы не удваивать артефакты за счёт EROFS.

Первичные источники: [stock/ubootmod profiles](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/image/filogic.mk), [MTK U-Boot profile](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/image/filogic-ext.mk), [image defaults/includes](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/image/Makefile), [stock DTS](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/dts/mt7986a-xiaomi-redmi-router-ax6000-stock.dts), [MTK U-Boot DTS](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/dts-ext/mt7986a-xiaomi-redmi-router-ax6000-mtkuboot.dts), [OpenWrt U-Boot DTS](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/dts/mt7986a-xiaomi-redmi-router-ax6000-ubootmod.dts).

Все три DTS включают один общий AX6000 `.dtsi`; физический Wi-Fi/SoC/порты не меняются из-за выбора разметки. Профиль ubootmod дополнительно выбирает сборку OpenWrt U-Boot/TF-A для своего bootloader artifact, что увеличивает время этой части, но не требует нового kernel/toolchain ABI. [Общий DTS](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/dts/mt7986a-xiaomi-redmi-router-ax6000.dtsi), [U-Boot device/dependency](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/boot/uboot-mediatek/Makefile).

Это **разные flash layouts**, не автоматически взаимозаменяемые файлы. Создание ubootmod artifacts само по себе не означает разрешения менять загрузчик пользователя. Для текущего роутера сначала выбирается stock image, по его `board_name`. Upgrade dispatcher stock пишет раздельные kernel/root UBI partitions; ubootmod использует `fit_do_upgrade`, mtkuboot — NAND path. [Upgrade rules](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/filogic/base-files/lib/upgrade/platform.sh).

## Почему одна сборка и один SDK на channel

`include/kernel.mk` задаёт `KERNEL_BUILD_DIR = linux-$(BOARD)_$(SUBTARGET)` и один `LINUX_DIR` с выбранной версией Linux. Профиль устройства не входит в путь сборки основного ядра. `.config.target` и `.config.override` собираются из общей верхней `.config`; kernel `.vermagic` вычисляется из отсортированных `=y/m` kernel options. Это объединённая конфигурация kernel modules выбранного target, а не три kernel configs по DTS. [Kernel paths](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/include/kernel.mk), [kernel config/vermagic](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/include/kernel-defaults.mk).

SDK archive именуется по board/subtarget/compiler/host и забирает `.config`, `Module.symvers`, kernel headers и scripts из этого единственного `LINUX_DIR`. Поэтому один экспорт SDK соответствует общей сборке трёх layouts. [SDK export](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/sdk/Makefile). Все три остаются `mediatek/filogic`, `aarch64_cortex-a53`. [Filogic target](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/target/linux/mediatek/filogic/target.mk).

`TARGET_PER_DEVICE_ROOTFS` создаёт rootfs variants из общей установленной базы, добавляя/удаляя DEVICE_PACKAGES и пользовательские DEVICE_EXTRA_PACKAGES. Набор дополнений хэшируется в ROOTFS_ID; одинаковые дополнения могут переиспользовать rootfs. Initramfs при разных ROOTFS_ID допускает отдельную временную копию kernel tree/упаковку; это дополнительная работа, но не три независимые сборки пакетов или отдельные опубликованные ABI. [Image selection/rootfs rules](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/include/image.mk).

## Точный выбор трёх устройств

В generated Kconfig для MULTI_PROFILE используются **`CONFIG_TARGET_DEVICE_...`** symbols, а не single-profile `CONFIG_TARGET_mediatek_filogic_DEVICE_...`. Минимальный stub до `make defconfig`:

```text
CONFIG_TARGET_mediatek=y
CONFIG_TARGET_mediatek_filogic=y
CONFIG_TARGET_MULTI_PROFILE=y
# CONFIG_TARGET_ALL_PROFILES is not set
CONFIG_TARGET_PER_DEVICE_ROOTFS=y
CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock=y
CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-mtkuboot=y
CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-ubootmod=y
CONFIG_TARGET_ROOTFS_SQUASHFS=y
# CONFIG_TARGET_ROOTFS_EROFS is not set
# CONFIG_USE_APK is not set
CONFIG_ALL_KMODS=y
```

К этому добавляются настройки SDK и проверенного AX6000 vendor defconfig, UI/язык/VPN, выбранные global image packages. После defconfig надо перечислить ВСЕ `CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_*=y`: их должно быть ровно три. Оставлять single-profile device selection вместе с MULTI_PROFILE нельзя. Source generator прямо рекомендует установить PER_DEVICE_ROOTFS в stub заранее, поскольку позднее включение оставляет уже предвыбранные пакеты глобально установленными. [Kconfig generation](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/scripts/target-metadata.pl).

## Vendor Wi-Fi, kmods и различия rootfs

У checked stock/ubootmod profiles DEVICE_PACKAGES содержит только `kmod-leds-ws2812b`; штатные `kmod-mt7915e`, `kmod-mt7986-firmware`, `mt7986-wo-firmware` удалены из этих AX6000 profiles. MTK U-Boot profile не задаёт DEVICE_PACKAGES. Поэтому **поставить `CONFIG_PACKAGE_kmod-leds-ws2812b=y` глобально** полезно: LEDs присутствуют во всех трёх, а rootfs extras можно нормализовать, например одинаковым DEVICE_EXTRA_PACKAGES для трёх профилей. Различие LED selection не требует другого ядра, если в единой сборке модуль уже выбран.

Vendor chain нужно выбирать **глобально одинаково**: MT7986/MT7976, AX6000 SKU, mt_wifi 7.6.7.3, MT7986 firmware 20260601, conninfra/WARP/HNAT и mtwifi-cfg-ucode/iwinfo-ucode/LuCI. Это один физический AX6000 с разными partitions; отдельные per-device варианты драйвера/SKU не нужны и не поддерживаются одной глобальной Kconfig. Общие image packages отмечаются `y`; дополнительные публикуемые VPN/mwan3/IPsec пакеты могут оставаться `m`, а не попадать во все образы. [AX6000 defconfig](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/defconfig/mt7986-ax6000.config), [vendor recipe](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/mtk/drivers/mt_wifi/Makefile), [ucode dependencies](FEEDS-6.12.md).

В recursive tree checked recommended source вообще нет `package/kernel/mt76/Makefile`; не надо возвращать его или добавлять upstream mt76 feed ради других профилей. При ALL_KMODS возможна сборка mac80211/cfg80211 и других общих модулей для каталога пакетов; **сборка `m` не равна установке в image**. Проверка каждого image manifest обязана подтвердить vendor packages и отсутствие open-source `kmod-mt7915e`/`kmod-mt76*` Wi-Fi driver packages. Одновременную установку двух Wi-Fi стеков нельзя считать допустимой. Не включать upstream PPE hardware-flow path рядом с vendor HNAT.

Три AX6000 profile blocks, все три layout DTS, `filogic-ext.mk`, image/kernel/SDK/Kconfig rules одинаковы между recommended/latest. Полный `filogic.mk` и upgrade script отличаются из-за изменений других устройств; AX6000 profile packaging blocks и обсуждаемые AX6000 dispatcher branches сохранены. Само ядро/HNAT firmware channel отличается, поэтому сравнивать ABI между channels только по board/arch нельзя.

## Практический порядок и проверки

1. После завершения 6.6 создать recommended multi-profile inputs с общими feeds, vendor SKU и ALL_KMODS. Выполнить одну ограниченную по ресурсам сборку, экспортировать один SDK/пакетный каталог, получить три образа. Затем таким же способом latest; два тяжёлых build channels одновременно на сервере не запускать.
2. Для каждого channel проверить получившуюся `.config`, точные три devices и отсутствие посторонних profiles; для каждого image — manifest, packages, архитектуру, metadata `supported_devices`, размер и соответствующий layout DTS. Наличие файла не заменяет эти проверки.
3. Сопоставить kernel version/ABI в трёх manifests, в `kernel` IPK, зависимостях kmods, экспортированном SDK и package-rebuild output. Если хоть один расходится, общий feed не публиковать до выяснения причины; при действительно различающемся ABI выделить другой build/feed.
4. При совпадении использовать **один feed и SDK внутри channel**, а на странице — отдельные image records для stock/mtkuboot/ubootmod. Installer kernel-feed должен проверять реальную ABI/arch; выбор firmware image дополнительно проверяет board/layout. Для пользователя текущий первый тест — stock. Другие layouts обозначать по реально выполненной проверке; сборка сама не подтверждает загрузку на устройстве.
5. Если сначала собирать stock отдельно, а остальные devices добавить позднее, изменение общей конфигурации способно изменить kernel ABI. Тогда снова экспортировать согласованный SDK/feed/stock image, а не объявлять прежний stock ABI общим по предположению. Для экономии и согласованности лучше сразу компилировать три layouts, но тестировать stock первым.

Оснований отвергать multi-profile здесь не обнаружено: один SoC, одна архитектура, одинаковый vendor SKU, три поддержанных упаковки одного target. Раздельные полные сборки понадобятся при различной kernel config/driver version/SDK ABI, а не из-за одного изменения DTS flash partitions.
