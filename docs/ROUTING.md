# VPN, GeoIP и MultiWAN

AmneziaWG устанавливается пакетами `kmod-amneziawg`, `amneziawg-tools` и
`luci-proto-amneziawg`. Используется поддержка AWG3.1 из
[awg-openwrt](https://github.com/2Grey/awg-openwrt), с исходниками
[официального модуля](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module)
и [инструментов](https://github.com/amnezia-vpn/amneziawg-tools).

В PBR добавить имя настроенного AWG-интерфейса в `supported_interface`; пример
для интерфейса с именем `awg0`:

```uci
config pbr 'config'
        list supported_interface 'awg0'
```

PBR и mwan3 требуют согласованных приоритетов routing rules и раздельных mark
masks. Проверять существующую конфигурацию перед включением обоих сервисов.
Пример приоритета из [документации PBR](https://docs.mossdef.org/pbr/):
`option uplink_ip_rules_priority '900'`. Пример не является готовой сетевой
конфигурацией: WAN-имена, шлюзы и политики задаются владельцем роутера.

banIP обеспечивает GeoIP-блокировки. Для направления стран через VPN нужны
CIDR-наборы назначения и политики PBR; сама установка базы GeoIP трафик не
перенаправляет. Для маршрутизации доменов через DNS-наборы нужен подходящий
`dnsmasq-full` и использование клиентами DNS роутера. Перед заменой dnsmasq
проверить текущие конфиги и конфликты пакетов.

Обновлять конкретные пакеты по необходимости. Массовый `opkg upgrade` и
принудительная установка несовместимых модулей не являются частью процедуры
подключения этого репозитория.
