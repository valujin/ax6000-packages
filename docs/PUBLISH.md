# Publish an existing local build

Use publish-existing.yml to publish a completed local build without compiling
the firmware again. The repository must have Pages configured for GitHub Actions,
immutable releases enabled, and the OPKG_SIGNING_KEY repository secret set.

Create a unique draft GitHub release in the **same repository**, upload
raw.tar.zst and raw.tar.zst.sha256, and publish the draft only after both uploads
complete. Publication makes the release immutable. The workflow requires the
GitHub API's immutable flag and rejects draft releases.

The archive contains the **contents** of the exported raw directory, with
regular files and directories only. Keep the generated profile.json,
lock.json, firmware-manifest.json, complete firmware/SDK files and IPKs.
Omit symlinks used as duplicate directory aliases; preserve every actual regular
IPK and firmware file. Do not include private signing keys.

For a raw tree already inspected to contain no links or special files:

~~~bash
tar -C /absolute/path/to/raw -cf - . | zstd -T1 -3 -o raw.tar.zst
sha256sum raw.tar.zst > raw.tar.zst.sha256
gh release create firmware-ec9ef10efc65 --draft \
  --repo OWNER/REPOSITORY --title 'AX6000 firmware ec9ef10efc65'
gh release upload firmware-ec9ef10efc65 raw.tar.zst raw.tar.zst.sha256 \
  --repo OWNER/REPOSITORY
gh release edit firmware-ec9ef10efc65 --draft=false --repo OWNER/REPOSITORY
gh workflow run publish-existing.yml --repo OWNER/REPOSITORY \
  --ref main --field release_tag=firmware-ec9ef10efc65
~~~

Use the actual default branch instead of main when different. Creating the
release and uploading the local artifacts are separate from workflow execution;
the workflow only reads that release.

The importer verifies SHA256 before extraction, rejects traversal, links,
devices and duplicate paths, and checks the firmware source identity against the
checked-in new-firmware profile. It also checks the exported profile, lock and
manifest agree on the source and actual kernel ABI. Extraction is limited to
8 GiB and 100,000 members. Release-supplied executables are never run.

Actions builds the small, pinned official host usign, validates/signs the feed
with the existing publisher, and deploys only if the complete site is at most
1,000,000,000 bytes. A failed import or deployment leaves the existing Pages
deployment intact and retains the source build in its release. Firmware and
module installation on the router remain separate user actions.
