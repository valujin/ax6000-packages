# GitHub Actions and Pages

This is a standalone package repository. Builds use GitHub hosted
`ubuntu-24.04` runners. `scripts/build.sh` limits compilation to half the runner's
available CPUs; no runner or build daemon is installed on the router or server.

## Workflows

`.github/workflows/test.yml` runs Python unit tests and shell syntax checks on
every push, pull request and manual run. These checks do not download an SDK or
compile firmware. The test runner compiles the small, pinned OpenWrt host `usign`
utility with one compiler job so the signing integration test runs against a real
implementation. Locally that test also runs when `USIGN` points to a host
executable; otherwise it reports a skip.

`.github/workflows/packages.yml` checks sources daily at 03:17 UTC and supports
manual runs under **Actions → Monitor and publish packages → Run workflow**.
Scheduled execution follows GitHub's default-branch schedule behavior. Manual
publication also runs only on the repository's default branch.

The package workflow first checks `profiles/redmi-ax6000.json`. The initial
`enabled: false` profile produces an actionable summary and still monitors feed
revisions and the latest Tailscale release. It skips SDK downloads, builds and
deployment. Complete and verify the router
compatibility profile before enabling it. In particular, use the exact firmware
source commit and kernel ABI dependency, plus the URL and SHA-256 checksum of a
matching SDK. A generic SDK for the same chipset is not sufficient evidence of
kernel module compatibility.

Monitoring resolves source revisions and the Tailscale
release into `state/lock.json`. The workflow compares this with the last
successful publication's `state/lock.json`. An unchanged lock skips compilation.
The manual **force** option rebuilds unchanged inputs; use it after changing build
or publishing code. Profile changes are included in source-lock identity.

Only HTTP 404 means there is no previous publication. Other download failures or
invalid JSON fail the monitor rather than quietly launching a redundant build.
The comparison URL defaults to
`https://OWNER.github.io/REPOSITORY/state/lock.json`. If Pages uses a custom domain
or a different path, set repository variable `PACKAGE_REPOSITORY_URL` to the
HTTPS site base URL, without `/state/lock.json`.

## Bootstrap

1. Push this directory as its own GitHub repository, with the workflows and
   scripts on its default branch.
2. In **Settings → Pages**, select **GitHub Actions** as the build/deployment
   source. The workflow reads existing Pages configuration; it does not enable
   Pages through the API.
3. Create a usign key pair in a trusted environment with an available OpenWrt
   `usign` executable:

   ```sh
   umask 077
   usign -G -s opkg-private.key -p opkg-public.key -c ax6000-packages
   ```

4. Store the complete contents of `opkg-private.key` as the repository Actions
   secret `OPKG_SIGNING_KEY`. Keep the private key out of Git. Preserve a private
   backup so future indexes retain the same trusted signing identity. The
   publisher derives and publishes the corresponding public key; no separate
   public-key secret is required.
   `scripts/install-feed.sh` pins this repository's public-key fingerprint. When
   creating a separate repository with a new key pair, update its
   `expected_fingerprint` to the output of `usign -F -p opkg-public.key` before
   distributing that installation script.
5. Configure `PACKAGE_REPOSITORY_URL` if the default Pages URL is unsuitable.
6. Finish the compatibility profile and set `enabled` to `true`. Run the package
   workflow manually on the default branch and inspect its summary and logs.
   Install the public signing key on the router before using its signed feed.

The existing `github-pages` environment must allow deployment from the default
branch. GitHub repository/environment policy controls access to secrets and
deployment. No personal access token is needed: checkout receives `contents:
read`, Pages setup receives `pages: read`, and the deployment job alone receives
`pages: write` and `id-token: write`. Pull requests execute only the test workflow.

## Publication and recovery

Every build consumes the resolved source lock. The publisher validates package
metadata against the profile, including package architecture and the exact kernel
dependency for kernel modules, before constructing and signing the index.
Validation failure prevents upload. The private signing key is written with mode
`0600` into the runner's temporary directory during publication and removed when
that step exits. It is never included in the Pages artifact.

The artifact contains the complete public repository, its signed index, public
key, build metadata and `state/lock.json`. A separate Pages deployment job runs
only after the artifact is ready. Workflow concurrency serializes monitoring,
building and deploying, with `cancel-in-progress: false` so a newer scheduled
check does not cancel an active publication.

A failed build, signing step or deployment leaves the previously deployed Pages
site in place. Its lock remains the last successful state, so a subsequent check
retries the changed sources. A lock from a failed build is never treated as a
successful publication. Source-lock Actions artifacts are retained for seven
days for inspection; they are not used as the success baseline.

The runner's 350-minute build timeout and GitHub account quota still apply.
Compiling all kernel modules can exceed hosted runner time or disk limits; review
build logs and narrow the profile package selection if needed. CI never falls
back to a self-hosted machine. GitHub may delay scheduled runs; manual execution
is available when an immediate check is needed.

## Local lightweight checks

```sh
python3 -m unittest discover -s tests -v
for script in scripts/*.sh; do bash -n "$script"; done
```

These validate the repository tooling, not the router's runtime ABI. A successful
lightweight check alone is not permission to enable a profile with an unverified
SDK.

## New firmware and matching SDK

`.github/workflows/firmware.yml` is a manual workflow for
`profiles/redmi-ax6000-new.json`. It builds a new firmware image, its SDK and
kernel modules together from the pinned full source tree. The resulting
`raw/profile.json` and `raw/lock.json` carry the actual new kernel ABI; publication
uses those generated files. The existing installed-firmware profile remains
disabled until its exact compatible inputs are supplied.

Every successful full build uploads the complete firmware, SDK, packages and
metadata as an Actions artifact retained for fourteen days. Build logs and the
source lock are retained for seven days, including failed builds. **publish** is
off by default so a full build can produce downloadable artifacts before Pages
and the signing secret are configured. Selecting it validates and signs the
matching package feed and deploys the complete repository through the same
serialized Pages publication path.

Full compilation requires at least 60 GiB free storage and refuses to start if
the hosted runner lacks it. Standard GitHub hosted Linux runners guarantee only
[14 GB of storage](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
so the standard runner can fail this preflight. Use the documented isolated local
full-build command on a machine with sufficient storage, or configure repository
variable `FIRMWARE_RUNNER` with the label of a larger GitHub hosted Ubuntu runner
provisioned in the repository's runner settings. A runtime guard rejects a
self-hosted runner before checkout or build commands. This
workflow never switches to a self-hosted runner or removes installed server
software to free space. The build retains the half-CPU resource limit and the
350-minute job timeout.

The entire published site must fit the
[GitHub Pages 1 GB limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
If the generated SDK, firmware and package feed exceed that limit, Pages
publication fails before upload while the complete Actions artifact remains
available. A failed publication keeps the previous Pages deployment intact.

GitHub documents the [Pages workflow and permissions](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
and the [Pages deployment action](https://github.com/actions/deploy-pages).
