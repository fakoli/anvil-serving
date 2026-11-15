"""Build a bounded, link-free native owner runtime from pinned Pi and Node inputs.

This writes a fresh build directory only. It does not install, activate, mount,
change ownership, copy a home directory, or include host credential/config stores.
The managed installer must independently qualify and root-own the resulting tree.
"""
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from .sandbox import BOOTSTRAP_JS,_root_custody,_SHA

MAX_BYTES=4*1024**3
MAX_FILES=100000
TOOLS=("/bin/sh","/bin/bash","/usr/bin/env","/usr/bin/git","/usr/bin/rg","/usr/bin/find","/usr/bin/sed","/usr/bin/grep","/usr/bin/cat","/usr/bin/ls","/usr/bin/mkdir","/usr/bin/rm","/usr/bin/cp","/usr/bin/mv","/usr/bin/head","/usr/bin/tail","/usr/bin/wc","/usr/bin/sort","/usr/bin/cut","/usr/bin/printf","/usr/bin/date","/usr/bin/true","/usr/bin/false","/usr/bin/timeout","/usr/bin/stat","/usr/bin/readlink","/usr/bin/id","/usr/bin/whoami","/usr/bin/ps","/usr/bin/python3")
# The qualified Debian/Ubuntu runtime is glibc. Npm includes these unused musl
# alternatives even when their glibc siblings are selected; do not ship binaries
# whose interpreter/dependency closure cannot execute in this rootfs.
EXCLUDED_PACKAGES=frozenset({'@img/sharp-libvips-linuxmusl-x64','@img/sharp-linuxmusl-x64','@next/swc-linux-x64-musl','@unrs/resolver-binding-linux-x64-musl','@tailwindcss/oxide-linux-x64-musl','lightningcss-linux-x64-musl'})
EXCLUDED_SUBTREES=('node_modules/node-pty/prebuilds/linux-arm64','node_modules/@earendil-works/pi-tui/native/linux/prebuilds/linux-arm64')


def digest_file(filename):
    digest=hashlib.sha256()
    with Path(filename).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value):
    if not isinstance(value,str) or not value or len(value)>4096 or any(c in value for c in '\0\r\n\\'):
        raise ValueError('invalid archive path')
    p=PurePosixPath(value)
    if p.is_absolute() or str(p)!=value or '..' in p.parts or value=='.':raise ValueError('invalid archive path')
    return p


def managed_file(filename, expected=None, single_link=True):
    p=Path(filename)
    if not p.is_absolute() or p.resolve(strict=True)!=p:raise ValueError('canonical source required')
    _root_custody(p.parent);info=p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or (single_link and info.st_nlink!=1) or not 0<=info.st_size<=MAX_BYTES:raise ValueError('source custody mismatch')
    if expected is not None and (not isinstance(expected,str) or not _SHA.fullmatch(expected) or digest_file(p)!=expected):raise ValueError('pinned source mismatch')
    return p


class Builder:
    def __init__(self,root):
        self.root=Path(root)
        if not self.root.is_absolute() or self.root.parent.resolve(strict=True)!=self.root.parent:raise ValueError('canonical output required')
        self.root.mkdir(mode=0o700)
        self.files={};self.total=0;self.elf=[];self.sources={}

    def add(self,relative,stream,size,executable=False,source=None):
        relative=str(safe_relative(relative))
        if relative in self.files:raise ValueError('duplicate output file')
        if len(self.files)>=MAX_FILES or not 0<=size<=MAX_BYTES-self.total:raise ValueError('runtime closure bound exceeded')
        target=self.root/relative;target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
        digest=hashlib.sha256();written=0;header=b''
        with target.open('xb') as out:
            while chunk:=stream.read(min(1024*1024,size-written+1)):
                written+=len(chunk)
                if written>size:raise ValueError('source exceeds declared size')
                if not header:header=chunk[:4]
                digest.update(chunk);out.write(chunk)
        if written!=size:raise ValueError('truncated source')
        target.chmod(0o755 if executable else 0o644)
        self.total+=written;self.files[relative]=digest.hexdigest()
        if header==b'\x7fELF':self.elf.append(relative)
        if source:self.sources[relative]=Path(source)

    def text(self,relative,value):
        import io
        raw=value.encode();self.add(relative,io.BytesIO(raw),len(raw))

    def copy(self,source,relative=None):
        # Host runtime inputs may use distro aliases, but only their resolved
        # root-owned regular inode is read; output has no links or shared inode.
        # Distro multi-call binaries can have root-owned hardlink aliases. Their
        # inode is never adopted: each output is a separate regular-file copy.
        original=Path(source);canonical=managed_file(original.resolve(strict=True),single_link=False)
        relative=relative or original.as_posix().lstrip('/')
        if relative in self.files:return
        with canonical.open('rb') as stream:self.add(relative,stream,canonical.stat().st_size,bool(canonical.stat().st_mode&0o111),canonical)

    def archive(self,archive):
        seen=set();entries=0
        with tarfile.open(archive,'r:gz') as source:
            for member in source:
                entries+=1
                if entries>100000:raise ValueError('Pi archive entry limit exceeded')
                relative=str(safe_relative(member.name))
                if relative in seen:raise ValueError('duplicate Pi archive entry')
                seen.add(relative)
                if member.mode&0o6000 or not (member.isdir() or member.isfile()):raise ValueError('unsafe Pi archive entry')
                if any(part=='.git' or part=='.env' or part.startswith('.env.') for part in PurePosixPath(relative).parts):raise ValueError('unexpected private input in Pi artifact')
                parts=PurePosixPath(relative).parts
                package='/'.join(parts[1:3]) if len(parts)>=3 and parts[1].startswith('@') else parts[1] if len(parts)>=2 else ''
                if parts[0]=='node_modules' and package in EXCLUDED_PACKAGES:continue
                if any(relative==prefix or relative.startswith(prefix+'/') for prefix in EXCLUDED_SUBTREES):continue
                if member.isdir():(self.root/'app'/relative).mkdir(parents=True,exist_ok=True,mode=0o755)
                else:
                    with source.extractfile(member) as stream:self.add('app/'+relative,stream,member.size,bool(member.mode&0o111))
        for required in ('app/.next/BUILD_ID','app/package.json','app/.anvil-native-agents.json','app/node_modules/next/package.json'):
            if required not in self.files:raise ValueError('incomplete built Pi artifact')

    def system_tree(self,source,python_stdlib=False):
        root=Path(source);_root_custody(root.resolve(strict=True))
        for entry in sorted(root.rglob('*')):
            if python_stdlib and any(part in ('sitecustomize.py','usercustomize.py','__pycache__') or part.startswith('config-') for part in entry.relative_to(root).parts):continue
            if entry.is_dir():continue
            if entry.is_symlink() and root.resolve() not in entry.resolve(strict=True).parents:raise ValueError('runtime subtree link escape')
            self.copy(entry)

    def dependencies(self):
        """Read ELF metadata without executing any bundled binary or addon."""
        readelf=managed_file(Path('/usr/bin/readelf').resolve(strict=True))
        ldconfig=managed_file(Path('/usr/sbin/ldconfig').resolve(strict=True))
        result=subprocess.run([str(ldconfig),'-p'],capture_output=True,text=True,check=True,timeout=15,env={'LC_ALL':'C'})
        libraries={}
        for line in result.stdout.splitlines():
            match=re.fullmatch(r'\s*(\S+)\s+\([^)]*\)\s+=>\s+(/\S+)\s*',line)
            if match:libraries.setdefault(match[1],[]).append(Path(match[2]))
        checked=set();index=0
        while index<len(self.elf):
            relative=self.elf[index];index+=1
            if relative in checked:continue
            checked.add(relative);filename=self.root/relative
            result=subprocess.run([str(readelf),'-l','-d',str(filename)],capture_output=True,text=True,check=True,timeout=15,env={'LC_ALL':'C'})
            interpreter=re.search(r'\[Requesting program interpreter: (/[^\]]+)\]',result.stdout)
            if interpreter:self.copy(interpreter[1])
            search=[]
            for paths in re.findall(r'\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]',result.stdout):
                for part in paths.split(':'):
                    expanded=part.replace('${ORIGIN}',str(filename.parent)).replace('$ORIGIN',str(filename.parent))
                    if '$' in expanded:continue
                    candidate=Path(expanded)
                    if not candidate.is_absolute():continue
                    resolved=candidate.resolve()
                    # Only bundled siblings or canonical host library locations.
                    if self.root in resolved.parents:search.append(resolved)
                    elif any(str(resolved).startswith(p) for p in ('/usr/lib/','/lib/','/lib64/')):search.append(resolved)
            for needed in re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',result.stdout):
                if '/' in needed or not needed:raise ValueError('invalid ELF dependency')
                bundled=next((p/needed for p in search if self.root in p.parents and (p/needed).is_file()),None)
                if bundled:
                    if bundled.relative_to(self.root).as_posix() not in self.files:raise ValueError('unmanifested bundled library')
                    continue
                candidates=[p/needed for p in search if self.root not in p.parents]+libraries.get(needed,[])
                candidate=next((p for p in candidates if p.is_file()),None)
                if candidate is None:raise ValueError('unresolved ELF dependency in '+relative+': '+needed)
                self.copy(candidate)

    def finish(self):
        value={'schema':'anvil-native-owner-rootfs/v1','files':dict(sorted(self.files.items()))}
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        if len(raw)>32*1024*1024:raise ValueError('rootfs manifest too large')
        target=self.root/'rootfs.manifest.json';target.write_bytes(raw);target.chmod(0o644)
        for directory in self.root.rglob('*'):
            if directory.is_dir():directory.chmod(0o755)
        self.root.chmod(0o755)
        return {'schema':value['schema'],'rootfs_sha256':hashlib.sha256(raw).hexdigest(),'files':len(self.files),'bytes':self.total}


def build(pi_archive,pi_sha,node,node_sha,output):
    import platform
    if sys.platform!='linux' or platform.machine()!='x86_64':raise ValueError('Linux x86_64 runtime builder required')
    archive=managed_file(pi_archive,pi_sha);node=managed_file(node,node_sha)
    builder=Builder(output);builder.archive(archive);builder.copy(node,'runtime/node/bin/node')
    builder.text('app/native-owner-bootstrap.cjs',BOOTSTRAP_JS)
    for binary in TOOLS:builder.copy(binary)
    stdlib=subprocess.run(['/usr/bin/python3','-c','import sysconfig;print(sysconfig.get_path("stdlib"))'],capture_output=True,text=True,check=True,timeout=10,env={}).stdout.strip()
    if not re.fullmatch(r'/usr/lib/python3\.\d+',stdlib):raise ValueError('unsupported Python standard library layout')
    builder.system_tree(stdlib,python_stdlib=True);builder.system_tree('/usr/lib/git-core');builder.dependencies()
    builder.text('etc/passwd','root:x:0:0:root:/nonexistent:/bin/false\n')
    builder.text('etc/group','root:x:0:\n')
    builder.text('etc/nsswitch.conf','passwd: files\ngroup: files\nhosts: files\n')
    builder.text('etc/hosts','127.0.0.1 localhost\n::1 localhost\n')
    builder.text('etc/resolv.conf','')
    builder.text('etc/anvil-native-runtime.json',json.dumps({'pi_archive_sha256':pi_sha,'node_sha256':node_sha,'tools':TOOLS,'excluded_alternate_libc_packages':sorted(EXCLUDED_PACKAGES),'excluded_foreign_arch_subtrees':EXCLUDED_SUBTREES,'network':'model-gateway-only'},sort_keys=True))
    for name in ('proc','dev','run','tmp','workspace','state','home/agent'):(builder.root/name).mkdir(parents=True,exist_ok=True,mode=0o755)
    return builder.finish()


def main():
    import argparse
    parser=argparse.ArgumentParser()
    for name in ('pi-archive','pi-sha256','node','node-sha256','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();result=build(args.pi_archive,args.pi_sha256,args.node,args.node_sha256,args.output)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
