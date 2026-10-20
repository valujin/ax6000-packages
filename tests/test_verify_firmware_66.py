"""Independent 6.6 stock payload rejection fixtures; no hardware/target execution."""
import gzip,hashlib,importlib.util,io,json,lzma,os,shutil,struct,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
try:
 spec=importlib.util.spec_from_file_location('firmware66',ROOT/'scripts/verify-firmware.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
except FileNotFoundError:v=None
ABI='6.6.133~'+'a'*32+'-r1'
def make_ipk(path,name,files,version='1'):
 def tar(values):
  out=io.BytesIO()
  with tarfile.open(fileobj=out,mode='w',format=tarfile.GNU_FORMAT) as t:
   for n,b in values.items():e=tarfile.TarInfo('./'+n);e.size=len(b);t.addfile(e,io.BytesIO(b))
  return gzip.compress(out.getvalue())
 control={'Package':name,'Version':version,'Architecture':'aarch64_cortex-a53'}
 path.write_bytes(tar({'debian-binary':b'2.0\n','control.tar.gz':tar({'control':''.join(k+': '+val+'\n' for k,val in control.items()).encode()}),'data.tar.gz':tar(files)}));return control

def elf(version='6.6.133'):
 names=b'\0.shstrtab\0.modinfo\0';info=('vermagic='+version+' SMP mod_unload aarch64\0').encode();b=bytearray(448);b[:7]=b'\x7fELF\x02\x01\x01';struct.pack_into('<HH',b,16,1,183);struct.pack_into('<Q',b,40,256);struct.pack_into('<HHH',b,58,64,3,1);b[64:64+len(names)]=names;b[128:128+len(info)]=info;struct.pack_into('<IIQQQQIIQQ',b,320,1,3,0,0,64,len(names),0,0,1,0);struct.pack_into('<IIQQQQIIQQ',b,384,11,1,0,0,128,len(info),0,0,1,0);return bytes(b)

class Stock66Payload(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(v,'6.6 native verifier missing');self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_kernel_payload_must_equal_native_preinitramfs_Image(self):
  source=bytearray(128);source[56:60]=b'ARM\x64';expected=self.root/'Image';expected.write_bytes(source);compressed=self.root/'k';compressed.write_bytes(lzma.compress(bytes(source),format=lzma.FORMAT_ALONE));v.verify_kernel(compressed,'lzma',expected,self.root/'decoded');source[80]=1;compressed.write_bytes(lzma.compress(bytes(source),format=lzma.FORMAT_ALONE));self.assertRaises(ValueError,v.verify_kernel,compressed,'lzma',expected,self.root/'bad')
 def test_foreign_kernel_module_release_rejected(self):
  cfg=b'CONFIG_SMP=y\nCONFIG_MODULE_UNLOAD=y\n';self.assertEqual(v.module_info(elf(),'6.6.133',cfg)['vermagic'],'6.6.133 SMP mod_unload aarch64');self.assertRaises(ValueError,v.module_info,elf('6.12.108'),'6.6.133',cfg)
 def test_stock_DTB_geometry_is_required(self):
  from test_stock_factory_payload import board_dtb
  path=self.root/'board.dtb';path.write_bytes(board_dtb());self.assertEqual(v.verify_dtb(path,'stock')['partitions']['ubi_kernel']['size'],30*1024**2);path.write_bytes(board_dtb(wrong=True));self.assertRaisesRegex(ValueError,'geometry',v.verify_dtb,path,'stock')
 def test_native66_common_manifest_omits_size_but_authenticates_whole_image(self):
  target=self.root/'raw';source=self.root/'native';target.mkdir();source.mkdir();name='immortalwrt-mediatek-filogic-xiaomi_redmi-router-ax6000-stock-squashfs-sysupgrade.bin';image=target/name;image.write_bytes(b'actual native image');(source/name).write_bytes(image.read_bytes());digest=hashlib.sha256(image.read_bytes()).hexdigest()
  native={'target':'mediatek/filogic','arch_packages':'aarch64_cortex-a53','linux_kernel':{'version':'6.6.133','vermagic':'a'*32,'release':'1'},'version_code':'r0+33648-ec9ef10efc','profiles':{v.DEVICES['stock']:{'image_prefix':'immortalwrt-mediatek-filogic-'+v.DEVICES['stock'],'supported_devices':['xiaomi,redmi-router-ax6000-stock'],'images':[{'type':'sysupgrade','filesystem':'squashfs','name':name,'sha256':digest}]}}}
  for folder in (target,source):(folder/'profiles.json').write_text(json.dumps(native));(folder/'immortalwrt-mediatek-filogic.manifest').write_text('kernel - '+ABI+'\n')
  p={'firmware':{'commit':v.PIN,'kernel_version':ABI,'architecture':'aarch64_cortex-a53'}};self.assertEqual(v.resolve_device_manifest(target,'stock',p,image,source).name,'immortalwrt-mediatek-filogic.manifest');(source/name).write_bytes(b'foreign native image');self.assertRaisesRegex(ValueError,'native build',v.resolve_device_manifest,target,'stock',p,image,source)
 def test_actual_squashfs_root_rejects_Tail_and_missing_vendor_module(self):
  tools=Path('/home/valuxin/dev/owrt/ax6000-packages-build/work/retry1/source/staging_dir/host/bin')
  maker=Path(os.environ.get('MKSQUASHFS',shutil.which('mksquashfs') or str(tools/'mksquashfs4')))
  reader=Path(os.environ.get('UNSQUASHFS',shutil.which('unsquashfs') or str(tools/'unsquashfs4')))
  if not maker.is_file() or not reader.is_file():self.skipTest('Native SquashFS tools unavailable')
  tree=self.root/'tree';tree.mkdir();files={'etc/openwrt_release':b"DISTRIB_TARGET='mediatek/filogic'\nDISTRIB_ARCH='aarch64_cortex-a53'\nDISTRIB_REVISION='r0+33648-ec9ef10efc'\n",'etc/opkg.conf':b'option check_signature\n','etc/opkg/distfeeds.conf':b'# no moving feeds\n','bin/opkg':b'opkg fixture','usr/bin/placeholder':b'fixture'};controls={};ipks={}
  for name in sorted(v.VENDOR|v.VENDOR_USERLAND|{'kernel','opkg'}):
   payload={}
   if name in v.VENDOR:payload={'lib/modules/6.6.133/'+name+'.ko':elf()}
   if name=='kmod-mt_wifi':payload['lib/firmware/mt7986.bin']=b'native vendor firmware'
   path=self.root/(name+'.ipk');controls[name]=make_ipk(path,name,payload,ABI if name=='kernel' else '1');ipks[name]=path;files.update(payload)
  files['usr/lib/opkg/status']=b'\n'.join((''.join(k+': '+val+'\n' for k,val in c.items())+'Status: install ok installed\n').encode() for c in controls.values())
  for name,data in files.items():path=tree/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  (tree/'bin/opkg').chmod(0o755)
  profile={'firmware':{'kernel_version':ABI,'architecture':'aarch64_cortex-a53','commit':v.PIN},'package_only':['tailscale'],'packages':['tailscale'],'image_packages':[]}
  def inspect(label):
   image=self.root/(label+'.squashfs');subprocess.run([str(maker),str(tree),str(image),'-noappend','-comp','xz','-processors','1','-b','262144'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   return v.inspect_root(image,'stock',reader,profile,controls,ipks,{},'key',b'CONFIG_SMP=y\nCONFIG_MODULE_UNLOAD=y\n',{n:c['Version'] for n,c in controls.items()})
  self.assertEqual(len(inspect('valid')['modules']),4)
  tail=tree/'usr/sbin/tailscaled';tail.parent.mkdir(parents=True);tail.write_bytes(b'leftover executable');self.assertRaisesRegex(ValueError,'package.only|Package.only|tailscale',inspect,'tail');tail.unlink()
  (tree/'lib/modules/6.6.133/kmod-mt_wifi.ko').unlink();self.assertRaises(ValueError,inspect,'missing-vendor')
 def test_wrong_supported_device_and_revision_rejected(self):
  p={'firmware':{'commit':'ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8'}};m={'metadata_version':'1.1','supported_devices':['xiaomi,redmi-router-ax6000-stock'],'version':{'target':'mediatek/filogic','board':'xiaomi_redmi-router-ax6000-stock','revision':'r0+33648-ec9ef10efc'}};v.verify_metadata(m,'stock',p);m['supported_devices']=['xiaomi,redmi-router-ax6000-ubootmod'];self.assertRaises(ValueError,v.verify_metadata,m,'stock',p)
 def test_baseline_bank_must_cover_native_packages_and_preserve_bytes(self):
  p={'firmware':{'architecture':'aarch64_cortex-a53','commit':'c'*40,'kernel_version':ABI},'packages':['tailscale','kmod-amneziawg']};paths={};controls={}
  for name in ['kernel','libc','kmod-wireguard','kmod-amneziawg','tailscale','vendor-ui','base-files','busybox','opkg']:
   f=self.root/(name+'.ipk');controls[name]=make_ipk(f,name,{},ABI if name=='kernel' else '1');paths[name]=f
  frozen={'kernel','libc','kmod-wireguard','vendor-ui'};records=[{'package':n,'path':'ipks/'+n+'.ipk','sha256':hashlib.sha256(paths[n].read_bytes()).hexdigest(),'version':controls[n]['Version']} for n in sorted(frozen)]
  snapshot={'manifest':{'schema_version':1,'architecture':p['firmware']['architecture'],'source_commit':'c'*40,'kernel_version':ABI,'files':records,'frozen_packages':sorted(frozen),'frozen_kmods':['kmod-wireguard'],'refreshed_kmods':['kmod-amneziawg']},'ipks':{'ipks/'+n+'.ipk':{'sha256':hashlib.sha256(paths[n].read_bytes()).hexdigest(),'size':paths[n].stat().st_size} for n in frozen}}
  self.assertEqual(v.verify_sdk_baseline(snapshot,controls,paths,p)['frozen_kmods'],1);snapshot['ipks']['ipks/vendor-ui.ipk']['sha256']='0'*64;self.assertRaises(ValueError,v.verify_sdk_baseline,snapshot,controls,paths,p)
 def test_actual_source_and_sdk_context_mismatch_is_rejected(self):
  source=self.root/'source';k=source/'build_dir/target-fixture/linux-mediatek_filogic/linux-6.6.133';k.mkdir(parents=True);parts={}
  for name in v.CONTEXTS:
   data=b'context '+name.encode();path=k/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);parts[(k.relative_to(source)/name).as_posix()]=data
  parts[next(iter(parts))]=b'foreign-sdk-context';parts['Config-build.in']=b'config TARGET_BOARD\n string\n default "mediatek"\n';parts['include/version.mk']=b'REVISION:=r0+33648-ec9ef10efc\n';parts['ax6000-baseline/manifest.json']=b'{}';archive=self.root/'sdk.tar'
  with tarfile.open(archive,'w') as t:
   for n,b in parts.items():i=tarfile.TarInfo('sdk/'+n);i.size=len(b);t.addfile(i,io.BytesIO(b))
  self.assertRaisesRegex(ValueError,'context',v.inspect_sdk,archive,k,source,self.root,{'firmware':{'commit':'ec9ef10efc65da1e6d1de4e2c043c0e13d08eed8','kernel_version':ABI}})
if __name__=='__main__':unittest.main()
