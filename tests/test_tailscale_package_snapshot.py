"""Finite 6.6 Tail r2 -> r3 snapshot transport; signed fixtures only, no network."""
import copy
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import shutil
import tarfile
import unittest
import test_deploy_signed_site as fixtures
metadata,sha,site,USIGN=fixtures.metadata,fixtures.sha,fixtures.site,fixtures.USIGN


def ipk(version,package='tailscale',provides='tailscaled'):
    control=(f'Package: {package}\nVersion: {version}\nArchitecture: aarch64_cortex-a53\n'
             f'Depends: libc, ca-bundle, kmod-tun\nProvides: {provides}\nDescription: signed fixture\n').encode()
    inner=io.BytesIO()
    with tarfile.open(fileobj=inner,mode='w:gz') as archive:
        member=tarfile.TarInfo('./control');member.size=len(control);archive.addfile(member,io.BytesIO(control))
    outer=io.BytesIO()
    with tarfile.open(fileobj=outer,mode='w:gz') as archive:
        member=tarfile.TarInfo('./control.tar.gz');member.size=len(inner.getvalue());archive.addfile(member,io.BytesIO(inner.getvalue()))
    return outer.getvalue()


class PackageTransportTests(unittest.TestCase):
    def test_package_transport_requires_explicit_exact_snapshot_pin(self):
        digest='d'*64;tag='packages-66-'+digest;release=metadata(tag)
        with self.assertRaises(ValueError):site.release_identity(release,tag)
        try: accepted=site.release_identity(release,tag,package_snapshot_sha=digest)
        except (TypeError,ValueError) as error:self.fail('Missing explicit immutable package transport: '+str(error))
        self.assertEqual(accepted[0],42)
        for pin in ('e'*64,None):
            with self.subTest(pin=pin),self.assertRaises(ValueError):site.release_identity(release,tag,package_snapshot_sha=pin)


@unittest.skipUnless(USIGN and shutil.which('zstd'),'Pinned usign and zstd required')
class TailscaleSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SignedImportTests('test_valid_complete_site_preserves_bytes_and_external_imagebuilder');self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        self.old='tailscale_1.104.2-r2_aarch64_cortex-a53.ipk';self.new='tailscale_1.104.2-r3_aarch64_cortex-a53.ipk'
        (f.feed/self.old).write_bytes(ipk('1.104.2-r2'));self.index(self.old,'1.104.2-r2');f.snapshot()
        self.before=f.root/'before-manifest.json';self.sig=f.root/'before-manifest.sig'
        self.before.write_bytes((f.public/'snapshot-manifest.json').read_bytes());self.sig.write_bytes((f.public/'snapshot-manifest.sig').read_bytes())
        self.old_index=f.root/'before-Packages';self.old_index.write_bytes((f.feed/'Packages').read_bytes())
        self.pin=sha(self.before.read_bytes());release=metadata(f.tag)
        for n,entry in enumerate(json.loads((f.public/'downloads.json').read_text())['assets'],100):
            release['assets'].append({'id':n,'name':entry['filename'],'size':entry['size'],'state':'uploaded','updated_at':'fixed','browser_download_url':entry['url'],'digest':'sha256:'+entry['sha256']})
        self.context={'predecessor_index':self.old_index,'predecessor_manifest':self.before,'predecessor_signature':self.sig,'predecessor_sha256':self.pin,'firmware_tag':f.tag,'firmware_before':release,'firmware_after':copy.deepcopy(release)}
        (f.feed/self.old).unlink();(f.feed/self.new).write_bytes(ipk('1.104.2-r3'));self.index(self.new,'1.104.2-r3');f.snapshot()
        self.tag='packages-66-'+f.snapshot_sha

    def index(self,name,version):
        f=self.f;old=(f.feed/'Packages').read_text().strip().split('\n\n');kept=[p for p in old if not p.startswith('Package: tailscale\n')]
        p=f.feed/name;kept.append(f'Package: tailscale\nVersion: {version}\nArchitecture: aarch64_cortex-a53\nProvides: tailscaled\nFilename: {name}\nSize: {p.stat().st_size}\nSHA256sum: {sha(p.read_bytes())}')
        data=('\n\n'.join(kept)+'\n\n').encode();(f.feed/'Packages').write_bytes(data);(f.feed/'Packages.gz').write_bytes(gzip.compress(data,mtime=0));f.sign(f.feed/'Packages',f.feed/'Packages.sig')

    def imported(self,context=None):
        f=self.f;archive,checksum,digest=f.bundle()
        return site.import_site(archive,checksum,f.root/'result',f.expected,f.trusted,USIGN,digest,f.snapshot_sha,'packages-66-'+f.snapshot_sha,package_update=self.context if context is None else context)

    def test_separate_package_release_keeps_original_signed_firmware_catalog(self):
        try: result=self.imported()
        except (TypeError,ValueError) as error:self.fail('Missing authenticated Tail-only import: '+str(error))
        self.assertEqual(result['build_id'],self.f.profile['build_id'])
        for name in ('profile.json','state/lock.json','downloads.json','downloads.sig'):
            self.assertEqual((self.f.public/name).read_bytes(),(self.f.root/'result'/name).read_bytes())
        self.assertTrue((self.f.root/'result'/self.f.feed.relative_to(self.f.public)/self.new).is_file())

    def test_unsigned_or_wrong_pinned_predecessor_refused(self):
        for what in ('sha','sig'):
            context=dict(self.context)
            if what=='sha':context['predecessor_sha256']='f'*64
            else:bad=self.f.root/'invalid.sig';bad.write_text('not signed');context['predecessor_signature']=bad
            with self.subTest(what=what),self.assertRaises((ValueError,subprocess.CalledProcessError)):
                self.imported(context)
            self.assertFalse((self.f.root/'result').exists())

    def test_unrelated_profile_or_payload_changes_refused(self):
        for name in ('profile.json','index.html'):
            p=self.f.public/name;original=p.read_bytes();p.write_bytes(original+b'\n');self.f.snapshot()
            with self.subTest(name=name),self.assertRaises(ValueError):self.imported()
            self.assertFalse((self.f.root/'result').exists());p.write_bytes(original);self.f.snapshot()

    def test_foreign_mutable_or_changed_original_firmware_assets_refused(self):
        for what in ('mutable','foreign','asset'):
            context=copy.deepcopy(self.context)
            if what=='mutable':context['firmware_after']['immutable']=False
            elif what=='foreign':context['firmware_after']['url']=context['firmware_after']['url'].replace('valujin','foreign')
            else:context['firmware_after']['assets'][-1]['digest']='sha256:'+'f'*64
            with self.subTest(what=what),self.assertRaises(ValueError):self.imported(context)
            self.assertFalse((self.f.root/'result').exists())

    def test_context_is_required_and_wrong_transport_pin_refused(self):
        f=self.f;archive,checksum,digest=f.bundle()
        for tag,context in ((self.tag,None),('packages-66-'+'e'*64,self.context),(f.tag,self.context)):
            with self.subTest(tag=tag),self.assertRaises(ValueError):
                site.import_site(archive,checksum,f.root/'result',f.expected,f.trusted,USIGN,digest,f.snapshot_sha,tag,package_update=context)


    def test_non_tail_index_metadata_cannot_change_even_with_same_archives(self):
        path=self.f.feed/'Packages';data=path.read_text().replace('Version: 1.0-r1','Version: 9.9-r9')
        path.write_text(data);(self.f.feed/'Packages.gz').write_bytes(gzip.compress(data.encode(),mtime=0))
        self.f.sign(path,self.f.feed/'Packages.sig');self.f.snapshot()
        with self.assertRaisesRegex(ValueError,'non-Tail index'):self.imported()

    def test_replacement_actual_control_cannot_claim_a_core_provider(self):
        (self.f.feed/self.new).write_bytes(ipk('1.104.2-r3',provides='libc'))
        self.index(self.new,'1.104.2-r3');self.f.snapshot()
        with self.assertRaisesRegex(ValueError,'control/provider'):self.imported()

    def test_predecessor_index_must_match_signed_inventory(self):
        self.old_index.write_bytes(self.old_index.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'predecessor index'):self.imported()


    def test_cli_routes_transport_original_firmware_and_all_predecessor_evidence(self):
        f=self.f;archive,checksum,digest=f.bundle();transport=metadata('packages-66-'+f.snapshot_sha)
        for asset,path in zip(transport['assets'],(archive,checksum)):
            asset['size']=path.stat().st_size;asset['digest']='sha256:'+sha(path.read_bytes())
        paths={}
        for name,data in [('transport-before',transport),('transport-after',transport),('firmware-before',self.context['firmware_before']),('firmware-after',self.context['firmware_after'])]:
            paths[name]=f.root/(name+'.json');paths[name].write_text(json.dumps(data))
        argv=[sys.executable,str(Path(site.__file__).resolve()),
              '--release-tag',transport['tag_name'],'--release-metadata',str(paths['transport-after']),
              '--previous-release-metadata',str(paths['transport-before']),'--archive',str(archive),
              '--checksum',str(checksum),'--output',str(f.root/'result'),'--expected-profile',str(f.expected),
              '--public-key',str(f.trusted),'--usign',USIGN,'--expected-archive-sha256',digest,
              '--expected-snapshot-sha256',f.snapshot_sha,'--expected-predecessor-sha256',self.pin,
              '--package-predecessor',str(self.before),'--package-predecessor-signature',str(self.sig),
              '--package-predecessor-index',str(self.old_index),'--firmware-release-tag',f.tag,
              '--previous-firmware-release-metadata',str(paths['firmware-before']),
              '--firmware-release-metadata',str(paths['firmware-after'])]
        missing=list(argv);i=missing.index('--package-predecessor-index');del missing[i:i+2]
        rejected=subprocess.run(missing,capture_output=True,text=True,timeout=20)
        self.assertNotEqual(rejected.returncode,0);self.assertIn('requires signed predecessor',rejected.stderr)
        accepted=subprocess.run(argv,capture_output=True,text=True,timeout=20)
        self.assertEqual(accepted.returncode,0,accepted.stderr)
        self.assertEqual((f.root/'result/profile.json').read_bytes(),(f.public/'profile.json').read_bytes())


class PackageWorkflowTests(unittest.TestCase):
    def test_real_shell_blocks_parse_and_bind_predecessor_and_original_firmware(self):
        root=Path(site.__file__).resolve().parents[1]
        source=(root/'.github/workflows/deploy-signed-site.yml').read_text()
        self.assertIn('group: package-repository\n  cancel-in-progress: false',source)
        self.assertIn('--package-predecessor-index "$RUNNER_TEMP/predecessor-Packages"',source)
        self.assertIn('--previous-firmware-release-metadata "$RUNNER_TEMP/firmware-before.json"',source)
        self.assertLess(source.index('Refuse a stale package-only predecessor'),source.index('id: deployment'))
        self.assertGreater(source.index('Verify package-only deployed snapshot'),source.index('id: deployment'))
        lines=source.splitlines();blocks=[]
        for i,line in enumerate(lines):
            if line=='        run: |':
                body=[]
                for following in lines[i+1:]:
                    if following and not following.startswith('          '):break
                    body.append(following[10:] if following else '')
                blocks.append('\n'.join(body)+'\n')
        self.assertGreaterEqual(len(blocks),5)
        for block in blocks:
            process=subprocess.run(['bash','-n'],input=block,text=True,capture_output=True)
            self.assertEqual(process.returncode,0,process.stderr)


if __name__=='__main__':unittest.main()
