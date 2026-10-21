# Deploy an existing signed site

`Deploy a verified signed site` is a manual Pages deployment for the 6.6 OPKG cohort. It copies a reviewed, already signed site from an immutable firmware Release in `valujin/ax6000-packages`. It performs no firmware/package build and needs no private signing key. The original complete ImageBuilder stays in its Release; the signed download catalog references it there. The deployed site must contain fewer than 1,000,000,000 regular-file bytes, including its snapshot signatures.

The publisher/operator must first create the complete site with the production public-key identity, sign `snapshot-manifest.json`, and review the actual generated profile, lock, packages, native firmware and external ImageBuilder references. Upload these two assets **before** publishing/freezing the Release:

- `public.tar.zst`: site files directly at archive root, without an enclosing `public/` directory; regular files and directories only. Preserve the exact signed bytes. Do not add the native ImageBuilder archive to the Pages site.
- `public.tar.zst.sha256`: exactly one lowercase SHA256 line, `<sha256>  public.tar.zst`, with one final newline.

Record the SHA256 of the compressed archive and the SHA256 of the exact signed `snapshot-manifest.json` in the publication review. Supply those reviewed hashes and `firmware-<build_id>` to the manual workflow on the repository default branch. The workflow does not derive its expected hashes from untrusted downloaded files. A changed site requires a new immutable Release; an existing immutable Release cannot acquire these assets afterward.

Before extracting anything, the workflow checks same-repository Release/asset identities, immutable/non-draft status, canonical asset names, sizes and operator-pinned archive hash. It compares Release metadata before and after download. The importer rejects duplicate paths, links, special files, escapes, oversized metadata, excessive members, oversized compressed or unpacked content, and never executes archive files. It writes a fresh directory only after verification succeeds.

Verification uses the checked-in `keys/repository.pub` and independently built official usign, pinned to `f1f65026a94137c91b5466b149ef3ea3f20091e9`. It verifies the snapshot signature and exact complete file inventory; profile/lock/source/SDK/build/kernel identities; ASU cohort66 with OPKG; public-key payload; `Packages.sig`, compressed index and package hashes; and `downloads.sig` plus reconstructed immutable-generation asset references. The checked-in source profile constrains the firmware source/board/architecture. Its future recipe seed is deliberately not compared to the older signed generation's seed. The snapshot hash and signature authenticate that seed.

Every asset listed in the signed download catalog must also exist exactly once, with canonical spelling, in the actual immutable Release metadata. Uploaded status, exact size, URL and GitHub's `sha256:` digest must match the signed catalog. This includes the external ImageBuilder, without downloading its 913 MB archive. A missing API digest fails closed; this new deployment route does not support historical Releases without asset digests. A signed URL alone does not prove the Release contains its target.

The final Pages upload/deploy uses one complete verified artifact and shares `package-repository` concurrency with other publishers. It replaces the full live site when manually dispatched; it does not merge files into the old site. Re-dispatching a previously reviewed immutable Release is an explicit rollback. Inspect the three inputs before dispatch, especially when selecting an older generation. There is no schedule or automatic deployment trigger.

Local regression verification (temporary test keys only):

```sh
USIGN=/absolute/path/to/trusted/usign python3 -m unittest discover -s tests -p test_deploy_signed_site.py -v
```

The importer accepts `--release-metadata` with `--release-tag` to validate metadata alone. Import additionally requires both before/after metadata files and all archive/checksum/output/profile/public-key/verifier/hash arguments; the workflow shows the full command. Never pass a verifier executable extracted from the Release.
