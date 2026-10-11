# AX6000 firmware and signed package repository

User intent: Redmi AX6000 stock layout currently runs PadavanOnly ImmortalWrt
24.10-SNAPSHOT r33471-924aaf9416 with MediaTek vendor wireless drivers. Build a
personal repository with updated VPN, routing, GeoIP-related and MultiWAN
packages, plus available kernel modules. User subsequently authorized a new
firmware build with its own matching modules and SDK. Never flash automatically.

Standalone repository: valujin/ax6000-packages. Keep firmware fork untouched.
Freeze source ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8 and stock device profile.
Use fork defconfig, correct stale subtarget symbols, retain vendor driver flags.
Build all target-selectable modules; report excluded/inapplicable modules.
Discover exact kernel ABI from generated kernel IPK and bind publication to it.
The existing kernel6.6.95 ABI remains a disabled legacy profile, because its
original SDK is unavailable. Never falsify ABI or relax opkg/kernel checks.

Pin feed commits in locks; monitor compatible24.10 branches daily. Package
Tailscale official ARM64 binaries with checksum and branch-compatible service
wrappers. Add maintained AWG3.1 community packaging referring official module
and tools upstream, WireGuard, PBR, mwan3, banIP, OpenVPN, IPsec and PPP VPN tools.
New packages do not silently activate routing/firewall configurations.

Full source build yields firmware, SDK, IPKs and provenance. Signed index has
package SHA256, exact kernel dependencies and architecture checks. Exclude kernel,
libc, base-files, busybox and opkg from supplemental package index. Publish complete
site atomically; failed runs preserve previous working site. Preserve stable
usign key privately and provision Actions secret without exposing it in logs.

Server builds run in isolated Docker with4CPU quota,10GiB RAM and zero swap;
compiler subprocesses use half host CPU affinity, nice19 and idle I/O class.
No host OS upgrade, service restart, host dependency installation or flash.
Daily package builds run on GitHub hosted runners, against verified matching SDK.
Full firmware updates are manual; source/base kernel never auto-roll forward.

Validation: archive/metadata/ABI failure cases, actual usign signed-index round
trip, shell and Actions lint, target config/package retention, complete build
exit status and checksummed outputs. Boot validation requires physical router
and is explicitly separate from build success.
