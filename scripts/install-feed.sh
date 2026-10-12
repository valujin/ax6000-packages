#!/bin/sh
# Run on the router AFTER installing the matching firmware. Never installs packages.
set -eu
base=${PACKAGE_REPOSITORY_URL:-https://valujin.github.io/ax6000-packages}
expected_fingerprint=eaad5e29fcc9ed69
case "$base" in https://*) ;; *) echo 'Repository URL must use HTTPS' >&2; exit 1 ;; esac
for tool in wget jsonfilter opkg usign awk cat cp mv mkdir chmod mktemp id rm; do
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
# opkg has architecture priorities, but no source priority. Moving feeds can
# supply higher package versions and a different virtual kernel identity.
# Preflight every loaded configuration before changing any keys or feeds.
fail() { printf '%s\n' "$*" >&2; exit 1; }
[ ! -L /etc/opkg ] && [ -d /etc/opkg ] && [ -w /etc/opkg ] || fail 'Unsafe or unwritable /etc/opkg directory'
[ ! -L /etc/opkg.conf ] && [ -f /etc/opkg.conf ] || fail 'Missing or symlink /etc/opkg.conf'
[ -w /etc ] || fail 'Unwritable /etc directory'
if [ -e /etc/opkg/keys ] || [ -L /etc/opkg/keys ]; then
    [ ! -L /etc/opkg/keys ] && [ -d /etc/opkg/keys ] && [ -w /etc/opkg/keys ] || fail 'Unsafe or unwritable opkg keys directory'
fi
key=/etc/opkg/keys/$fingerprint
[ ! -L "$key" ] || fail "Refusing symlink signing key: $key"
if [ -e "$key" ]; then
    [ -f "$key" ] || fail "Signing key is not a regular file: $key"
fi
line="src/gz ax6000_custom $feed_url"
feeds=/etc/opkg/customfeeds.conf
: > "$tmp/configs"
for path in /etc/opkg.conf /etc/opkg/*.conf; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    case "$path" in *'
'*) fail 'Configuration filename contains a newline' ;; esac
    [ ! -L "$path" ] || fail "Refusing symlink configuration: $path"
    [ -f "$path" ] && [ -r "$path" ] || fail "Configuration is not a readable regular file: $path"
    printf '%s\n' "$path" >> "$tmp/configs"
done
if [ ! -e "$feeds" ]; then
    printf '%s\n' "$feeds" >> "$tmp/configs"
fi
# Backup directories never match /etc/opkg/*.conf. Stage every replacement
# first; cp -p retains the original file ownership and permissions.
backup=$(mktemp -d /etc/opkg/ax6000-feed-backup.XXXXXX)
mkdir -p "$backup/original/opkg" "$backup/staged/opkg"
printf 'Backup: %s\n' "$backup"
while IFS= read -r path; do
    relative=${path#/etc/}
    staged=$backup/staged/$relative
    if [ -f "$path" ]; then
        cp -p "$path" "$backup/original/$relative"
        cp -p "$path" "$staged"
        input=$path
    else
        : > "$staged"
        chmod 644 "$staged"
        input=/dev/null
    fi
    awk -v expected="$line" -v seen="$(test -f "$tmp/own-kept" && printf 1 || printf 0)" -v marker="$tmp/own-kept" '
        $1 == "src" || $1 == "src/gz" {
            if ($0 == expected && !seen) {
                print; seen = 1; print "1" > marker
            } else {
                print "# Disabled by AX6000 matching feed installer: " $0
            }
            next
        }
        {print}
    ' "$input" > "$tmp/filtered"
    cat "$tmp/filtered" > "$staged"
done < "$tmp/configs"
if [ ! -f "$tmp/own-kept" ]; then
    printf '\n%s\n' "$line" >> "$backup/staged/opkg/customfeeds.conf"
fi
mkdir -p /etc/opkg/keys
cp "$tmp/repository.pub" "$backup/staged/repository.pub"
chmod 644 "$backup/staged/repository.pub"
mv "$backup/staged/repository.pub" "$key"
while IFS= read -r path; do
    relative=${path#/etc/}
    mv "$backup/staged/$relative" "$path"
done < "$tmp/configs"
opkg update
printf 'Verified matching feed enabled. Kernel ABI: %s\n' "$kernel"
