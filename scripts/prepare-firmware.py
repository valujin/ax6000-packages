#!/usr/bin/env python3
"""Prepare a pinned AX6000 stock-layout firmware build and export its actual ABI."""
import argparse
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
    return profile


def configure(profile, lock, source):
    validate(profile, lock)
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
    (source / ".config").write_text(config)


def set_symbols(config, values):
    config = re.sub(r"^(?:# )?CONFIG_([^= ]+)(?:=.*| is not set)\n?",
                    lambda match: "" if match.group(1) in values else match.group(0), config, flags=re.M)
    return config.rstrip() + "\n" + "".join(
        f"CONFIG_{symbol}={value}\n" if value != "n" else f"# CONFIG_{symbol} is not set\n"
        for symbol, value in sorted(values.items()))


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
    baseline_y = set(re.findall(r"^CONFIG_PACKAGE_(.+)=y$", config, re.M))
    kmods = sorted(name for name in available if name.startswith("kmod-")) if profile.get("all_kmods", True) else []
    for name in sorted(requested | set(kmods)):
        values["PACKAGE_" + name] = "y" if name in baseline_y or name in profile.get("image_packages", []) else "m"
    config_path.write_text(set_symbols(config, values))
    report = {"profile": profile["id"], "requested": sorted(requested), "candidate_kmods": kmods,
              "source_commit": profile["firmware"]["commit"], "device": profile["build_source"]["device"],
              "baseline_vendor_drivers": sorted(name for name in baseline_y if name in
                                                 {"kmod-mt_wifi", "kmod-conninfra", "kmod-warp", "kmod-mediatek_hnat"})}
    (source / "build-selection.json").write_text(json.dumps(report, indent=2) + "\n")


def verify(profile, source):
    report = prepare.verify_selection(profile, source)
    config = (source / ".config").read_text()
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


def augment_sdk(profile, source, archive, controls, abi):
    """Preserve the original SDK kernel configuration and freeze in-tree module IPKs."""
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
    baseline = [(path, control) for path, control in controls
                if control.get("Package") == "kernel" or control.get("Package") in core]
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
            prepare.require(not target.exists(), "duplicate core baseline package filename")
            shutil.copy2(path, target)
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"package": control["Package"], "version": control["Version"],
                          "path": "ipks/" + path.name, "sha256": digest})
        (snapshot / "manifest.json").write_text(json.dumps(
            {"schema_version": 1, "source_commit": profile["firmware"]["commit"],
             "kernel_version": abi, "architecture": profile["firmware"]["architecture"],
             "core_kmods": sorted(core & {control["Package"] for _, control in baseline}), "files": files},
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
    # Daily SDK rebuilds keep the kernel configuration fixed and reuse the frozen core modules.
    generated["all_kmods"] = False
    generated["feeds"].insert(0, {"name": "base", "url": profile["firmware"]["repository"],
                                  "commit": profile["firmware"]["commit"]})
    build_id = f"{profile['id']}-{profile['firmware']['commit'][:12]}"
    site_url = profile["build_source"].get("publication_url", "https://valujin.github.io/ax6000-packages")
    url_base = site_url.rstrip("/") + "/firmware/" + build_id
    prepare.https_url(url_base, "firmware publication URL")
    with sdks[0].open("rb") as stream:
        sdk_hash = hashlib.file_digest(stream, "sha256").hexdigest()
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
    parser.add_argument("--phase", choices=("validate", "configure", "check-feeds", "overrides", "select", "verify", "export"), default="validate")
    args = parser.parse_args()
    try:
        profile, lock = json.loads(args.profile.read_text()), json.loads(args.lock.read_text())
        validate(profile, lock)
        if args.phase == "validate":
            print(f"Validated new-firmware source profile {profile['id']}; ABI will be discovered after build")
            return
        prepare.require(args.source_dir is not None and args.source_dir.is_dir(), "--source-dir is required")
        if args.phase == "export":
            prepare.require(args.output_dir is not None, "--output-dir is required")
            export(profile, lock, args.source_dir, args.output_dir)
        else:
            actions = {"configure": lambda: configure(profile, lock, args.source_dir),
                       "check-feeds": lambda: prepare.verify_feeds(profile, lock, args.source_dir),
                       "overrides": lambda: prepare.apply_overrides(lock, args.source_dir, profile),
                       "select": lambda: select(profile, args.source_dir),
                       "verify": lambda: verify(profile, args.source_dir)}
            actions[args.phase]()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"prepare-firmware: {error}\n")


if __name__ == "__main__":
    main()
