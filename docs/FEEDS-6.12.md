# Источники пакетов для двух вариантов AX6000 / Linux 6.12

Проверено 2026-10-12, без запуска сборок и без изменения входов существующей сборки 6.6.

## Зафиксированные firmware и feeds

Предлагаемые firmware revisions:

- `recommended`: `chasey-dev/immortalwrt-mt798x-rebase`, commit `ae65e1de69971b5a2e3cf7e39390ee93f274d488`, Linux 6.12.108; кандидат до последних изменений HNAT, а не прошивка с подтверждённой эксплуатацией на нашем stock AX6000.
- `latest`: тот же repository, commit `a99b0888b88f134f56bcb2a7d93027b5d20638a9`, Linux 6.12.112; включает последние изменения и известные открытые регрессии HNAT. Подробности: [KERNEL-6.12.md](KERNEL-6.12.md).

Их `feeds.conf.default` побайтово одинаковы: packages/LuCI берутся из ImmortalWrt, routing/telephony/video из OpenWrt; **все ветки `openwrt-25.12`**. [Recommended feeds](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/feeds.conf.default), [latest feeds](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/a99b0888b88f134f56bcb2a7d93027b5d20638a9/feeds.conf.default).

Ниже текущие полные SHA этих веток, пригодные как **одинаковые закреплённые feeds для обоих вариантов**. Это состояние на дату исследования, а не восстановленные feeds сторонних прошлых release-сборок. Использование свежих feeds рядом с older firmware требует собственной проверки сборки; слово recommended относится к firmware base.

| Feed | Git URL | Закреплённый commit | Дата commit UTC |
| --- | --- | --- | --- |
| packages | `https://github.com/immortalwrt/packages.git` | [52ddbd11d66106615543af7352bf84ae20e5f1f8](https://github.com/immortalwrt/packages/commit/52ddbd11d66106615543af7352bf84ae20e5f1f8) | 2026-10-10 02:44:55 |
| luci | `https://github.com/immortalwrt/luci.git` | [d6167ea0645cbd1327708d85f94824f42d0eb872](https://github.com/immortalwrt/luci/commit/d6167ea0645cbd1327708d85f94824f42d0eb872) | 2026-09-06 07:54:19 |
| routing | `https://github.com/openwrt/routing.git` | [23912fb99fc2fd0888466eb5f4f550651bc12aa3](https://github.com/openwrt/routing/commit/23912fb99fc2fd0888466eb5f4f550651bc12aa3) | 2026-10-11 13:54:03 |
| telephony | `https://github.com/openwrt/telephony.git` | [2618106d5846a4a542fdf5809f0d3ed228ce439b](https://github.com/openwrt/telephony/commit/2618106d5846a4a542fdf5809f0d3ed228ce439b) | 2026-01-04 00:02:58 |
| video | `https://github.com/openwrt/video.git` | [094bf58da6682f895255a35a84349a79dab4bf95](https://github.com/openwrt/video/commit/094bf58da6682f895255a35a84349a79dab4bf95) | 2025-11-27 11:18:29 |
| amneziawg, внешний | `https://github.com/2Grey/awg-openwrt.git` | [3f3ec68d594c8e965c988af75ac5314616229c88](https://github.com/2Grey/awg-openwrt/commit/3f3ec68d594c8e965c988af75ac5314616229c88) | 2026-09-08 05:16:34 |

Для мониторинга первые пять refs — `refs/heads/openwrt-25.12`, внешний AmneziaWG — `refs/heads/master`. Профиль должен сохранять полные commit SHA; мониторинг предлагает обновления, а не меняет входы уже запущенной сборки. Нельзя переносить pins/бинарные пакеты 24.10 из текущего 6.6 профиля.

## Доступные версии нужных программ

| Пакет | Версия в закреплённом исходнике | UI и особенности |
| --- | --- | --- |
| Tailscale | 1.98.3-r1 | [Рецепт](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/tailscale/Makefile); зависит от ca-bundle и kmod-tun |
| PBR | 1.2.2-r20 | `luci-app-pbr` также 1.2.2-r20; nftables/json, ip-full, resolveip. [PBR](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/pbr/Makefile), [LuCI](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-pbr/Makefile) |
| mwan3 | 2.12.0-r3 | `luci-app-mwan3`; этот рецепт всё ещё требует iptables/ipset/ip6tables и соответствующие extensions. Не считать его native nft-only MultiWAN. [Рецепт](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/mwan3/Makefile) |
| banIP | 1.8.12-r3 | `luci-app-banip` 1.8.12-r4; firewall4/gawk/jsonfilter/rpcd. [Программа](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/banip/Makefile), [UI](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-banip/Makefile) |
| OpenVPN | 2.7.6-r1 | `openvpn-openssl`, `luci-app-openvpn`; UI требует `luci-compat`. DCO при включении требует kmod-ovpn-backports и должен быть проверен отдельно. [Рецепт](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/openvpn/Makefile), [UI](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-openvpn/Makefile) |
| strongSwan | 6.0.3-r2 | Для IKE/IPsec: `strongswan-default`, `strongswan-swanctl`, при необходимости `luci-app-strongswan-swanctl`; default подтягивает charon/plugins/swanctl, UI — swanmon. [Программа](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/strongswan/Makefile), [UI](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-strongswan-swanctl/Makefile) |
| WireGuard tools | 1.0.20250521-r1 | Находится в firmware tree, а не packages feed; модуль kmod-wireguard собирается из выбранного ядра. [Инструменты](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/network/utils/wireguard-tools/Makefile) |

Ядерные определения IPsec находятся в firmware `netsupport.mk`: для ESP IPv4/IPv6 нужны `kmod-ipsec4`/`kmod-ipsec6` и зависимости; просто strongswan userspace не заменяет эти модули. [Определения kmods](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/kernel/linux/modules/netsupport.mk). `CONFIG_ALL_KMODS`/явный список должны действительно оставить необходимые модули в публикуемом результате.

Для GeoIP доступны `geoip-shell` 0.5.2-r1, `geoipupdate` 8.0.0-r1, `geoview` 0.2.6-r1 и раздельные `v2ray-geodata` GeoIP/GeoSite data packages. Это разные способы применения данных, а не один взаимозаменяемый GeoIP пакет; банIP остаётся отдельным инструментом firewall. [GeoIP shell](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/geoip-shell/Makefile), [GeoIP updater](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/geoipupdate/Makefile), [Geo data](https://github.com/immortalwrt/packages/blob/52ddbd11d66106615543af7352bf84ae20e5f1f8/net/v2ray-geodata/Makefile).

## AmneziaWG 3.1

В проверенных штатных packages/LuCI feeds 25.12 AmneziaWG отсутствует; внешний feed обязателен. В предложенном 2Grey commit:

- `kmod-amneziawg`: **3.1.20260906-r1**, исходники upstream tag `v3.1.20260906`, разрешённый commit `4569c4c67f3a57414969260cafbbd04694fbaae0`. [Рецепт](https://github.com/2Grey/awg-openwrt/blob/3f3ec68d594c8e965c988af75ac5314616229c88/kmod-amneziawg/Makefile), [исходники модуля](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module/tree/4569c4c67f3a57414969260cafbbd04694fbaae0).
- `amneziawg-tools`: **3.1.20260812-r1**, upstream tag `v3.1.20260812`, commit `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843`. Пакет также устанавливает netifd protocol helper и watchdog. [Рецепт](https://github.com/2Grey/awg-openwrt/blob/3f3ec68d594c8e965c988af75ac5314616229c88/amneziawg-tools/Makefile).
- `luci-proto-amneziawg`: **3.1.1**, зависимости tools/ucode/luci-lib-uqr/resolveip. Для образа использовать этот protocol package; одновременно старый `luci-app-amneziawg` не нужен. [Рецепт](https://github.com/2Grey/awg-openwrt/blob/3f3ec68d594c8e965c988af75ac5314616229c88/luci-proto-amneziawg/Makefile).

Упаковка использует обычные OpenWrt `BuildPackage`/`KernelPackage` и LuCI framework: отдельная APK-реализация этого feed не требуется при выборе IPK. Реальная совместимость компиляции с каждым новым SDK/ABI должна подтверждаться нашей сборкой; готовые официальные APK kmods 25.12 не подходят для собственного IPK ядра rebase.

## Цепочка vendor Wi-Fi 6.12

AX6000 defconfig уже выбирает **mtwifi-cfg-ucode**, `iwinfo-ucode`, datconf/l1util bindings, `luci-app-mtwifi-cfg`, драйвер 7.6.7.3 и firmware MT7986 20260601. Это следует сохранить вместе с настройками MT7986/MT7976/AX6000, отдельно заменив выбранный device на stock profile. [Defconfig](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/defconfig/mt7986-ax6000.config).

`luci-app-mtwifi-cfg` требует `mtwifi-cfg-ucode` 3-r1. Последний конфликтует со старым Lua `mtwifi-cfg`, требует wifi-scripts, iwinfo-ucode и ucode-модули fs/log/ubus/uci/datconf/l1parser. `iwinfo-ucode` 1.0-r1 требует ucode-mod-iwinfo и имеет условие **`@!PACKAGE_iwinfo`**: перенос старого выбора обычного iwinfo может сделать новую vendor-цепочку недоступной. Это относится к CLI `iwinfo`, а не к необходимой библиотеке libiwinfo. [mtwifi-cfg-ucode](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/mtk/applications/mtwifi-cfg-ucode/Makefile), [iwinfo-ucode](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/network/utils/iwinfo-ucode/Makefile), [vendor LuCI](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/blob/ae65e1de69971b5a2e3cf7e39390ee93f274d488/package/mtk/applications/luci-app-mtwifi-cfg/Makefile).

Рецепты этой ucode-цепочки, wifi-scripts и настройки упаковки APK/opkg побайтово одинаковы между указанными recommended/latest firmware commits. Их нельзя заменять vendor-пакетами из старой сборки 24.10.

## IPK/opkg и LuCI 25.12

Fork сохраняет выключение APK: `CONFIG_USE_APK=n` выбирает opkg и IPK-ветку упаковки. Доказательства и ссылки приведены в [KERNEL-6.12.md](KERNEL-6.12.md). Нужно проверять **получившийся `.config` после defconfig**, отсутствие apk в образе, наличие opkg и фактические `.ipk` артефакты; один исходный override не подтверждает результат.

LuCI 25.12 совместим с этим выбором по исходникам: `luci-app-package-manager` предоставляет старое имя `luci-app-opkg`, UI выбирает APK/OPKG через system feature, helper переключается на opkg при отсутствии `/usr/bin/apk`, ACL разрешает opkg конфиги и загрузку `/tmp/upload.ipk`. Следовательно, брать LuCI 24.10 для сохранения IPK не требуется. Предпочтительно явно выбирать **`luci-app-package-manager`**. [Makefile](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-package-manager/Makefile), [UI](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-package-manager/htdocs/luci-static/resources/view/package-manager.js), [helper](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-package-manager/root/usr/libexec/package-manager-call), [ACL](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/applications/luci-app-package-manager/root/usr/share/rpcd/acl.d/luci-app-package-manager.json).

Важно: `luci-light` не включает package manager; `luci` включает его. Если базовый defconfig оставляет luci-light, требуемое приложение следует добавить явно. [luci-light](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/collections/luci-light/Makefile), [luci](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/collections/luci/Makefile).

Русские `.po` присутствуют у base, PBR, mwan3, banIP, OpenVPN, strongswan-swanctl и package-manager; во внешнем feed есть ru AmneziaWG. При `CONFIG_LUCI_LANG_ru=y` можно включать в образ `luci-i18n-base-ru`, `luci-i18n-pbr-ru`, `luci-i18n-mwan3-ru`, `luci-i18n-banip-ru`, `luci-i18n-openvpn-ru`, `luci-i18n-strongswan-swanctl-ru`, `luci-i18n-package-manager-ru`, `luci-i18n-amneziawg-ru` согласно выбранным UI. Наличие перевода не означает стопроцентную переведённость. [Base ru](https://github.com/immortalwrt/luci/blob/d6167ea0645cbd1327708d85f94824f42d0eb872/modules/luci-base/po/ru/base.po), [AmneziaWG ru](https://github.com/2Grey/awg-openwrt/blob/3f3ec68d594c8e965c988af75ac5314616229c88/luci-proto-amneziawg/po/ru/amneziawg.po).

У vendor `luci-app-mtwifi-cfg` в проверенном firmware tree отдельного ru перевода нет. Для `luci-proto-wireguard` в проверенном LuCI tree нет отдельного собственного ru каталога: не добавлять несуществующий `luci-i18n-wireguard-ru`; общие переводы интерфейса относятся к base. Это ограничение локализации, не сборки WireGuard.

## Проверки при будущей реализации

Сборка обеих revisions должна подтвердить разрешённые SHA всех шести feeds, выбранный stock profile, сохранённую vendor ucode-цепочку, пакетный формат, внешнее AWG 3.1, русский UI и зависимости VPN/IPsec. SDK, модули ядра и пакеты recommended/latest необходимо публиковать раздельно по ABI. Эти read-only результаты дают конкретные входы для реализации, но не заменяют успешную компиляцию и проверку роутера.
