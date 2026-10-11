#!/bin/sh
# Run on the router AFTER installing the matching firmware. Never installs packages.
set -eu
base=${PACKAGE_REPOSITORY_URL:-https://valujin.github.io/ax6000-packages}
expected_fingerprint=eaad5e29fcc9ed69
case "$base" in https://*) ;; *) echo 'Repository URL must use HTTPS' >&2; exit 1 ;; esac
for tool in wget jsonfilter opkg usign; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || { echo 'Run on router as root' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
wget -q -O "$tmp/profile.json" "$base/profile.json"
wget -q -O "$tmp/repository.pub" "$base/repository.pub"
arch=$(jsonfilter -i "$tmp/profile.json" -e '@.firmware.architecture')
kernel=$(jsonfilter -i "$tmp/profile.json" -e '@.firmware.kernel_version')
profile=$(jsonfilter -i "$tmp/profile.json" -e '@.id')
[ -n "$profile" ] && [ -n "$kernel" ] && [ -n "$arch" ] || { echo 'Invalid repository profile' >&2; exit 1; }
case "$profile" in *[!a-z0-9-]*) echo 'Invalid profile id' >&2; exit 1 ;; esac
case "$kernel" in *[!a-zA-Z0-9.~_+-]*) echo 'Invalid kernel version' >&2; exit 1 ;; esac
installed=$(opkg status kernel | awk '/^Version: / {print $2}')
[ "$installed" = "$kernel" ] || { echo "Kernel ABI mismatch: router=$installed repository=$kernel" >&2; exit 1; }
opkg print-architecture | awk -v expected="$arch" '$2 == expected {found=1} END {exit !found}' || { echo 'Architecture mismatch' >&2; exit 1; }
fingerprint=$(usign -F -p "$tmp/repository.pub")
[ "$fingerprint" = "$expected_fingerprint" ] || { echo 'Unexpected repository signing key' >&2; exit 1; }
feed_url="$base/feeds/$profile/$kernel"
wget -q -O "$tmp/Packages" "$feed_url/Packages"
wget -q -O "$tmp/Packages.sig" "$feed_url/Packages.sig"
usign -V -m "$tmp/Packages" -p "$tmp/repository.pub" -x "$tmp/Packages.sig"
mkdir -p /etc/opkg/keys
cp "$tmp/repository.pub" "/etc/opkg/keys/$fingerprint"
chmod 644 "/etc/opkg/keys/$fingerprint"
line="src/gz ax6000_custom $feed_url"
feeds=/etc/opkg/customfeeds.conf
touch "$feeds"
if awk '$2 == "ax6000_custom" {found=1} END {exit !found}' "$feeds"; then
    awk -v expected="$line" '$2 == "ax6000_custom" && $0 != expected {bad=1} END {exit bad}' "$feeds" || {
        echo 'Existing ax6000_custom feed differs; review customfeeds.conf before replacing it.' >&2; exit 1;
    }
else
    printf '\n%s\n' "$line" >> "$feeds"
fi
opkg update
printf 'Verified feed added. Kernel ABI: %s\n' "$kernel"
