# Retired Linux 6.6 workflows

On 2026-10-22 this repository stopped producing firmware and package updates.
Future work is limited to the recommended ImmortalWrt25.12 / Linux6.12 line.
Attended Sysupgrade support is discontinued.

All six GitHub Actions workflows were disabled server-side. Their original
contents are preserved byte-for-byte here with `.yml.disabled` names, outside
`.github/workflows`, so source changes cannot restart scheduled, push, pull
request or manual workflow runs:

- `firmware.yml.disabled`: firmware and SDK builds.
- `packages.yml.disabled`: scheduled source monitoring, package builds,
  publication and heartbeat commits.
- `publish-existing.yml.disabled`: manual generation publication.
- `deploy-signed-site.yml.disabled`: manual signed-site deployment.
- `tailscale-compact.yml.disabled`: manual compact Tailscale verification builds.
- `test.yml.disabled`: the retired GitHub test pipeline.

The repository is not archived on GitHub. Published releases, package feeds,
Pages content, run history and artifacts have not been removed or replaced.
Build and validation sources remain available as historical material.
