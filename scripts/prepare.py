#!/usr/bin/env python3
"""Validate exact-firmware SDK inputs and prepare package-only selections."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit

COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")
SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]*\Z")
KERNEL_PACKAGE = re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)~([0-9a-f]{32})-r([0-9]+)\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def https_url(value, label):
    require(isinstance(value, str) and not re.search(r"[\s'\"\\^;]", value),
            f"{label} must be an unambiguous HTTPS URL")
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.fragment, f"{label} must use HTTPS")


def feed_pins(profile, lock):
    updates = lock.get("feeds", {})
    require(isinstance(updates, dict), "lock feeds must be an object")
    names = {feed["name"] for feed in profile["feeds"]}
    require(not (updates.keys() - names), "lock contains feeds absent from the profile")
    result = {}
    for feed in profile["feeds"]:
        pin = updates.get(feed["name"], feed["commit"])
        require(isinstance(pin, str) and COMMIT.fullmatch(pin),
                f"feed {feed['name']} needs a full 40-character commit")
        result[feed["name"]] = pin.lower()
    return result


def validate(profile, lock):
    require(isinstance(profile, dict), "profile must be an object")
    require(profile.get("enabled") is True,
            "profile is disabled: collect the router ABI and exact firmware SDK before enabling")
    require(profile.get("schema_version") == 1, "unsupported profile schema_version")
    require(isinstance(profile.get("id"), str) and NAME.fullmatch(profile["id"]), "invalid profile id")
    firmware = profile.get("firmware", {})
    require(isinstance(firmware, dict), "firmware must be an object")
    https_url(firmware.get("repository"), "firmware repository")
    require(isinstance(firmware.get("commit"), str) and COMMIT.fullmatch(firmware["commit"]),
            "firmware commit needs a full 40-character commit")
    for field in ("kernel_version", "target", "subtarget", "architecture"):
        value = firmware.get(field)
        pattern = KERNEL_PACKAGE if field == "kernel_version" else NAME
        require(isinstance(value, str) and pattern.fullmatch(value), f"invalid firmware {field}")
    sdk = profile.get("sdk")
    require(isinstance(sdk, dict), "exact firmware SDK URL and SHA256 are required")
    https_url(sdk.get("url"), "SDK URL")
    require(isinstance(sdk.get("sha256"), str) and SHA256.fullmatch(sdk["sha256"]),
            "SDK SHA256 needs 64 hexadecimal characters")
    feeds = profile.get("feeds")
    require(isinstance(feeds, list) and feeds, "at least one pinned feed is required")
    names = set()
    for feed in feeds:
        require(isinstance(feed, dict), "feed must be an object")
        name = feed.get("name")
        require(isinstance(name, str) and NAME.fullmatch(name) and name not in names,
                "invalid or duplicate feed name")
        names.add(name)
        https_url(feed.get("url"), f"feed {name} URL")
        require(isinstance(feed.get("commit"), str) and COMMIT.fullmatch(feed["commit"]),
                f"feed {name} profile pin needs a full 40-character commit")
    packages = profile.get("packages")
    require(isinstance(packages, list) and packages and all(
        isinstance(name, str) and NAME.fullmatch(name) for name in packages),
        "packages must be a nonempty list of OpenWrt package names")
    require(len(set(packages)) == len(packages), "duplicate requested package")
    require(isinstance(profile.get("all_kmods", True), bool), "all_kmods must be boolean")
    languages = profile.get("luci_languages", [])
    require(isinstance(languages, list) and all(isinstance(language, str) and
            re.fullmatch(r"[a-z]{2,3}(?:_[A-Za-z]{2,8})?", language) for language in languages)
            and len(set(languages)) == len(languages), "luci_languages must be a unique list of LuCI language codes")
    require(isinstance(lock, dict), "lock must be an object")
    feed_pins(profile, lock)
    overrides = package_overrides(lock)
    for name, override in overrides.items():
        require(isinstance(name, str) and NAME.fullmatch(name), "invalid package override name")
        require(isinstance(override, dict), f"override {name} must be an object")
        require(isinstance(override.get("version"), str)
                and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", override["version"]),
                f"override {name} needs a numeric release version")
        require(isinstance(override.get("sha256"), str) and SHA256.fullmatch(override["sha256"]),
                f"override {name} needs source SHA256")
        if name == "tailscale":
            require(override.get("artifact") == "static", "Tailscale override requires artifact: static")
            require(firmware["architecture"].startswith("aarch64_"),
                    "official Tailscale static ARM64 archive requires an ARM64 firmware profile")
            require(override.get("url") == f"https://pkgs.tailscale.com/stable/tailscale_{override['version']}_arm64.tgz",
                    "Tailscale static URL must name the matching official ARM64 release archive")
    return profile


def package_overrides(lock):
    overrides = lock.get("packages", {})
    require(isinstance(overrides, dict), "lock packages must be an object")
    overrides = dict(overrides)
    if "tailscale" in lock:
        require("tailscale" not in overrides, "duplicate Tailscale override")
        overrides["tailscale"] = lock["tailscale"]
    return overrides


def configure_feeds(profile, lock, sdk):
    validate(profile, lock)
    pins = feed_pins(profile, lock)
    (sdk / "feeds.conf").write_text("".join(
        f"src-git {feed['name']} {feed['url']}^{pins[feed['name']]}\n" for feed in profile["feeds"]))


def verify_feeds(profile, lock, sdk):
    for name, expected in feed_pins(profile, lock).items():
        directory = sdk / "feeds" / name
        actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
        require(actual == expected, f"feed {name} checkout {actual} differs from locked {expected}")


def static_tailscale_recipe(override):
    return """include $(TOPDIR)/rules.mk

PKG_NAME:=tailscale
PKG_VERSION:=""" + override["version"] + """
PKG_RELEASE:=1
PKG_SOURCE:=tailscale_$(PKG_VERSION)_arm64.tgz
PKG_SOURCE_URL:=https://pkgs.tailscale.com/stable
PKG_HASH:=""" + override["sha256"] + """
PKG_BUILD_DIR:=$(BUILD_DIR)/tailscale_$(PKG_VERSION)_arm64
PKG_LICENSE:=BSD-3-Clause

include $(INCLUDE_DIR)/package.mk

define Package/tailscale
  SECTION:=net
  CATEGORY:=Network
  SUBMENU:=VPN
  TITLE:=Tailscale official static ARM64 release
  URL:=https://tailscale.com
  DEPENDS:=@aarch64 +ca-bundle +kmod-tun
  PROVIDES:=tailscaled
endef

define Package/tailscale/conffiles
/etc/config/tailscale
/etc/tailscale/
endef

define Build/Configure
endef
define Build/Compile
endef

define Package/tailscale/install
\t$(INSTALL_DIR) $(1)/usr/sbin $(1)/etc/init.d $(1)/etc/config
\t$(INSTALL_BIN) $(PKG_BUILD_DIR)/tailscale $(1)/usr/sbin/tailscale
\t$(INSTALL_BIN) $(PKG_BUILD_DIR)/tailscaled $(1)/usr/sbin/tailscaled
\t$(INSTALL_BIN) ./files/tailscale.init $(1)/etc/init.d/tailscale
\t$(INSTALL_DATA) ./files/tailscale.conf $(1)/etc/config/tailscale
endef

$(eval $(call BuildPackage,tailscale))
"""


def apply_overrides(lock, sdk, profile):
    """Copy a pinned recipe, never rewrite the feed or global Go compiler."""
    for name, override in package_overrides(lock).items():
        # Full source trees have feeds/base -> package and package/feeds aliases.
        # Count actual files once, while still rejecting two distinct recipes.
        recipes = sorted({path.resolve() for path in (sdk / "feeds").glob(f"*/**/{name}/Makefile")})
        require(len(recipes) == 1, f"override {name} needs one unambiguous feed recipe")
        source = recipes[0]
        require(source.is_relative_to(sdk.resolve()), f"override {name} feed recipe escapes the build tree")
        text = source.read_text()
        target = sdk / "package/ax6000-overrides" / name
        require(not target.exists(), f"override directory already exists: {target}")
        if name == "tailscale":
            require(override.get("artifact") == "static" and profile["firmware"]["architecture"].startswith("aarch64_"),
                    "Tailscale static override requires the ARM64 profile")
            require((source.parent / "files/tailscale.init").is_file() and
                    (source.parent / "files/tailscale.conf").is_file(),
                    "pinned Tailscale service and config wrappers are required")
            target.mkdir(parents=True)
            shutil.copytree(source.parent / "files", target / "files", symlinks=True)
            (target / "Makefile").write_text(static_tailscale_recipe(override))
            for link in (sdk / "package/feeds").glob("*/tailscale"):
                require(link.is_symlink(), f"refusing to replace non-symlink feed package {link}")
                link.unlink()
            continue
        require(re.search(r"^PKG_VERSION\s*:?=", text, re.M) and
                re.search(r"^PKG_HASH\s*:?=\s*[0-9a-fA-F]{64}\s*$", text, re.M),
                f"override {name} needs a versioned archive recipe with PKG_HASH")
        require("$(PKG_VERSION)" in text and not re.search(r"^PKG_SOURCE_PROTO\s*:?=", text, re.M),
                f"override {name} source recipe cannot be safely version-bumped")
        shutil.copytree(source.parent, target, symlinks=True)
        for variable, value in (("PKG_VERSION", override["version"]), ("PKG_HASH", override["sha256"]),
                                ("PKG_RELEASE", "1")):
            text, count = re.subn(rf"^{variable}\s*:?=.*$", f"{variable}:={value}", text, flags=re.M)
            require(count == 1, f"override {name} requires exactly one {variable}")
        # Resolve relative include paths from the copied recipe against its pinned feed.
        def relocate(match):
            path = match.group(2)
            original = (source.parent / path).resolve()
            require(original.is_relative_to(sdk.resolve()), "recipe include escapes SDK")
            return match.group(1) + "$(TOPDIR)/" + str(original.relative_to(sdk.resolve()))
        text = re.sub(r"^(\s*include\s+)(\.{1,2}/[^\s]+)\s*$", relocate, text, flags=re.M)
        (target / "Makefile").write_text(text)
        # The SDK feed installer uses package/feeds links; the local overlay wins explicitly.
        for link in (sdk / "package/feeds").glob(f"*/{name}"):
            require(link.is_symlink(), f"refusing to replace non-symlink feed package {link}")
            link.unlink()


def check_kernel(profile, sdk):
    """A userspace SDK alone is insufficient to build matching kernel modules."""
    if not (profile.get("all_kmods", True) or any(name.startswith("kmod-") for name in profile["packages"])):
        return
    match = KERNEL_PACKAGE.fullmatch(profile["firmware"]["kernel_version"])
    require(match is not None, "exact kernel package version including ABI hash is required")
    expected_version, expected_vermagic, _ = match.groups()
    trees = [path.parent for path in (sdk / "build_dir").glob("target-*/linux-*/linux-*/Makefile")]
    prepared = [path for path in trees if (path / ".config").is_file()
                and (path / "include/generated/autoconf.h").is_file()
                and (path / "include/config/kernel.release").is_file()]
    require(len(prepared) == 1,
            "kernel module builds require one exact prepared kernel source tree in the SDK; "
            "generic userspace SDKs or headers alone cannot reproduce installed kmods")
    tree = prepared[0]
    if (tree / ".vermagic").is_file():
        vermagic = (tree / ".vermagic").read_text().strip()
    else:
        # OpenWrt SDKs omit .vermagic but bake the original value into kernel.mk.
        kernel_mk = (sdk / "include/kernel.mk").read_text()
        baked = re.findall(r"^\s*LINUX_VERMAGIC\s*:?=.*[, ]([0-9a-f]{32})\)?\s*$", kernel_mk, re.M)
        require(len(baked) == 1, "SDK has no unambiguous baked kernel ABI hash")
        vermagic = baked[0]
    require((tree / "include/config/kernel.release").read_text().strip() == expected_version
            and vermagic == expected_vermagic,
            "SDK prepared kernel ABI differs from the running firmware; never force vermagic")


def extract_sdk(archive, destination):
    require(not destination.exists(), "SDK extraction destination already exists; use a fresh work directory")
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        require(bool(members), "SDK archive is empty")
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and bool(path.parts),
                    f"unsafe SDK archive path: {member.name}")
            roots.add(path.parts[0])
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    f"unsupported SDK archive member: {member.name}")
            if member.issym() or member.islnk():
                require(not member.linkname.startswith("/"), f"absolute SDK archive link: {member.name}")
                link = (posixpath.join(str(path.parent), member.linkname)
                        if member.issym() else member.linkname)
                normalized = PurePosixPath(posixpath.normpath(link))
                require(normalized.parts and normalized.parts[0] == path.parts[0]
                        and ".." not in normalized.parts, f"SDK archive link escapes its root: {member.name}")
        require(len(roots) == 1, "SDK archive needs exactly one top-level directory")
        require(hasattr(tarfile, "data_filter"), "Python with tarfile data_filter is required (3.12 recommended)")
        destination.mkdir(parents=True)
        tar.extractall(destination, members=members, filter="data")
    sdk = destination / next(iter(roots))
    require((sdk / "Makefile").is_file(), "archive does not contain an SDK Makefile")
    return sdk


def available_packages(sdk):
    info, symbols = sdk / "tmp/.packageinfo", sdk / "tmp/.config-package.in"
    require(info.is_file() and symbols.is_file(), "run SDK make defconfig to generate package metadata first")
    packages = set(re.findall(r"^Package:\s*(\S+)", info.read_text(), re.M))
    config = set(re.findall(r"^\s*(?:menu)?config\s+PACKAGE_(\S+)", symbols.read_text(), re.M))
    return packages & config


def luci_language_symbols(profile, sdk, value):
    languages = profile.get("luci_languages", [])
    if not languages:
        return {}
    metadata = (sdk / "tmp/.config-package.in").read_text()
    available = set(re.findall(r"^\s*(?:menu)?config\s+LUCI_LANG_(\S+)", metadata, re.M))
    require(set(languages) <= available, "requested LuCI languages absent from pinned feeds: "
            + ", ".join(sorted(set(languages) - available)))
    return {"LUCI_LANG_" + language: value for language in languages}


def check_sdk_identity(profile, sdk):
    config = (sdk / ".config").read_text()
    expected = {"TARGET_BOARD": profile["firmware"]["target"],
                "TARGET_SUBTARGET": profile["firmware"]["subtarget"],
                "TARGET_ARCH_PACKAGES": profile["firmware"]["architecture"]}
    for symbol, value in expected.items():
        require(f'CONFIG_{symbol}="{value}"' in config, f"SDK {symbol} does not match exact firmware profile")
    version = (sdk / "include/version.mk").read_text()
    # getver.sh uses r<upstream>+<local commits>-<hash> for fork/detached builds.
    revision = re.findall(r"^REVISION\s*:?=\s*r[0-9]+(?:\+[0-9]+)?-([0-9a-f]{7,40})\s*$", version, re.M)
    require(len(revision) == 1 and profile["firmware"]["commit"].startswith(revision[0]),
            "SDK baked source revision does not match the firmware commit")


def baseline_packages(profile, sdk):
    directory = sdk / "ax6000-baseline"
    if not directory.exists():
        return {}
    from publish import read_control, validate_package
    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest.get("schema_version") == 1 and manifest.get("source_commit") == profile["firmware"]["commit"]
            and manifest.get("kernel_version") == profile["firmware"]["kernel_version"]
            and manifest.get("architecture") == profile["firmware"]["architecture"],
            "SDK module baseline differs from the exact firmware identity")
    frozen = manifest.get("frozen_kmods", manifest.get("core_kmods", []))
    require(isinstance(frozen, list) and all(isinstance(name, str) and name.startswith("kmod-") for name in frozen)
            and len(frozen) == len(set(frozen)), "invalid frozen module baseline list")
    frozen_packages = manifest.get("frozen_packages", ["kernel"] + frozen)
    require(isinstance(frozen_packages, list) and all(isinstance(name, str) and NAME.fullmatch(name)
            for name in frozen_packages) and len(frozen_packages) == len(set(frozen_packages))
            and set(frozen) <= set(frozen_packages), "invalid frozen package baseline list")
    result = {}
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe baseline package path")
        path = directory / relative
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()),
                "baseline package is missing or escapes SDK")
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        require(checksum == entry["sha256"], f"baseline checksum mismatch: {path.name}")
        control = read_control(path)
        require(control["Package"] == entry["package"] and control["Version"] == entry["version"],
                f"baseline package identity mismatch: {path.name}")
        validate_package(control, profile)
        if control["Package"] == "kernel":
            require(control["Version"] == profile["firmware"]["kernel_version"], "baseline kernel identity version mismatch")
        require(control["Package"] not in result, "duplicate baseline package")
        require(control["Package"] in frozen_packages,
                "baseline contains a package outside the frozen allowlist")
        result[control["Package"]] = path
    require(set(frozen) <= result.keys(), "baseline manifest omits listed frozen modules")
    if "frozen_packages" in manifest:
        require(set(frozen_packages) == result.keys(), "baseline manifest omits listed frozen packages")
    return result


def select_packages(profile, sdk):
    available = available_packages(sdk)
    requested = set(profile["packages"])
    baseline = baseline_packages(profile, sdk)
    missing = sorted(requested - available - baseline.keys())
    require(not missing, "requested packages absent from pinned SDK feeds: " + ", ".join(missing))
    kmods = sorted(name for name in available if name.startswith("kmod-")) if profile.get("all_kmods", True) else []
    # Frozen dependency packages remain available as fallback, but requested
    # userland packages must still refresh. Modules keep the exact prepared ABI.
    frozen_kmods = {name for name in baseline if name.startswith("kmod-")}
    selected = sorted((requested | set(kmods)) - frozen_kmods - {"kernel"})
    config = sdk / ".config"
    text = config.read_text()
    language_symbols = luci_language_symbols(profile, sdk, "m")
    text = re.sub(r"^(?:# )?CONFIG_(LUCI_LANG_[^= ]+)(?:=.*| is not set)\n?",
                  lambda match: "" if match.group(1) in language_symbols else match.group(0), text, flags=re.M)
    # Preserve target/toolchain config, clear preselected package defaults and firmware build output.
    text = re.sub(r"^(?:CONFIG_PACKAGE_.*|# CONFIG_PACKAGE_.*)\n?", "", text, flags=re.M)
    text = re.sub(r"^(?:CONFIG_(?:SDK|ALL|ALL_KMODS|ALL_NONSHARED|SIGNED_PACKAGES|BUILD_LOG|BUILDBOT|TARGET_ROOTFS_[A-Z0-9_]+)=.*|# CONFIG_(?:SDK|ALL|ALL_KMODS|ALL_NONSHARED|SIGNED_PACKAGES|BUILD_LOG|BUILDBOT|TARGET_ROOTFS_[A-Z0-9_]+) is not set)\n?", "", text, flags=re.M)
    config.write_text(text.rstrip() + "\nCONFIG_SDK=y\n# CONFIG_ALL is not set\n"
                      "# CONFIG_ALL_KMODS is not set\n# CONFIG_ALL_NONSHARED is not set\n"
                      "# CONFIG_SIGNED_PACKAGES is not set\n"
                      + "".join(f"CONFIG_{symbol}={value}\n" for symbol, value in sorted(language_symbols.items()))
                      + "".join(f"# CONFIG_PACKAGE_{name} is not set\n" for name in sorted(available - set(selected)))
                      + "".join(f"CONFIG_PACKAGE_{name}=m\n" for name in selected))
    report = {"profile": profile["id"], "requested": sorted(requested), "candidate_kmods": kmods,
              "baseline_kmods": sorted(name for name in baseline if name.startswith("kmod-")),
              "luci_languages": {name: "m" for name in profile.get("luci_languages", [])}}
    (sdk / "build-selection.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def verify_selection(profile, sdk):
    report = json.loads((sdk / "build-selection.json").read_text())
    config = (sdk / ".config").read_text()
    selected = set(re.findall(r"^CONFIG_PACKAGE_(.+)=[my]$", config, re.M))
    baseline = baseline_packages(profile, sdk)
    missing = sorted(set(profile["packages"]) - selected - baseline.keys())
    require(not missing, "requested packages unresolved after defconfig: " + ", ".join(missing))
    for language in profile.get("luci_languages", []):
        expected = report.get("luci_languages", {}).get(language)
        require(expected in {"m", "y"} and f"CONFIG_LUCI_LANG_{language}={expected}\n" in config,
                f"requested LuCI language {language} unresolved after defconfig")
    report["selected_packages"] = sorted(selected)
    report["selected_kmods"] = sorted(set(report["candidate_kmods"]) & selected)
    report["incompatible_kmods"] = sorted(set(report["candidate_kmods"]) - selected)
    report["firmware"] = profile["firmware"]
    (sdk / "build-selection.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def export_packages(profile, lock, sdk, output, sdk_archive=None):
    """Merge frozen modules with freshly built packages by opkg package name."""
    from publish import read_control, validate_package
    require(not output.exists(), "SDK output directory already exists")
    baseline = baseline_packages(profile, sdk)
    built = list((sdk / "bin").rglob("*.ipk"))
    require(bool(built), "SDK produced no .ipk packages")
    packages = dict(baseline)
    for path in sorted(built):
        control = read_control(path)
        validate_package(control, profile)
        packages[control["Package"]] = path
    require(set(profile["packages"]) <= packages.keys(),
            "SDK produced no output for requested packages: " + ", ".join(sorted(set(profile["packages"]) - packages.keys())))
    signer = sdk / "staging_dir/host/bin/usign"
    require(signer.is_file() and os.access(signer, os.X_OK), "SDK host usign is unavailable for repository signing")
    package_dir = output / "bin/packages"
    package_dir.mkdir(parents=True)
    for path in packages.values():
        shutil.copy2(path, package_dir / path.name)
    shutil.copy2(signer, output / "usign")
    shutil.copy2(sdk / "build-selection.json", output / "build-selection.json")
    firmware_assets = sdk / "ax6000-baseline/firmware"
    if firmware_assets.is_dir():
        require(sdk_archive is not None and sdk_archive.is_file(),
                "SDK with frozen firmware requires its verified --sdk-archive for complete daily publication")
        with sdk_archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(digest == profile["sdk"]["sha256"], "downloaded SDK hash changed before daily export")
        target = output / "bin/targets" / profile["firmware"]["target"] / profile["firmware"]["subtarget"]
        shutil.copytree(firmware_assets, target, symlinks=True)
        sdk_name = PurePosixPath(urlsplit(profile["sdk"]["url"]).path).name
        require(bool(sdk_name) and NAME.fullmatch(sdk_name), "invalid published SDK archive filename")
        shutil.copy2(sdk_archive, target / sdk_name)
        checksums = []
        for asset in sorted(target.iterdir()):
            if asset.is_file() and asset.name not in {"sha256sums", "SHA256SUMS"}:
                with asset.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                checksums.append(f"{digest}  {asset.name}\n")
        for filename in ("sha256sums", "SHA256SUMS"):
            (target / filename).write_text("".join(checksums))
    (output / "profile.json").write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    generated_lock = dict(lock)
    generated_lock["profile_sha256"] = hashlib.sha256((output / "profile.json").read_bytes()).hexdigest()
    (output / "lock.json").write_text(json.dumps(generated_lock, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--sdk-dir", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sdk-archive", type=Path)
    parser.add_argument("--phase", choices=("validate", "feeds", "check-feeds", "overrides", "check-sdk", "check-kernel", "select", "verify", "export"), default="feeds")
    parser.add_argument("--inspect-sdk", choices=("url", "sha256", "id"))
    parser.add_argument("--extract", type=Path)
    parser.add_argument("--extract-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.extract:
            require(args.extract_dir is not None, "--extract-dir is required")
            print(extract_sdk(args.extract, args.extract_dir))
            return
        require(args.profile is not None and args.lock is not None, "--profile and --lock are required")
        profile, lock = json.loads(args.profile.read_text()), json.loads(args.lock.read_text())
        validate(profile, lock)
        if args.inspect_sdk:
            print(profile["id"] if args.inspect_sdk == "id" else profile["sdk"][args.inspect_sdk])
            return
        if args.phase == "validate":
            print(f"Validated enabled profile {profile['id']} and locked SDK inputs")
            return
        require(args.sdk_dir is not None and args.sdk_dir.is_dir(), "--sdk-dir must be an extracted SDK")
        if args.phase == "export":
            require(args.output_dir is not None, "--output-dir is required")
            export_packages(profile, lock, args.sdk_dir, args.output_dir, args.sdk_archive)
            return
        actions = {"feeds": lambda: configure_feeds(profile, lock, args.sdk_dir),
                   "check-feeds": lambda: verify_feeds(profile, lock, args.sdk_dir),
                   "overrides": lambda: apply_overrides(lock, args.sdk_dir, profile),
                   "check-kernel": lambda: check_kernel(profile, args.sdk_dir),
                   "check-sdk": lambda: check_sdk_identity(profile, args.sdk_dir),
                   "select": lambda: select_packages(profile, args.sdk_dir),
                   "verify": lambda: verify_selection(profile, args.sdk_dir)}
        result = actions[args.phase]()
        if result is not None:
            print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"prepare: {error}\n")


if __name__ == "__main__":
    main()
