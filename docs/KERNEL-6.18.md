# Linux 6.18 и vendor-драйверы Redmi AX6000

Дата исследования: 2026-10-12. Целевая плата: MT7986, `xiaomi,redmi-router-ax6000-stock`; нужен именно стек vendor `mt_wifi` + conninfra + WARP + HNAT, с сохранением производительности существующей прошивки.

## Результат

**Подтверждённого опубликованного порта полного vendor-стека для Redmi AX6000/MT7986 на Linux 6.18 не найдено.** Это результат проверки доступных источников, а не утверждение о технической невозможности такого порта. Создавать репозиторий 6.18 как уже готовую альтернативу 6.6/6.12 на основании найденных материалов пока нельзя: условие пользователя о наличии подходящего vendor-стека не подтверждено.

Есть конкретный проект переноса proprietary MTWiFi на 6.18.38, но для **MT7988 + MT7992 / TP-Link TL-7DR7299 v1**, с **отключёнными HNAT и WARP** и без подтверждённой проверки на устройстве. Он даёт полезные примеры исправлений API, но не готовую интеграцию для AX6000. [Состояние этого порта](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/docs/PORTING_STATUS.md).

## Что проверено в основных источниках

| Источник и зафиксированная версия | Что действительно найдено | Значение для AX6000 |
| --- | --- | --- |
| OpenWrt main, `1daf1410c575360bc4ec4896281f0140d528d34d` | MediaTek target выбирает Linux 6.18 | Подтверждает доступность ядра, но не proprietary-драйверов. [Makefile](https://github.com/openwrt/openwrt/blob/1daf1410c575360bc4ec4896281f0140d528d34d/target/linux/mediatek/Makefile) |
| MediaTek `mtk-openwrt-feeds`, `08196fa6e5d338ce88aa0002f781e2d24289b1f1` | Линии 21.02, 24.10 и 25.12; опубликованная инструкция 25.12 описывает SoftMAC/mt76 на ядре 6.12, включая mac80211 backports 6.18 | Версия backports не означает ядро 6.18 и не означает vendor `mt_wifi`. [Инструкция производителя](https://github.com/mediatek/mtk-openwrt-feeds/blob/08196fa6e5d338ce88aa0002f781e2d24289b1f1/autobuild/unified/Readme-6.12.md) |
| Тот же официальный feed | Есть backport LVTS из 6.18 внутри `patches-6.12` | Патч thermal-драйвера не подтверждает порт Wi-Fi/HNAT/WARP. [Реальный путь патча](https://github.com/mediatek/mtk-openwrt-feeds/blob/08196fa6e5d338ce88aa0002f781e2d24289b1f1/25.12/files/target/linux/mediatek/patches-6.12/999-lvts-01-thermal-mediatek-backport-kernel-6.18-lvts-driver.patch) |
| `chasey-dev/immortalwrt-mt798x-rebase`, ветка 25.12, `a99b0888b88f134f56bcb2a7d93027b5d20638a9` | Полный vendor-стек с профилем AX6000 имеется для **6.12** | Это подходящая исходная база для двух запрошенных вариантов 6.12, но не доказательство совместимости с 6.18. [Target](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/a99b0888b88f134f56bcb2a7d93027b5d20638a9/target/linux/mediatek/Makefile), [исследование 6.12](KERNEL-6.12.md) |
| `coolsnowwolf/lede`, `d5ccef5f796981c79811f27722596173482a35e3` | Патч proprietary `mt7615d` для 6.18 действительно существует | Это драйвер 5.0.4.0 с зависимостью `TARGET_ramips`, а не драйвер MT7986/AX6000. [Рецепт](https://github.com/coolsnowwolf/lede/blob/d5ccef5f796981c79811f27722596173482a35e3/package/lean/mt/drivers/mt7615d/Makefile), [патч 6.18](https://github.com/coolsnowwolf/lede/blob/d5ccef5f796981c79811f27722596173482a35e3/package/lean/mt/drivers/mt7615d/patches-6.18/100-linux-6.18-kbuild-ccflags.patch) |

Также проверены ветки исходных репозиториев padavanonly/hanwckf и rebase, GitHub-поиск по именам `mt_wifi`, `mtk_warp`, `mtk_hnat`, `mt7986-fw`, архиву `mt79xx_20250408`, номерам ядра, Redmi AX6000 и MT7986; поиск репозиториев, issues и форков; независимый веб-поиск. Отдельно проверялись названия, в которых 6.18 относится к backports, времени в dmesg или другому SoC. Результат поиска не гарантирует отсутствия частного SDK или неиндексированного порта.

## Реальный сторонний порт 6.18: возможности и границы доказательств

Репозиторий [mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/tree/44981664105f9592e2d182c88d1c096199253552) на commit `44981664105f9592e2d182c88d1c096199253552` содержит набор подготовки дерева и патчей. Это не готовое дерево AX6000.

В опубликованном автором manifest указано ядро `6.18.38~20f160679f47f9600f3fe0acf136ccaf-r1`, а в записи проверки сборки — proprietary-модули семейства MTWiFi7/HWIFI. Пакеты имеют формат APK. Автор сообщает о сформированном образе размером 22 151 448 байт и приводит SHA-256. В доступном репозитории отсутствуют сам образ и GitHub release с ним: независимо скачать и проверить бинарный результат не удалось. Это **исходные патчи плюс опубликованный автором отчёт о сборке**, а не собственная проверка готовой прошивки. [Manifest](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/docs/verified-build.manifest), [отчёт](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/docs/verified-build.txt).

Автор явно ограничивает результат компиляцией, упаковкой и статическими проверками; подтверждения загрузки, радиочасти и длительной стабильности нет. HNAT/WARP оставлены на последующий этап. Поэтому этот проект не подтверждает ни MT7986, ни stock-разметку AX6000, ни сохранение аппаратного ускорения. [Границы результата](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/docs/PORTING_STATUS.md).

Скрипт подготовки фиксирует OpenWrt commit `e5c7612b1664d9e533ed5ddb6a861b4d0a62daa8`, но vendor-драйверы забирает из **движущейся ветки** padavanonly `mt798x-mt799x-6.6-mtwifi`. Для воспроизводимого собственного порта пришлось бы дополнительно закрепить vendor commit и feeds. [Скрипт подготовки](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/prepare-tree.sh).

| Опубликованная адаптация | Возможная польза для отдельного будущего порта |
| --- | --- |
| [Замена `from_timer`](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/patches/mt_wifi7/980-linux-6.18-from-timer-compat.patch), [удаление timer через новый API](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/patches/mt_wifi7/981-linux-6.18-timer-delete-sync.patch) | Примеры исправлений несовместимых timer API |
| [Исправление KBUILD_EXTMOD](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/patches/mt_hwifi/976-linux-6.18-kbuild-src-dir.patch), [page-fragment cache](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/patches/mt_hwifi/977-linux-6.18-page-frag-cache-drain.patch) | Образцы поправок для внешних модулей HWIFI; применимость к старому MT7986-стеку нужно проверять отдельно |
| [Адаптация dummy netdev](https://github.com/mlwn-2023/immortalwrt-7dr7299-6.18-mtwifi/blob/44981664105f9592e2d182c88d1c096199253552/patches/mt_wifi_cmn/003-linux-6.18-init-dummy-netdev.patch) | Ещё один пример изменений сетевого API; иной набор исходников |

Проверен и используемый MT7986 исходный архив `mt79xx_20250408-705eb4.tar.xz`: в `rtmp_timer.h` остаётся `from_timer`, в `rt_linux.c` — `del_timer_sync`, в WARP `warp_bm_v2.c` — `del_timer`. В [заголовке Linux v6.18](https://github.com/torvalds/linux/blob/v6.18/include/linux/timer.h) вместо этих имён предоставлены новые timer API. Это конкретный пример необходимого переноса, но не исчерпывающий список несовместимостей. Простая замена `KERNEL_PATCHVER` недостаточна. [Архив исходников MT7986](https://github.com/hanwckf/immortalwrt-mt798x/blob/openwrt-21.02/dl/mt79xx_20250408-705eb4.tar.xz).

## Что необходимо до создания пригодной ветки AX6000 6.18

1. Закрепить полный порт **MT7986**: `mt_wifi` 7.6.7.3, conninfra, WARP v2, HNAT, Ethernet/DSA-интеграцию, соответствующие firmware и конфигурацию Wi-Fi. Порт MTWiFi7/HWIFI для MT7992 нельзя считать заменой этой цепочки.
2. Получить успешную сборку всех модулей из одной конфигурации ядра; проверить symbol versions, vermagic, зависимости, профиль `xiaomi,redmi-router-ax6000-stock` и разметку. Публиковать 6.18 в отдельном репозитории пакетов с собственным ABI; модули не совместимы с существующим 6.6.95.
3. Подтвердить на AX6000 загрузку, работу обоих радиодиапазонов, переключение портов/VLAN и реальные пути HNAT/WARP. Компиляция без ускорения не выполняет требование о сохранении vendor-производительности.
4. Измерить LAN/WAN/WLAN, нагрузку CPU и длительную работу; потом проверить VPN/PBR/MultiWAN с аппаратным ускорением. Измерений, доказывающих превосходство или равную производительность AX6000 vendor на 6.18, не найдено.

Эти пункты означают самостоятельный экспериментальный перенос, а не настройку уже найденного рабочего SDK. Текущие 6.6 и два запрошенных варианта 6.12 не меняются этим исследованием; подтверждённая подходящая база для 6.12 описана в [KERNEL-6.12.md](KERNEL-6.12.md). До появления MT7986-порта создание третьего репозитория как готовой прошивки остаётся неподтверждённым условием.

Исследование выполнено чтением исходников, небольших архивов и опубликованных метаданных. Сборки 6.18 и изменение текущего процесса сборки 6.6 не выполнялись.
