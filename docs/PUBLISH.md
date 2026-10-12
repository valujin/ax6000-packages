# Publish an existing local build

Use publish-existing.yml to publish a completed local build without compiling
the firmware again. The repository must have Pages configured for GitHub Actions,
immutable releases enabled, and the OPKG_SIGNING_KEY repository secret set.

Create a unique draft GitHub release in the **same repository**, upload
raw.tar.zst and raw.tar.zst.sha256, and publish the draft only after both uploads
complete. Publication makes the release immutable. The workflow requires the
GitHub API's immutable flag and rejects draft releases.
Upload every other desired release asset, including any separate firmware,
SDK or provenance downloads, while the release is still a draft. Assets cannot
be added, replaced or deleted after publication.

The archive contains the **contents** of the exported raw directory, with
regular files and directories only. Keep the generated profile.json,
lock.json, firmware-manifest.json, complete firmware/SDK files and IPKs.
Omit symlinks used as duplicate directory aliases; preserve every actual regular
IPK and firmware file. Do not include private signing keys.

For a raw tree already inspected to contain no links or special files:

~~~bash
set -euo pipefail
raw=/absolute/path/to/raw
repo=valujin/ax6000-packages
build_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["build_id"])' "$raw/profile.json")
recipe_commit=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["recipe_commit"])' "$raw/firmware-manifest.json")
[[ "$recipe_commit" =~ ^[0-9a-f]{40}$ ]]
tag="firmware-$build_id"
gh api "repos/$repo/commits/$recipe_commit" --jq .sha
tar -C "$raw" --hard-dereference -cf - . | zstd -T1 -3 -o raw.tar.zst
sha256sum raw.tar.zst > raw.tar.zst.sha256
python3 scripts/import-release.py --archive raw.tar.zst \
  --checksum raw.tar.zst.sha256 --output /absolute/fresh/path/imported-raw \
  --expected-profile profiles/redmi-ax6000-new.json
# Complete local signing, pinned-key comparison and site-size checks below
# before creating the release. Use fresh import/staging paths for every attempt.
gh release create "$tag" --draft --target "$recipe_commit" \
  --repo "$repo" --title "AX6000 firmware $build_id" \
  --notes 'Firmware, SDK and packages for the kernel ABI recorded in the build profile.'
gh release upload "$tag" raw.tar.zst raw.tar.zst.sha256 --repo "$repo"
# Upload all additional desired assets here and verify the completed uploads.
gh release edit "$tag" --draft=false --repo "$repo"
gh api -H 'X-GitHub-Api-Version: 2026-03-10' \
  "repos/$repo/releases/tags/$tag" > release.json
python3 scripts/import-release.py --release-metadata release.json
gh workflow run publish-existing.yml --repo "$repo" \
  --ref main --field "release_tag=$tag"
~~~

Use the actual default branch instead of main when different. Creating the
release and uploading the local artifacts are separate from workflow execution;
the workflow only reads that release. The generated build ID includes the SDK
checksum, so different SDK cohorts have distinct tags. The release tag points
to the actual recipe commit recorded by the build, rather than whichever commit
happens to be at the tip of the default branch.

Before creating or publishing the release, stage and sign the complete site
locally using a trusted host usign and the existing private repository key.
Verify the generated `public/repository.pub` key payload matches the pinned
`keys/repository.pub`; comments may differ. For example:

~~~bash
python3 - /absolute/path/to/public/repository.pub keys/repository.pub <<'PY'
import base64
from pathlib import Path
import sys

payloads = [base64.b64decode(Path(path).read_text().splitlines()[1], validate=True)
            for path in sys.argv[1:]]
if payloads[0] != payloads[1]:
    raise SystemExit('Generated signing key differs from the key bundled in the firmware')
PY
~~~

Keep the private key outside the raw bundle and Git. Check the complete staged
site size before making the release immutable, and verify the release asset
checksums after upload while the draft can still be corrected.

The importer verifies SHA256 before extraction, rejects traversal, links,
devices and duplicate paths, and checks the firmware source identity against the
checked-in new-firmware profile. It also checks the exported profile, lock and
manifest agree on the source and actual kernel ABI. Extraction is limited to
8 GiB and 100,000 members. Every regular file under `bin` must appear exactly
once in the firmware manifest, with its actual size and SHA256. Missing, extra
or duplicate inventory entries are rejected. The recorded stock sysupgrade
image and exactly one SDK matching the generated profile's URL and SHA256 are
required. Release-supplied executables are never run.

Actions builds the small, pinned official host usign, validates/signs the feed
with the existing publisher, and deploys only if the complete site is at most
1,000,000,000 bytes. A failed import or deployment leaves the existing Pages
deployment intact and retains the source build in its release. Firmware and
module installation on the router remain separate user actions.

After deployment succeeds, download the public SDK URL from `raw/profile.json`
and verify its SHA256. Verify the live public signing key still matches the
pinned repository key and the exact exported SDK has passed the package build
checks. Only then copy the generated `raw/profile.json` byte-for-byte to
`profiles/redmi-ax6000.json`, commit and push to activate daily package builds.
Preserve its exact kernel ABI, fixed source commit, SDK URL/checksum and
`all_kmods: false`; the SDK contains the frozen module baseline. Keep
`profiles/redmi-ax6000-new.json` as the separate full firmware build profile.
