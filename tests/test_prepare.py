"""Boundary tests for SDK preparation; fixtures never download or compile."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare", ROOT / "scripts/prepare.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
sys.path.insert(0, str(ROOT / "scripts"))
FIRMWARE_SPEC = importlib.util.spec_from_file_location("prepare_firmware", ROOT / "scripts/prepare-firmware.py")
firmware = importlib.util.module_from_spec(FIRMWARE_SPEC)
FIRMWARE_SPEC.loader.exec_module(firmware)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        self.profile = {
            "schema_version": 1, "id": "router", "enabled": True,
            "firmware": {"repository": "https://example.com/firmware.git",
                         "commit": "a" * 40, "kernel_version": "6.6.1~0123456789abcdef0123456789abcdef-r1",
                         "target": "mediatek", "subtarget": "filogic",
                         "architecture": "aarch64_cortex-a53"},
            "sdk": {"url": "https://example.com/sdk.tar.xz", "sha256": "b" * 64},
            "feeds": [{"name": "packages", "url": "https://example.com/packages.git",
                       "commit": "c" * 40}],
            "packages": ["tailscale", "kmod-wireguard"], "all_kmods": True,
        }
        self.lock = {"feeds": {"packages": "d" * 40}}

    def test_disabled_profile_refuses_even_without_sdk(self):
        self.profile["enabled"] = False
        self.profile["sdk"] = None
        with self.assertRaisesRegex(ValueError, "disabled"):
            prepare.validate(self.profile, self.lock)

    def test_unpinned_feed_refuses_before_writing_config(self):
        self.lock["feeds"]["packages"] = "main"
        with self.assertRaisesRegex(ValueError, "40"):
            prepare.configure_feeds(self.profile, self.lock, self.sdk)
        self.assertFalse((self.sdk / "feeds.conf").exists())

    def test_locked_feed_revision_replaces_profile_revision(self):
        prepare.configure_feeds(self.profile, self.lock, self.sdk)
        self.assertEqual((self.sdk / "feeds.conf").read_text(),
                         "src-git packages https://example.com/packages.git^" + "d" * 40 + "\n")

    def test_running_kernel_package_version_is_accepted(self):
        self.profile["firmware"]["kernel_version"] = "6.6.95~b5eab2158127d54f4b11361a8599235a-r1"
        prepare.validate(self.profile, self.lock)

    def test_kernel_build_requires_matching_prepared_tree(self):
        self.profile["firmware"]["kernel_version"] = "6.6.95~b5eab2158127d54f4b11361a8599235a-r1"
        with self.assertRaisesRegex(ValueError, "prepared kernel"):
            prepare.check_kernel(self.profile, self.sdk)
        kernel = self.sdk / "build_dir/target-aarch64/linux-mediatek_filogic/linux-6.6.95"
        (kernel / "include/generated").mkdir(parents=True)
        (kernel / "include/config").mkdir()
        (kernel / "Makefile").write_text("kernel")
        (kernel / ".config").write_text("CONFIG_ARM64=y")
        (kernel / "include/generated/autoconf.h").write_text("config")
        (kernel / "include/config/kernel.release").write_text("6.6.95\n")
        (kernel / ".vermagic").write_text("b5eab2158127d54f4b11361a8599235a\n")
        prepare.check_kernel(self.profile, self.sdk)
        (kernel / ".vermagic").write_text("0" * 32)
        with self.assertRaisesRegex(ValueError, "kernel ABI"):
            prepare.check_kernel(self.profile, self.sdk)

    def test_archive_rejects_parent_traversal_and_escaping_symlink(self):
        for name, link in [("sdk/../../escape", None), ("sdk/link", "../../escape")]:
            with self.subTest(name=name):
                archive = self.root / "bad.tar"
                with tarfile.open(archive, "w") as tar:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type, member.linkname = tarfile.SYMTYPE, link
                        tar.addfile(member)
                    else:
                        member.size = 1
                        tar.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    prepare.extract_sdk(archive, self.root / "unpacked")
        self.assertFalse((self.root / "escape").exists())

    def test_archive_extracts_internal_symlink_and_sdk_root(self):
        archive = self.root / "sdk.tar"
        with tarfile.open(archive, "w") as tar:
            member = tarfile.TarInfo("openwrt-sdk/Makefile")
            member.size = 4
            tar.addfile(member, io.BytesIO(b"all:"))
            member = tarfile.TarInfo("openwrt-sdk/link")
            member.type, member.linkname = tarfile.SYMTYPE, "Makefile"
            tar.addfile(member)
        result = prepare.extract_sdk(archive, self.root / "unpacked")
        self.assertEqual(result.name, "openwrt-sdk")
        self.assertEqual((result / "link").read_text(), "all:")

    def metadata(self):
        (self.sdk / "tmp").mkdir()
        (self.sdk / "tmp/.packageinfo").write_text(
            "Package: tailscale\n\nPackage: kmod-wireguard\n\n"
            "Package: kmod-tun\n\nPackage: kmod-other-target\n\n")
        (self.sdk / "tmp/.config-package.in").write_text(
            "config PACKAGE_tailscale\nconfig PACKAGE_kmod-wireguard\n"
            "config PACKAGE_kmod-tun\nconfig PACKAGE_kmod-other-target\n")
        (self.sdk / ".config").write_text("CONFIG_TARGET_mediatek=y\n")

    def test_selection_uses_metadata_and_reports_incompatible_optional_kmods(self):
        self.metadata()
        prepare.select_packages(self.profile, self.sdk)
        config = (self.sdk / ".config").read_text()
        self.assertIn("CONFIG_PACKAGE_tailscale=m", config)
        self.assertIn("CONFIG_PACKAGE_kmod-tun=m", config)
        (self.sdk / ".config").write_text(config.replace("CONFIG_PACKAGE_kmod-other-target=m\n", ""))
        result = prepare.verify_selection(self.profile, self.sdk)
        self.assertEqual(result["incompatible_kmods"], ["kmod-other-target"])
        self.assertEqual(result["selected_kmods"], ["kmod-tun", "kmod-wireguard"])

    def test_missing_requested_package_refuses(self):
        self.metadata()
        self.profile["packages"].append("missing")
        with self.assertRaisesRegex(ValueError, "missing"):
            prepare.select_packages(self.profile, self.sdk)

    def test_sdk_identity_rejects_other_target_or_source_revision(self):
        (self.sdk / "include").mkdir()
        (self.sdk / "include/version.mk").write_text("REVISION:=r100-aaaaaaaaaa\n")
        (self.sdk / ".config").write_text(
            'CONFIG_TARGET_BOARD="mediatek"\nCONFIG_TARGET_SUBTARGET="filogic"\n'
            'CONFIG_TARGET_ARCH_PACKAGES="aarch64_cortex-a53"\n')
        prepare.check_sdk_identity(self.profile, self.sdk)
        (self.sdk / "include/version.mk").write_text("REVISION:=r100-cccccccccc\n")
        with self.assertRaisesRegex(ValueError, "source revision"):
            prepare.check_sdk_identity(self.profile, self.sdk)

    def baseline(self):
        directory = self.sdk / "ax6000-baseline/ipks"
        directory.mkdir(parents=True)
        control_text = ("Package: kmod-wireguard\nVersion: 6.6.1-r1\n"
                        "Architecture: aarch64_cortex-a53\nDepends: kernel (= " + self.profile["firmware"]["kernel_version"] + ")\n")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            member = tarfile.TarInfo("./control")
            member.size = len(control_text.encode())
            archive.addfile(member, io.BytesIO(control_text.encode()))
        path = directory / "kmod-wireguard.ipk"
        with tarfile.open(path, mode="w:gz") as archive:
            member = tarfile.TarInfo("./control.tar.gz")
            member.size = len(buffer.getvalue())
            archive.addfile(member, io.BytesIO(buffer.getvalue()))
        (directory.parent / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "source_commit": "a" * 40,
            "architecture": "aarch64_cortex-a53", "kernel_version": self.profile["firmware"]["kernel_version"],
            "core_kmods": ["kmod-wireguard"],
            "files": [{"package": "kmod-wireguard", "version": "6.6.1-r1", "path": "ipks/kmod-wireguard.ipk",
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}))
        return path

    def test_sdk_reuses_abi_checked_frozen_core_module_without_selecting_it(self):
        self.metadata()
        self.baseline()
        self.profile["all_kmods"] = False
        prepare.select_packages(self.profile, self.sdk)
        self.assertNotIn("CONFIG_PACKAGE_kmod-wireguard=m", (self.sdk / ".config").read_text())
        result = prepare.verify_selection(self.profile, self.sdk)
        self.assertEqual(result["baseline_kmods"], ["kmod-wireguard"])

    def test_corrupted_sdk_baseline_refuses(self):
        path = self.baseline()
        path.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "checksum"):
            prepare.baseline_packages(self.profile, self.sdk)

    def test_defconfig_dropping_explicit_package_refuses(self):
        self.metadata()
        prepare.select_packages(self.profile, self.sdk)
        (self.sdk / ".config").write_text("CONFIG_PACKAGE_kmod-wireguard=m\n")
        with self.assertRaisesRegex(ValueError, "tailscale"):
            prepare.verify_selection(self.profile, self.sdk)

    def test_static_tailscale_preserves_service_wrappers_without_go_dependency(self):
        recipe = self.sdk / "feeds/packages/net/tailscale"
        recipe.mkdir(parents=True)
        (recipe / "files").mkdir()
        (recipe / "files/tailscale.init").write_text("service")
        (recipe / "files/tailscale.conf").write_text("config")
        # Full source trees expose their own packages through feeds/base. Recipe
        # discovery must not count that alias as a different upstream recipe.
        (self.sdk / "package/feeds/packages").mkdir(parents=True)
        (self.sdk / "feeds/base").symlink_to(self.sdk / "package", target_is_directory=True)
        (self.sdk / "package/feeds/packages/tailscale").symlink_to(recipe, target_is_directory=True)
        (recipe / "Makefile").write_text(
            "PKG_NAME:=tailscale\nPKG_VERSION:=1.80.0\nPKG_RELEASE:=2\n"
            "PKG_SOURCE_URL:=https://codeload.github.com/tailscale/tailscale/tar.gz/v$(PKG_VERSION)?\n"
            "PKG_HASH:=" + "e" * 64 + "\ninclude ../../lang/golang/golang-package.mk\n")
        self.lock["tailscale"] = {"version": "1.90.1", "sha256": "f" * 64,
                                  "artifact": "static", "url": "https://pkgs.tailscale.com/stable/tailscale_1.90.1_arm64.tgz"}
        prepare.apply_overrides(self.lock, self.sdk, self.profile)
        overlay = self.sdk / "package/ax6000-overrides/tailscale"
        content = (overlay / "Makefile").read_text()
        self.assertIn("PKG_VERSION:=1.90.1", content)
        self.assertIn("PKG_HASH:=" + "f" * 64, content)
        self.assertNotIn("golang", content)
        self.assertIn("$(PKG_BUILD_DIR)/tailscale", content)
        self.assertIn("$(PKG_BUILD_DIR)/tailscaled", content)
        self.assertEqual((recipe / "Makefile").read_text().splitlines()[1], "PKG_VERSION:=1.80.0")
        self.assertEqual((overlay / "files/tailscale.init").read_text(), "service")

    def test_static_tailscale_refuses_non_arm64_profile(self):
        self.profile["firmware"]["architecture"] = "mips_24kc"
        self.lock["tailscale"] = {"version": "1.90.1", "sha256": "f" * 64,
                                  "artifact": "static", "url": "https://pkgs.tailscale.com/stable/tailscale_1.90.1_arm64.tgz"}
        with self.assertRaisesRegex(ValueError, "ARM64"):
            prepare.validate(self.profile, self.lock)

    def test_resource_limit_enforces_affinity_and_rejects_excess_jobs(self):
        available = sorted(os.sched_getaffinity(0))
        cap = max(1, len(available) // 2)
        script = ROOT / "scripts/resource-limit.sh"
        probe = subprocess.run([str(script), "--", "python3", "-c",
                                "import os; print(len(os.sched_getaffinity(0)))"],
                               capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(int(probe.stdout), cap)
        rejected = subprocess.run([str(script), "--jobs", str(cap + 1), "--", "true"],
                                  capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)


class FirmwarePreparationTests(unittest.TestCase):
    metadata = PrepareTests.metadata

    def setUp(self):
        PrepareTests.setUp(self)
        self.profile["firmware"]["kernel_version"] = None
        self.profile["build_source"] = {"defconfig": "defconfig/mt7986-ax6000.config",
                                         "device": "xiaomi_redmi-router-ax6000-stock"}
        self.profile.pop("sdk")
        (self.sdk / "defconfig").mkdir()
        (self.sdk / "defconfig/mt7986-ax6000.config").write_text(
            "CONFIG_TARGET_mediatek=y\nCONFIG_TARGET_mediatek_mt7986=y\n"
            "CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_other-router=y\n"
            "CONFIG_MTK_MT_WIFI=m\nCONFIG_MTK_DBDC_MODE=y\nCONFIG_PACKAGE_kmod-mt_wifi=y\n")

    def test_source_config_preserves_vendor_wifi_and_selects_only_stock(self):
        firmware.configure(self.profile, self.lock, self.sdk)
        config = (self.sdk / ".config").read_text()
        self.assertIn("CONFIG_MTK_MT_WIFI=m\n", config)
        self.assertIn("CONFIG_MTK_DBDC_MODE=y\n", config)
        self.assertIn("CONFIG_PACKAGE_kmod-mt_wifi=y\n", config)
        self.assertIn("CONFIG_TARGET_mediatek_filogic=y\n", config)
        self.assertNotIn("CONFIG_TARGET_mediatek_mt7986=y", config)
        self.assertNotIn("DEVICE_other-router=y", config)
        self.assertIn("DEVICE_xiaomi_redmi-router-ax6000-stock=y", config)
        self.assertIn("CONFIG_ALL_KMODS=y", config)
        self.assertIn("CONFIG_SIGNED_PACKAGES=y\n", config)
        self.assertIn("CONFIG_SIGNATURE_CHECK=y\n", config)

    def test_source_selection_preserves_vendor_drivers_and_includes_requested_image_packages(self):
        self.metadata()
        (self.sdk / "tmp/.packageinfo").write_text((self.sdk / "tmp/.packageinfo").read_text() + "Package: kmod-mt_wifi\n")
        (self.sdk / "tmp/.config-package.in").write_text((self.sdk / "tmp/.config-package.in").read_text() + "config PACKAGE_kmod-mt_wifi\n")
        (self.sdk / "tmp/.config-target.in").write_text(
            "menuconfig TARGET_DEVICE_mediatek_filogic_DEVICE_other-router\n"
            "menuconfig TARGET_DEVICE_mediatek_filogic_DEVICE_xiaomi_redmi-router-ax6000-stock\n")
        self.profile["image_packages"] = ["tailscale"]
        firmware.configure(self.profile, self.lock, self.sdk)
        firmware.select(self.profile, self.sdk)
        config = (self.sdk / ".config").read_text()
        self.assertIn("CONFIG_PACKAGE_kmod-mt_wifi=y\n", config)
        self.assertIn("CONFIG_PACKAGE_tailscale=y\n", config)
        self.assertIn("CONFIG_PACKAGE_kmod-wireguard=m\n", config)
        self.assertIn("CONFIG_SIGNED_PACKAGES=y\n", config)
        self.assertIn("CONFIG_SIGNATURE_CHECK=y\n", config)
        self.assertIn("# CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_other-router is not set", config)

    def test_source_rejects_ubootmod_layout(self):
        self.profile["build_source"]["device"] = "xiaomi_redmi-router-ax6000-ubootmod"
        with self.assertRaisesRegex(ValueError, "stock"):
            firmware.validate(self.profile, self.lock)

if __name__ == "__main__":
    unittest.main()
