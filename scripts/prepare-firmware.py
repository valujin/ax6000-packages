#!/usr/bin/env python3
"""Prepare a pinned AX6000 stock-layout firmware build and export its actual ABI."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import prepare
from publish import read_control


def validate(profile, lock):
    prepare.require(isinstance(profile, dict) and profile.get("enabled") is True,
                    "source firmware profile must be enabled")
    source = profile.get("build_source")
    prepare.require(isinstance(source, dict), "build_source is required for a firmware build")
    defconfig = source.get("defconfig")
    prepare.require(isinstance(defconfig, str) and not PurePosixPath(defconfig).is_absolute()
                    and ".." not in PurePosixPath(defconfig).parts and bool(defconfig),
                    "build_source.defconfig must be a relative path inside pinned source")
    prepare.require(source.get("device") == "xiaomi_redmi-router-ax6000-stock",
                    "firmware build supports only Redmi AX6000 stock flash layout")
    firmware = profile.get("firmware", {})
    prepare.require((firmware.get("target"), firmware.get("subtarget"), firmware.get("architecture")) ==
                    ("mediatek", "filogic", "aarch64_cortex-a53"), "source firmware target must be mediatek/filogic AX6000")
    prepare.require(firmware.get("kernel_version") is None or prepare.KERNEL_PACKAGE.fullmatch(firmware.get("kernel_version", "")),
                    "new firmware kernel_version must be null until built, or its exact built ABI")
    # Reuse the strict pin and package validation without claiming an existing SDK/ABI.
    surrogate = copy.deepcopy(profile)
    surrogate["firmware"]["kernel_version"] = "0.0.0~00000000000000000000000000000000-r0"
    surrogate["sdk"] = {"url": "https://invalid.example/source-build-only.tar.xz", "sha256": "0" * 64}
    prepare.validate(surrogate, lock)
    prepare.require(not any(feed["name"] == "base" for feed in profile["feeds"]),
                    "full source builds supply base packages directly; omit the base feed")
    image = profile.get("image_packages", [])
    prepare.require(isinstance(image, list) and all(name in profile["packages"] for name in image),
                    "image_packages must be a subset of requested packages")
    ssl_symbols(profile)
    return profile


def configure(profile, lock, source):
    validate(profile, lock)
    repository_files(source)
    pins = prepare.feed_pins(profile, lock)
    (source / "feeds.conf").write_text("".join(
        f"src-git {feed['name']} {feed['url']}^{pins[feed['name']]}\n" for feed in profile["feeds"]))
    baseline = source / profile["build_source"]["defconfig"]
    prepare.require(baseline.is_file(), "pinned source does not contain the requested vendor defconfig")
    config = baseline.read_text()
    config = re.sub(r"^(?:# )?CONFIG_TARGET_(?:mediatek(?:_[A-Za-z0-9_-]+)?|DEVICE_[^= ]+|DEVICE_PACKAGES_[^= ]+|MULTI_PROFILE|PER_DEVICE_ROOTFS|ALL_PROFILES)(?:=.*| is not set)\n?", "", config, flags=re.M)
    config = re.sub(r"^(?:# )?CONFIG_(?:SDK|DEVEL|ALL_KMODS|ALL|ALL_NONSHARED|SIGNED_PACKAGES|SIGNATURE_CHECK)(?:=.*| is not set)\n?", "", config, flags=re.M)
    config += ("\nCONFIG_TARGET_mediatek=y\nCONFIG_TARGET_mediatek_filogic=y\n"
               "CONFIG_TARGET_MULTI_PROFILE=y\nCONFIG_TARGET_PER_DEVICE_ROOTFS=y\n"
               "# CONFIG_TARGET_ALL_PROFILES is not set\n"
               "CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock=y\n"
               'CONFIG_TARGET_DEVICE_PACKAGES_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock="-kmod-mt7915e -kmod-mt7986-firmware"\n'
               "CONFIG_ALL_KMODS=y\nCONFIG_DEVEL=y\nCONFIG_SDK=y\n"
               "# CONFIG_ALL is not set\n# CONFIG_ALL_NONSHARED is not set\n"
               "CONFIG_SIGNED_PACKAGES=y\nCONFIG_SIGNATURE_CHECK=y\n")
    (source / ".config").write_text(set_symbols(config, ssl_symbols(profile)))


def repository_files(source, check_only=False):
    """Override moving official feeds; connect the verified feed with install-feed.sh."""
    public = (Path(__file__).resolve().parents[1] / "keys/repository.pub").read_bytes()
    lines = public.decode("ascii").splitlines()
    prepare.require(len(lines) == 2 and lines[0].startswith("untrusted comment:"), "invalid repository public key")
    data = base64.b64decode(lines[1], validate=True)
    prepare.require(len(data) == 42 and data[:2] == b"Ed", "invalid repository public key")
    fingerprint = data[2:10].hex()
    installer = (Path(__file__).resolve().parent / "install-feed.sh").read_bytes()
    prepare.require(b"expected_fingerprint=" + fingerprint.encode() + b"\n" in installer,
                    "bundled installer signing key differs from repository key")
    files = {
        "files/etc/opkg/distfeeds.conf":
            b"# Packages for this firmware require its exact kernel ABI.\n"
            b"# Connect the signed matching repository with ax6000-install-feed.\n"
            b"# Moving official snapshot feeds are deliberately not configured.\n",
        "files/etc/opkg/keys/" + fingerprint: public,
        "files/usr/sbin/ax6000-install-feed": installer,
    }
    for relative, content in files.items():
        target = source / relative
        prepare.require(target.resolve().is_relative_to(source.resolve()) and not target.is_symlink()
                        and not any(parent.is_symlink() for parent in target.parents
                                    if parent != source and parent.is_relative_to(source)),
                        f"repository overlay is outside source or a symlink: {relative}")
        prepare.require(not target.exists() or (target.is_file() and target.read_bytes() == content),
                        f"repository overlay differs from reviewed defaults: {relative}")
        mode = 0o755 if relative == "files/usr/sbin/ax6000-install-feed" else 0o644
        prepare.require(not target.exists() or target.stat().st_mode & 0o777 == mode,
                        f"repository overlay permissions differ: {relative}")
        prepare.require(not check_only or target.is_file(), f"repository overlay is missing: {relative}")
    if not check_only:
        for relative, content in files.items():
            target = source / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o755 if relative == "files/usr/sbin/ax6000-install-feed" else 0o644)


def set_symbols(config, values):
    config = re.sub(r"^(?:# )?CONFIG_([^= ]+)(?:=.*| is not set)\n?",
                    lambda match: "" if match.group(1) in values else match.group(0), config, flags=re.M)
    return config.rstrip() + "\n" + "".join(
        f"CONFIG_{symbol}={value}\n" if value != "n" else f"# CONFIG_{symbol} is not set\n"
        for symbol, value in sorted(values.items()))


def ssl_symbols(profile):
    """Install the collection's SSL provider; keep the alternative as an IPK."""
    image = set(profile.get("image_packages", []))
    collections = image & {"luci-ssl", "luci-ssl-openssl"}
    prepare.require(len(collections) <= 1, "image SSL collections luci-ssl and luci-ssl-openssl conflict")
    if not collections:
        return {}
    openssl = "luci-ssl-openssl" in collections
    return {"PACKAGE_libustream-openssl": "y" if openssl else "m",
            "PACKAGE_libustream-mbedtls": "m" if openssl else "y"}


def ssl_selection(profile, source):
    """Repair userspace provider selection without repeating kernel selection."""
    values = ssl_symbols(profile)
    if values:
        config = source / ".config"
        root = source.resolve()
        prepare.require(config.is_file() and not config.is_symlink()
                        and config.resolve().is_relative_to(root), "SSL configuration is missing or a symlink")
        updated = set_symbols(config.read_text(), values)
        inactive = next(symbol.removeprefix("PACKAGE_libustream-")
                        for symbol, value in values.items() if value == "m")
        staging = source / "staging_dir"
        prepare.require(not staging.is_symlink(), "SSL install stamp staging directory is a symlink")
        stale = []
        # A y recipe's image list reads every built variant. Demoting one variant
        # to m does not run prepare-package-install to clear its old y stamp.
        for info in sorted(staging.glob("target-*/pkginfo")):
            prepare.require(info.is_dir() and info.resolve().is_relative_to(root)
                            and not info.is_symlink()
                            and not any(parent.is_symlink() for parent in info.parents
                                        if parent != source and parent.is_relative_to(source)),
                            f"SSL install stamp directory is outside source or a symlink: {info}")
            stamp = info / f"ustream-ssl.{inactive}.install"
            for path in (stamp, stamp.with_name(stamp.name + ".flags")):
                if path.exists() or path.is_symlink():
                    prepare.require(path.is_file() and not path.is_symlink()
                                    and path.resolve().is_relative_to(root),
                                    f"SSL install stamp is not a regular source file: {path}")
                    if path == stamp:
                        prepare.require(path.read_bytes() == f"libustream-{inactive}\n".encode(),
                                        f"SSL install stamp has unexpected packages: {path}")
                    stale.append(path)
        # Complete preflight before changing either the config or cached metadata.
        for path in stale:
            path.unlink()
        config.write_text(updated)


def verify_ssl(profile, config):
    values = ssl_symbols(profile)
    providers = set(re.findall(r"^CONFIG_PACKAGE_libustream-(openssl|mbedtls|wolfssl)=y$", config, re.M))
    prepare.require(len(providers) <= 1, "multiple image SSL providers install /lib/libustream-ssl.so")
    for symbol, value in values.items():
        prepare.require(f"CONFIG_{symbol}={value}\n" in config,
                        f"SSL provider selection requires {symbol}={value}")


def apply_source_patch(source, spec, patch, check_only=False):
    """Apply only the reviewed source revision; an exact patched file is idempotent."""
    relative = Path(spec["path"])
    target = source / relative
    prepare.require(not relative.is_absolute() and ".." not in relative.parts
                    and target.resolve().is_relative_to(source.resolve())
                    and not target.is_symlink()
                    and not any(parent.is_symlink() for parent in target.parents
                                if parent != source and parent.is_relative_to(source)),
                    f"vendor patch target is absent or outside source: {spec['path']}")
    operation = spec.get("operation", "modify")
    prepare.require(operation in {"modify", "add"}, "vendor patch operation is unsupported")
    if operation == "add":
        prepare.require(spec["before_sha256"] is None, "new vendor patch requires a null before hash")
    else:
        prepare.require(target.is_file(), f"vendor patch target is absent or outside source: {spec['path']}")
    prepare.require(hashlib.sha256(patch.read_bytes()).hexdigest() == spec["sha256"],
                    f"vendor patch hash differs: {spec['patch']}")
    if target.exists():
        prepare.require(target.is_file(), f"vendor patch source hash differs: {spec['path']}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest == spec["after_sha256"]:
            return
        prepare.require(operation == "modify" and digest == spec["before_sha256"],
                        f"vendor patch source hash differs: {spec['path']}")
    prepare.require(not check_only, f"required vendor patch is not applied: {spec['patch']}")
    command = ["git", "apply", "--whitespace=nowarn"]
    subprocess.run(command + ["--check", str(patch.resolve())], cwd=source, check=True)
    subprocess.run(command + [str(patch.resolve())], cwd=source, check=True)
    prepare.require(hashlib.sha256(target.read_bytes()).hexdigest() == spec["after_sha256"],
                    f"vendor patch produced an unexpected source hash: {spec['path']}")


def vendor_fixes(profile, source, check_only=False):
    directory = Path(__file__).resolve().parents[1] / "patches/firmware"
    manifest = json.loads((directory / "manifest.json").read_text())
    target = source / "target/linux/mediatek/Makefile"
    series = re.findall(r"^KERNEL_PATCHVER\s*:?=\s*(\S+)\s*$", target.read_text(), re.M)
    prepare.require(series == [manifest["kernel_series"]], "vendor patches require the reviewed kernel series 6.6")
    prepare.require(profile["firmware"]["commit"] == manifest["source_commit"],
                    "vendor patches require the reviewed firmware source commit")
    for spec in manifest["patches"]:
        if "feed_name" in spec:
            pins = [feed.get("commit") for feed in profile.get("feeds", [])
                    if feed.get("name") == spec["feed_name"]]
            prepare.require(pins == [spec["feed_commit"]],
                            f"vendor patch feed {spec['feed_name']} pin differs from the reviewed recipe")
    for spec in manifest["patches"]:
        apply_source_patch(source, spec, directory / spec["patch"], check_only=check_only)
    record = source / "firmware-source-patches.json"
    if check_only:
        prepare.require(record.is_file() and json.loads(record.read_text()) == manifest,
                        "vendor patch provenance is absent or differs from the reviewed patches")
    else:
        record.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        selection = source / "build-selection.json"
        if selection.is_file():
            report = json.loads(selection.read_text())
            report["source_patches"] = manifest["patches"]
            selection.write_text(json.dumps(report, indent=2) + "\n")
    return manifest["patches"]


def overrides(profile, lock, source):
    vendor_fixes(profile, source)
    prepare.apply_overrides(lock, source, profile)


def select(profile, source):
    available = prepare.available_packages(source)
    requested = set(profile["packages"])
    prepare.require(not requested - available,
                    "requested firmware packages absent from pinned feeds: " + ", ".join(sorted(requested - available)))
    config_path = source / ".config"
    config = config_path.read_text()
    # Disable every generated device except the explicitly requested stock profile.
    target_info = (source / "tmp/.config-target.in").read_text()
    devices = set(re.findall(r"^(?:menu)?config\s+(TARGET_DEVICE_\S+)", target_info, re.M))
    desired = "TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock"
    prepare.require(desired in devices, "pinned source has no AX6000 stock device profile")
    values = {device: ("y" if device == desired else "n") for device in devices}
    values.update({"SIGNED_PACKAGES": "y", "SIGNATURE_CHECK": "y"})
    values.update(prepare.luci_language_symbols(profile, source, "y"))
    baseline_y = set(re.findall(r"^CONFIG_PACKAGE_(.+)=y$", config, re.M))
    kmods = sorted(name for name in available if name.startswith("kmod-")) if profile.get("all_kmods", True) else []
    for name in sorted(requested | set(kmods)):
        values["PACKAGE_" + name] = "y" if name in baseline_y or name in profile.get("image_packages", []) else "m"
    values.update(ssl_symbols(profile))
    config_path.write_text(set_symbols(config, values))
    report = {"profile": profile["id"], "requested": sorted(requested), "candidate_kmods": kmods,
              "source_commit": profile["firmware"]["commit"], "device": profile["build_source"]["device"],
              "luci_languages": {name: "y" for name in profile.get("luci_languages", [])},
              "baseline_vendor_drivers": sorted(name for name in baseline_y if name in
                                                 {"kmod-mt_wifi", "kmod-conninfra", "kmod-warp", "kmod-mediatek_hnat"})}
    patches = source / "firmware-source-patches.json"
    if patches.is_file():
        report["source_patches"] = json.loads(patches.read_text())["patches"]
    (source / "build-selection.json").write_text(json.dumps(report, indent=2) + "\n")


def verify(profile, source):
    repository_files(source, check_only=True)
    patches = vendor_fixes(profile, source, check_only=True)
    report = prepare.verify_selection(profile, source)
    prepare.require(report.get("source_patches") == patches, "selection report omits vendor patch provenance")
    config = (source / ".config").read_text()
    verify_ssl(profile, config)
    enabled = set(re.findall(r"^CONFIG_TARGET_DEVICE_(.+)=y$", config, re.M))
    prepare.require(enabled == {"mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock"},
                    "firmware selection includes another device or omits AX6000 stock")
    for symbol in ("TARGET_mediatek", "TARGET_mediatek_filogic", "SDK", "DEVEL", "ALL_KMODS", "SIGNED_PACKAGES", "SIGNATURE_CHECK"):
        prepare.require(f"CONFIG_{symbol}=y\n" in config, f"firmware configuration dropped {symbol}")
    prepare.require('CONFIG_TARGET_ARCH_PACKAGES="aarch64_cortex-a53"' in config,
                    "firmware architecture does not match AX6000 profile")
    for name in report["baseline_vendor_drivers"]:
        prepare.require(f"CONFIG_PACKAGE_{name}=y\n" in config, f"vendor firmware driver {name} was removed from image")
    for name in profile.get("image_packages", []):
        prepare.require(f"CONFIG_PACKAGE_{name}=y\n" in config, f"required image package {name} dropped from firmware image")
    return report


def core_kmod_names(source):
    result = set()
    recipe = ""
    for line in (source / "tmp/.packageinfo").read_text().splitlines():
        if line.startswith("Source-Makefile:"):
            recipe = line.split(":", 1)[1].strip()
        elif line.startswith("Package:") and recipe.startswith("package/") and not recipe.startswith("package/feeds/"):
            name = line.split(":", 1)[1].strip()
            if name.startswith("kmod-"):
                result.add(name)
    return result


def dependency_groups(value, label):
    """Read opkg comma groups and alternatives, keeping version syntax intact."""
    if not value.strip():
        return []
    result = []
    for group in value.split(","):
        names = set()
        for alternative in group.split("|"):
            match = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9_.+\-]*)"
                                 r"(?:\s*\((?:>=|<=|>>|<<|!=|=|>|<)\s*[^\s()]+\))?\s*", alternative)
            prepare.require(match is not None, f"invalid {label}: {alternative!r}")
            names.add(match[1])
        result.append(names)
    return result


def runtime_dependency_closure(controls, roots):
    """Preserve built providers conservatively; final IPK controls are authoritative."""
    packages = {}
    providers = {}
    for _, control in controls:
        name = control["Package"]
        prepare.require(name not in packages, f"duplicate built package in SDK dependency baseline: {name}")
        packages[name] = control
        provided = {name}
        for group in dependency_groups(control.get("Provides", ""), f"Provides for {name}"):
            provided.update(group)
        for virtual in provided:
            providers.setdefault(virtual, set()).add(name)
    closure, pending = set(), set(roots)
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        prepare.require(name in packages, f"missing built baseline package: {name}")
        closure.add(name)
        for group in dependency_groups(packages[name].get("Depends", ""), f"Depends for {name}"):
            candidates = set().union(*(providers.get(dependency, set()) for dependency in group))
            prepare.require(bool(candidates), f"built module baseline dependency unavailable: {name} -> "
                            + " | ".join(sorted(group)))
            pending.update(candidates - closure)
    return closure


def augment_sdk(profile, source, archive, controls, abi):
    """Keep the full successful package inventory; refresh requested external packages."""
    match = prepare.KERNEL_PACKAGE.fullmatch(abi)
    version, vermagic, _ = match.groups()
    trees = [path.parent for path in (source / "build_dir").glob("target-*/linux-*/linux-*/.vermagic")
             if path.read_text().strip() == vermagic]
    prepare.require(len(trees) == 1, "cannot identify built prepared kernel context for SDK")
    kernel = trees[0]
    prepare.require((kernel / "include/config/kernel.release").read_text().strip() == version,
                    "built kernel source release differs from its kernel ipk")
    core = core_kmod_names(source)
    prepare.require("kmod-amneziawg" not in core, "external AmneziaWG must be rebuilt rather than frozen as a core module")
    built_modules = {control["Package"] for _, control in controls
                     if control.get("Package", "").startswith("kmod-")}
    refreshed = set(profile["packages"]) - core
    frozen = built_modules - refreshed
    protected = {"kernel", "libc", "base-files", "busybox", "opkg"}
    built_names = {control["Package"] for _, control in controls}
    retained = (built_names - refreshed - protected) | {"kernel"}
    # Keep every offered full-build package, including vendor GUI/tools and
    # auto-selected translations. Protected identities are retained only when
    # required by this closure; the publisher never offers them as upgrades.
    frozen_packages = runtime_dependency_closure(controls, retained) - refreshed
    baseline = [(path, control) for path, control in controls
                if control.get("Package") in frozen_packages]
    prepare.require(bool(core), "source metadata did not identify any core kernel modules for SDK baseline")
    with tempfile.TemporaryDirectory(prefix="ax6000-sdk-", dir=source.parent) as temporary:
        temporary = Path(temporary)
        compressed = archive.name.endswith(".zst")
        unpack_archive = archive
        if compressed:
            unpack_archive = temporary / "sdk.tar"
            with unpack_archive.open("wb") as stream:
                subprocess.run(["zstd", "-d", "-c", "--", str(archive)], stdout=stream, check=True)
        sdk = prepare.extract_sdk(unpack_archive, temporary / "extracted")
        sdk_kernel = sdk / kernel.relative_to(source)
        for required in (".config", "Makefile", "Module.symvers", "include/generated/autoconf.h", "include/config/kernel.release"):
            prepare.require((sdk_kernel / required).is_file(), f"generated SDK lacks prepared kernel file {required}")
        for identity in (".config", "Module.symvers"):
            prepare.require((sdk_kernel / identity).read_bytes() == (kernel / identity).read_bytes(),
                            f"generated SDK kernel {identity} differs from the built source context")
        prepare.require((kernel / "modules.builtin.modinfo").is_file(),
                        "built prepared kernel lacks modules.builtin.modinfo required by the SDK kernel package")
        shutil.copy2(kernel / "modules.builtin.modinfo", sdk_kernel / "modules.builtin.modinfo")
        for filename in (".vermagic", ".config.set"):
            if (kernel / filename).is_file():
                shutil.copy2(kernel / filename, sdk_kernel / filename)
        snapshot = sdk / "ax6000-baseline"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        (snapshot / "ipks").mkdir(parents=True)
        files = []
        for path, control in baseline:
            target = snapshot / "ipks" / path.name
            prepare.require(not target.exists(), "duplicate frozen baseline package filename")
            shutil.copy2(path, target)
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"package": control["Package"], "version": control["Version"],
                          "path": "ipks/" + path.name, "sha256": digest})
        (snapshot / "manifest.json").write_text(json.dumps(
            {"schema_version": 1, "source_commit": profile["firmware"]["commit"],
             "kernel_version": abi, "architecture": profile["firmware"]["architecture"],
             "source_patches": json.loads((source / "firmware-source-patches.json").read_text())["patches"]
             if (source / "firmware-source-patches.json").is_file() else [],
             "frozen_kmods": sorted(frozen), "frozen_packages": sorted(frozen_packages),
             "refreshed_kmods": sorted(refreshed & built_modules), "files": files},
            indent=2, sort_keys=True) + "\n")
        # Carry immutable images and build metadata into daily SDK exports. The SDK
        # itself is added from the verified download later, avoiding archive recursion.
        firmware_assets = snapshot / "firmware"
        firmware_assets.mkdir()
        for asset in sorted(archive.parent.iterdir()):
            if asset.is_file() and asset != archive and not asset.name.endswith(".partial") \
                    and asset.name not in {"sha256sums", "SHA256SUMS"}:
                shutil.copy2(asset, firmware_assets / asset.name)
        # Validate the augmented context without changing its original configuration/hash.
        sdk_profile = copy.deepcopy(profile)
        sdk_profile["firmware"]["kernel_version"] = abi
        prepare.check_kernel(sdk_profile, sdk)
        partial = archive.with_name(archive.name + ".partial")
        if compressed:
            with partial.open("wb") as stream:
                process = subprocess.Popen(["zstd", "-T1", "-6", "-c"], stdin=subprocess.PIPE, stdout=stream)
                try:
                    with tarfile.open(fileobj=process.stdin, mode="w|") as tar:
                        tar.add(sdk, arcname=sdk.name)
                finally:
                    process.stdin.close()
                prepare.require(process.wait() == 0, "failed to compress augmented SDK")
        else:
            mode = "w:xz" if archive.name.endswith(".xz") else "w:gz" if archive.name.endswith(".gz") else "w"
            with tarfile.open(partial, mode) as tar:
                tar.add(sdk, arcname=sdk.name)
        partial.replace(archive)


def export(profile, lock, source, output):
    prepare.require(not output.exists(), "firmware output directory already exists")
    controls = [(path, read_control(path)) for path in sorted((source / "bin").rglob("*.ipk"))]
    kernel = [control for _, control in controls if control.get("Package") == "kernel"]
    prepare.require(len(kernel) == 1, "build must produce exactly one kernel ipk to discover actual firmware ABI")
    abi = kernel[0].get("Version", "")
    prepare.require(prepare.KERNEL_PACKAGE.fullmatch(abi), "built kernel ipk has invalid ABI version")
    prepare.require(kernel[0].get("Architecture") == profile["firmware"]["architecture"], "built kernel architecture mismatch")
    for path, control in controls:
        if control.get("Package", "").startswith("kmod-"):
            dependencies = re.findall(r"(?:^|,)\s*kernel\s*\(\s*=\s*([^\s)]+)\s*\)", control.get("Depends", ""))
            prepare.require(dependencies == [abi], f"built module {path.name} has another kernel ABI")
    built = {control.get("Package") for _, control in controls}
    prepare.require(set(profile["packages"]) <= built,
                    "build omitted requested packages: " + ", ".join(sorted(set(profile["packages"]) - built)))
    report = json.loads((source / "build-selection.json").read_text())
    missing_kmods = sorted(set(report["selected_kmods"]) - built)
    prepare.require(not missing_kmods,
                    "build omitted selected kernel modules: " + ", ".join(missing_kmods))
    target = source / "bin/targets/mediatek/filogic"
    images = sorted(target.glob("*xiaomi_redmi-router-ax6000-stock*sysupgrade*"))
    prepare.require(bool(images), "build produced no Redmi AX6000 stock sysupgrade image")
    sdks = sorted(target.glob("*sdk*.tar.*"))
    prepare.require(len(sdks) == 1, "build must produce one matching SDK archive")
    signer = source / "staging_dir/host/bin/usign"
    prepare.require(signer.is_file() and os_access_executable(signer), "built host usign is unavailable")
    generated = copy.deepcopy(profile)
    generated["firmware"]["kernel_version"] = abi
    augment_sdk(profile, source, sdks[0], controls, abi)
    # Daily SDK builds keep the kernel configuration fixed and reuse frozen modules.
    generated["all_kmods"] = False
    generated["feeds"].insert(0, {"name": "base", "url": profile["firmware"]["repository"],
                                  "commit": profile["firmware"]["commit"]})
    with sdks[0].open("rb") as stream:
        sdk_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    build_id = f"{profile['id']}-{profile['firmware']['commit'][:12]}-{sdk_hash[:12]}"
    generated["build_id"] = build_id
    site_url = profile["build_source"].get("publication_url", "https://valujin.github.io/ax6000-packages")
    url_base = site_url.rstrip("/") + "/firmware/" + build_id
    prepare.https_url(url_base, "firmware publication URL")
    generated["sdk"] = {"url": url_base.rstrip("/") + "/" + sdks[0].name, "sha256": sdk_hash}
    output.mkdir(parents=True)
    shutil.copytree(source / "bin", output / "bin", symlinks=True)
    exported_target = output / "bin/targets/mediatek/filogic"
    checksums = []
    for asset in sorted(exported_target.iterdir()):
        if asset.is_file() and asset.name not in {"sha256sums", "SHA256SUMS"}:
            with asset.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            checksums.append(f"{digest}  {asset.name}\n")
    for filename in ("sha256sums", "SHA256SUMS"):
        (exported_target / filename).write_text("".join(checksums))
    shutil.copy2(signer, output / "usign")
    report["firmware"] = generated["firmware"]
    report["built_packages"] = sorted(built)
    (output / "build-selection.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "profile.json").write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    generated_lock = copy.deepcopy(lock)
    generated_lock["firmware"] = generated["firmware"]
    generated_lock["sdk"] = generated["sdk"]
    generated_lock.setdefault("feeds", {})["base"] = profile["firmware"]["commit"]
    generated_lock["profile_sha256"] = hashlib.sha256((output / "profile.json").read_bytes()).hexdigest()
    (output / "lock.json").write_text(json.dumps(generated_lock, indent=2, sort_keys=True) + "\n")
    shutil.copy2(source / ".config", output / "build.config")
    manifest = []
    for path in sorted((output / "bin").rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest.append({"path": str(path.relative_to(output)), "sha256": digest, "size": path.stat().st_size})
    recipe_commit = os.environ.get("AX6000_RECIPE_COMMIT")
    if not recipe_commit:
        try:
            recipe_commit = subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parents[1]),
                                                     "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            recipe_commit = None
    prepare.require(recipe_commit is None or prepare.COMMIT.fullmatch(recipe_commit), "invalid build recipe commit provenance")
    (output / "firmware-manifest.json").write_text(json.dumps({"build_id": build_id, "kernel_version": abi,
                                                               "recipe_commit": recipe_commit,
                                                               "source_commit": profile["firmware"]["commit"],
                                                               "source_patches": report.get("source_patches", []),
                                                               "files": manifest}, indent=2) + "\n")
    print(f"Exported matching firmware, SDK and packages with kernel ABI {abi} to {output}")


def os_access_executable(path):
    return os.access(path, os.X_OK)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--phase", choices=("validate", "configure", "repository-files", "check-feeds", "vendor-fixes", "overrides", "select", "ssl-selection", "verify", "export"), default="validate")
    args = parser.parse_args()
    try:
        profile, lock = json.loads(args.profile.read_text()), json.loads(args.lock.read_text())
        validate(profile, lock)
        if args.phase == "validate":
            print(f"Validated new-firmware source profile {profile['id']}; ABI will be discovered after build")
            return
        prepare.require(args.source_dir is not None and args.source_dir.is_dir(), "--source-dir is required")
        if args.phase == "export":
            repository_files(args.source_dir, check_only=True)
            vendor_fixes(profile, args.source_dir, check_only=True)
            prepare.require(args.output_dir is not None, "--output-dir is required")
            export(profile, lock, args.source_dir, args.output_dir)
        else:
            actions = {"configure": lambda: configure(profile, lock, args.source_dir),
                       "repository-files": lambda: repository_files(args.source_dir),
                       "check-feeds": lambda: prepare.verify_feeds(profile, lock, args.source_dir),
                       "vendor-fixes": lambda: vendor_fixes(profile, args.source_dir),
                       "overrides": lambda: overrides(profile, lock, args.source_dir),
                       "select": lambda: select(profile, args.source_dir),
                       "ssl-selection": lambda: ssl_selection(profile, args.source_dir),
                       "verify": lambda: verify(profile, args.source_dir)}
            actions[args.phase]()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"prepare-firmware: {error}\n")


if __name__ == "__main__":
    main()
