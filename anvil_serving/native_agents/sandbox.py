"""Pure whole-owner confinement plan. Rendering is not permission to activate.

The supervisor must qualify the immutable rootfs and aggregate storage quota,
and install the fixed ingress/model relays. Bubblewrap creates the private network.
Rendering never launches processes or upgrades a workspace's isolation label.
The foreground launcher verifies managed custody before exec; runtime evidence
remains a separate managed acceptance gate.
"""
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re

_ID = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def path(value):
    if not isinstance(value,str) or not value.startswith("/") or value.startswith("//") or len(value)>4096 or any(c in value for c in "\0\r\n"):
        raise ValueError("absolute canonical sandbox path required")
    p=PurePosixPath(value)
    if str(p)!=value or ".." in p.parts or value=="/": raise ValueError("canonical sandbox path required")
    return p


@dataclass(frozen=True)
class SandboxPlan:
    id: str
    uid: int
    owner_port: int
    model_port: int
    argv: tuple[str,...]
    credential_fds: tuple[tuple[int,str],...]
    environment: tuple[tuple[str,str],...]
    supervisor_requirements: tuple[str,...]
    rootfs_sha256: str
    storage_limit_bytes: int
    bwrap_sha256: str

    def public(self):
        # Mobile/user-visible evidence contains no operator paths or argv.
        return {"schema":"anvil-native-owner-sandbox-plan/v1","id":self.id,"uid":self.uid,
                "owner_port":self.owner_port,"model_port":self.model_port,
                "controls":["private-filesystem","private-network","private-processes","bounded-storage","bounded-resources"],
                "rootfs_sha256":self.rootfs_sha256,"bwrap_sha256":self.bwrap_sha256,"storage_limit_bytes":self.storage_limit_bytes,
                "activation":"requires-live-qualification"}


def render(spec):
    """Render only fixed mounts/entrypoint, never arbitrary argv or inherited env.

    Credential descriptors3/4 must be opened by the managed supervisor using
    systemd credentials; bwrap copies them privately, so secrets never enter argv.
    rootfs must contain /runtime/node/bin/node, /app, the bootstrap module below,
    the selected toolchain, basic system libraries, and no host/user credentials.
    """
    keys={"id","deployment_id","uid","rootfs","rootfs_sha256","bwrap_sha256","directories","owner_port","model_port","storage_limit_bytes"}
    if not isinstance(spec,dict) or set(spec)!=keys: raise ValueError("invalid sandbox declaration")
    if not isinstance(spec["id"],str) or not _ID.fullmatch(spec["id"]): raise ValueError("invalid sandbox id")
    if type(spec["uid"]) is not int or not 60000<=spec["uid"]<=64999: raise ValueError("dedicated owner uid required")
    if not isinstance(spec["rootfs_sha256"],str) or not _SHA.fullmatch(spec["rootfs_sha256"]): raise ValueError("pinned rootfs required")
    if not isinstance(spec["bwrap_sha256"],str) or not _SHA.fullmatch(spec["bwrap_sha256"]): raise ValueError("pinned bubblewrap required")
    if type(spec["storage_limit_bytes"]) is not int or not 16*1024*1024<=spec["storage_limit_bytes"]<=64*1024**3: raise ValueError("bounded aggregate storage required")
    for key in ("owner_port","model_port"):
        if type(spec[key]) is not int or not 1024<=spec[key]<=65535: raise ValueError("invalid private port")
    if spec["owner_port"]==spec["model_port"]: raise ValueError("distinct private ports required")
    if not isinstance(spec["deployment_id"],str) or not _ID.fullmatch(spec["deployment_id"]): raise ValueError("invalid deployment id")
    rootfs=path(spec["rootfs"])
    expected_rootfs=PurePosixPath("/opt/anvil-native-agents")/spec["deployment_id"]/("rootfs-"+spec["rootfs_sha256"])
    if rootfs!=expected_rootfs: raise ValueError("rootfs must be the dedicated pinned artifact")
    owner_root=PurePosixPath("/var/lib/anvil-native-agents")/spec["deployment_id"]/"owners"/spec["id"]
    gateway_socket=str(PurePosixPath("/run/anvil-native-agents")/spec["deployment_id"]/spec["id"]/"model.sock")
    roots=[rootfs]
    directories=spec["directories"]
    if not isinstance(directories,list) or not 1<=len(directories)<=128: raise ValueError("explicit directories required")
    mounts=[]; ids=set()
    for directory in directories:
        if not isinstance(directory,dict) or set(directory)!={"id"} or not isinstance(directory["id"],str) or not _ID.fullmatch(directory["id"]): raise ValueError("invalid directory")
        if directory["id"] in ids: raise ValueError("duplicate directory")
        ids.add(directory["id"]); source=owner_root/"workspaces"/directory["id"]; roots.append(source)
        mounts.append((str(source),"/workspace/"+directory["id"]))
    # No rootfs overlay or overlapping writable mounts can conceal qualification.
    for i,a in enumerate(roots):
        for b in roots[i+1:]:
            if a==b or a in b.parents or b in a.parents: raise ValueError("sandbox mount sources must not overlap")
    env={"HOME":"/home/agent","PATH":"/runtime/node/bin:/usr/bin:/bin","LANG":"C.UTF-8",
         "XDG_CONFIG_HOME":"/state/config","XDG_CACHE_HOME":"/state/cache","XDG_DATA_HOME":"/state/data",
         "TMPDIR":"/tmp","PI_CODING_AGENT_DIR":"/state/agent","PI_CODING_AGENT_SESSION_DIR":"/state/sessions",
         "PI_WEB_NATIVE_AGENTS_DIRECTORIES":json.dumps([target for _,target in mounts],separators=(",",":")),
         "PI_WEB_ALLOWED_HOSTS":"127.0.0.1,localhost","PI_WEB_HOSTNAME":"127.0.0.1",
         "PI_WEB_NO_OPEN":"1","PI_WEB_SKIP_VERSION_CHECK":"1","JITI_FS_CACHE":"false","NODE_ENV":"production",
         "ANVIL_OWNER_PORT":str(spec["owner_port"]),"ANVIL_MODEL_BASE_URL":f"http://127.0.0.1:{spec['model_port']}/v1"}
    # Every namespace is mandatory. The sole network path is the mounted owner-scoped model socket.
    argv=["/usr/bin/bwrap","--die-with-parent","--new-session","--unshare-user","--disable-userns",
          "--unshare-pid","--unshare-ipc","--unshare-uts","--unshare-cgroup","--unshare-net","--cap-drop","ALL",
          "--hostname",spec["id"],"--clearenv","--ro-bind",spec["rootfs"],"/",
          "--proc","/proc","--dev","/dev","--tmpfs","/tmp","--tmpfs","/run","--tmpfs","/workspace","--tmpfs","/state",
          "--bind",str(owner_root/"home"),"/home/agent",
          "--bind",str(owner_root/"channels"/"ingress"),"/run/anvil/ingress",
          "--ro-bind",gateway_socket,"/run/anvil/model.sock"]
    for name in ("agent","sessions","config","cache","data"): argv.extend(["--bind",str(owner_root/name),"/state/"+name])
    for source,target in mounts: argv.extend(["--bind",source,target])
    argv.extend(["--perms","0700","--dir","/run/credentials","--perms","0400","--ro-bind-data","3","/run/credentials/owner-token",
                 "--perms","0400","--ro-bind-data","4","/run/credentials/web-password"])
    for name,value in sorted(env.items()): argv.extend(["--setenv",name,value])
    argv.extend(["--chdir",mounts[0][1],"--","/runtime/node/bin/node","/app/native-owner-bootstrap.cjs"])
    requirements=(
        "Mandatory bwrap --unshare-net; prove owner namespace differs from host and has no non-loopback routes",
        f"User={spec['uid']} Group={spec['uid']}; empty supplementary groups; NoNewPrivileges=yes; reject all unmanaged unit drop-ins",
        "Launcher closes every inherited descriptor except stdio and explicitly supplied credential FDs3/4; stdin=/dev/null; live sentinel FD/socket absence test required",
        "Root-owned read-only rootfs from pinned manifest; verify canonical mount custody and no symlink/hardlink adoption",
        "Pinned root-owned bubblewrap 0.11.1 executable; every writable mount leaf has root-owned nonwritable ancestors, preventing owner replacement before bind",
        "Whole-owner cgroup: MemoryMax=4G, MemorySwapMax=0, TasksMax=256, CPUQuota=200%, KillMode=control-group",
        "UID and filesystem UUID must be unique per active owner; managed receipt binds identity and volume across restart",
        "Verified dedicated bounded filesystem covers every writable home/agent/sessions/config/cache/data/project/channels mount; private tmpfs charged to owner memory cgroup",
        "Host ingress relay binds only the declared loopback owner_port and forwards only this owner's ingress UNIX socket",
        "In-sandbox model relay binds private loopback model_port and forwards only the exact mounted UNIX gateway; host gateway checks SO_PEERCRED owner uid",
        "Model gateway uses fixed allowed inference paths/models, bounded requests/streams, owner-scoped credential; deny CONNECT, redirects, admin routes and arbitrary upstream URLs",
        "Two-owner live qualification: filesystem/config/credential/process/namespace/socket/network/symlink/child/extension/resource escape attempts denied",
    )
    if sum(len(arg)+1 for arg in argv)>65536: raise ValueError("sandbox argument limit exceeded")
    return SandboxPlan(spec["id"],spec["uid"],spec["owner_port"],spec["model_port"],tuple(argv),((3,"owner-token"),(4,"web-password")),tuple(sorted(env.items())),requirements,spec["rootfs_sha256"],spec["storage_limit_bytes"],spec["bwrap_sha256"])

# Added to the immutable rootfs by its builder; it is intentionally not a runtime
# download or user-editable script. Only the credential files contain secrets.
BOOTSTRAP_JS = r'''"use strict";
const fs = require("node:fs");
const http = require("node:http");
function credential(name) {
  const path="/run/credentials/"+name;
  const info=fs.lstatSync(path);
  if(!info.isFile() || info.size<1 || info.size>4096) throw new Error("credential unavailable");
  const value=fs.readFileSync(path,"utf8").trim();
  if(!/^[!-~]+$/.test(value)) throw new Error("credential unavailable");
  return value;
}
process.env.PI_WEB_NATIVE_AGENTS_TOKEN=credential("owner-token");
process.env.PI_WEB_PASSWORD=credential("web-password");
if(process.env.PI_WEB_NATIVE_AGENTS_TOKEN===process.env.PI_WEB_PASSWORD) throw new Error("distinct credentials required");
const model=new URL(process.env.ANVIL_MODEL_BASE_URL);
if(model.protocol!=="http:" || model.hostname!=="127.0.0.1" || model.pathname!=="/v1" || !model.port) throw new Error("invalid private model endpoint");
const relay=http.createServer((request,response)=>{
  const headers={};
  for(const name of ["content-type","content-length"]) if(request.headers[name]) headers[name]=request.headers[name];
  const upstream=http.request({socketPath:"/run/anvil/model.sock",method:request.method,path:request.url,headers},incoming=>{
    response.writeHead(incoming.statusCode,{"Content-Type":incoming.headers["content-type"]||"application/json","Cache-Control":"no-store"});
    incoming.pipe(response); incoming.on("error",()=>response.destroy());
  });
  upstream.setTimeout(125000,()=>upstream.destroy());
  upstream.on("error",()=>{if(!response.headersSent)response.writeHead(502);response.end();});
  request.on("aborted",()=>upstream.destroy());response.on("close",()=>upstream.destroy());request.pipe(upstream);
});
relay.maxConnections=8;relay.requestTimeout=15000;relay.headersTimeout=10000;
(async()=>{
  await new Promise((resolve,reject)=>{relay.once("error",reject);relay.listen(Number(model.port),"127.0.0.1",resolve);});
  process.chdir("/app");
  const next=require("/app/node_modules/next");
  const app=next({dev:false,dir:"/app",hostname:"127.0.0.1",port:Number(process.env.ANVIL_OWNER_PORT)});
  await app.prepare();
  const socket="/run/anvil/ingress/owner.sock";
  try{const info=fs.lstatSync(socket);if(!info.isSocket())throw new Error("invalid ingress socket");fs.unlinkSync(socket);}catch(error){if(error.code!=="ENOENT")throw error;}
  const server=http.createServer(app.getRequestHandler());
  server.maxConnections=32;server.requestTimeout=15000;server.headersTimeout=10000;
  server.on("upgrade",(_request,socket)=>socket.destroy());
  await new Promise((resolve,reject)=>{server.once("error",reject);server.listen(socket,resolve);});
  fs.chmodSync(socket,0o600);
})().catch(()=>{process.stderr.write("Native owner startup failed\n");process.exit(1);});
'''


def _root_custody(value):
    import stat
    from pathlib import Path
    p=Path(value)
    if not p.is_absolute() or p.resolve(strict=True)!=p:raise ValueError("noncanonical managed path")
    for parent in [p,*p.parents]:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError("managed root custody mismatch")
    return p


def _read_rootfs_manifest(filename, maximum=32*1024*1024):
    """Read a public runtime artifact; credential readers remain private-only.

    Every owner UID must be able to qualify the same immutable manifest. Its
    reviewed hash supplies content authority; root custody prevents replacement.
    """
    import os
    import stat
    from pathlib import Path
    manifest=Path(filename)
    _root_custody(manifest.parent)
    def identity(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
                info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    try:
        fd=os.open(manifest,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            before=os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
                    or stat.S_IMODE(before.st_mode) not in {0o444,0o644}
                    or not 1<=before.st_size<=maximum):
                raise ValueError("unsafe rootfs manifest")
            chunks=[];size=0
            while size<=maximum:
                part=os.read(fd,min(65536,maximum+1-size))
                if not part:break
                chunks.append(part);size+=len(part)
            if (size!=before.st_size or size>maximum or identity(before)!=identity(os.fstat(fd))
                    or identity(before)!=identity(manifest.lstat())):
                raise ValueError("changed rootfs manifest")
            return b"".join(chunks)
        finally:
            os.close(fd)
    except OSError:
        raise ValueError("rootfs manifest unavailable") from None


def verify_rootfs(rootfs, expected_digest):
    """Qualify the complete root-owned, link-free runtime closure, including bootstrap."""
    import hashlib
    import stat
    from .auth import strict_json
    root=_root_custody(rootfs)
    raw=_read_rootfs_manifest(root/"rootfs.manifest.json")
    if hashlib.sha256(raw).hexdigest()!=expected_digest:raise ValueError("rootfs manifest mismatch")
    manifest=strict_json(raw)
    if not isinstance(manifest,dict) or set(manifest)!={"schema","files"} or manifest["schema"]!="anvil-native-owner-rootfs/v1":raise ValueError("invalid rootfs manifest")
    files=manifest["files"]
    if not isinstance(files,dict) or not 1<=len(files)<=100000:raise ValueError("invalid rootfs closure")
    total=0;seen=set()
    for entry in root.rglob("*"):
        info=entry.lstat();relative=entry.relative_to(root).as_posix()
        if info.st_uid!=0 or info.st_mode&0o022 or stat.S_ISLNK(info.st_mode):raise ValueError("unsafe rootfs entry")
        if stat.S_ISDIR(info.st_mode):continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError("unsafe rootfs entry")
        if relative=="rootfs.manifest.json":continue
        total+=info.st_size
        if total>4*1024**3 or relative not in files or not isinstance(files[relative],str) or not _SHA.fullmatch(files[relative]):raise ValueError("unbounded rootfs entry")
        digest=hashlib.sha256()
        with entry.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        if digest.hexdigest()!=files[relative]:raise ValueError("changed rootfs entry")
        seen.add(relative)
    if seen!=set(files) or not {"runtime/node/bin/node","app/native-owner-bootstrap.cjs","app/package.json"}<=seen:raise ValueError("incomplete rootfs")
    if (root/"app/native-owner-bootstrap.cjs").read_text()!=BOOTSTRAP_JS:raise ValueError("bootstrap does not match reviewed runtime")


def verify_owner_storage(spec):
    """Require a separately bounded filesystem for every writable owner mount."""
    import os
    from pathlib import Path
    plan=render(spec)
    root=_root_custody(Path("/var/lib/anvil-native-agents")/spec["deployment_id"]/"owners"/spec["id"])
    info=root.stat();parent=root.parent.stat()
    if info.st_dev==parent.st_dev or not os.path.ismount(root):raise ValueError("dedicated owner filesystem required")
    filesystem=os.statvfs(root)
    if filesystem.f_blocks*filesystem.f_frsize>spec["storage_limit_bytes"]:raise ValueError("owner filesystem exceeds bounded size")
    if not filesystem.f_flag & os.ST_NOSUID or not filesystem.f_flag & os.ST_NODEV:raise ValueError("owner filesystem must be nosuid,nodev")
    for i,arg in enumerate(plan.argv):
        if arg!="--bind":continue
        verify_writable_mount(Path(plan.argv[i+1]),spec["uid"],info.st_dev)
    return plan


def verify_writable_mount(source,uid,device):
    import stat
    # Bubblewrap resolves bind paths before mounting. FD magic paths alone
    # cannot pin them; root custody of the parent prevents owner replacement.
    _root_custody(source.parent)
    if source.resolve(strict=True)!=source:raise ValueError("owner mount symlink denied")
    mount=source.stat()
    if not stat.S_ISDIR(mount.st_mode) or mount.st_uid!=uid or stat.S_IMODE(mount.st_mode)!=0o700 or mount.st_dev!=device:raise ValueError("owner writable mount custody/quota mismatch")


def verify_bwrap(expected_digest):
    import hashlib
    import os
    import stat
    import subprocess
    from pathlib import Path
    executable=Path("/usr/bin/bwrap")
    _root_custody(executable.parent)
    fd=os.open(executable,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o6022 or not info.st_mode&0o111 or not 1<=info.st_size<=16*1024*1024:raise ValueError("bubblewrap custody mismatch")
        digest=hashlib.sha256()
        while chunk:=os.read(fd,1024*1024):digest.update(chunk)
        if digest.hexdigest()!=expected_digest:raise ValueError("bubblewrap digest mismatch")
    finally:os.close(fd)
    result=subprocess.run([str(executable),"--version"],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={},close_fds=True,timeout=5,check=False)
    if result.returncode or result.stdout!=b"bubblewrap 0.11.1\n":raise ValueError("bubblewrap version mismatch")


def open_credentials(spec, directory):
    """Pin the exact systemd read-only credential mount and open bounded files.

    systemd uses root ownership plus a read-only UID ACL (0440/0550 stat modes),
    or service ownership when ACLs are unavailable. Both require a read-only
    mount; owner-writable directories or plain-directory fallback are rejected.
    """
    import os
    import stat
    expected=f"/run/credentials/anvil-native-{spec['deployment_id']}-{spec['id']}.service"
    if directory!=expected:raise ValueError("exact systemd credentials directory required")
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC
    current=os.open("/",flags);opened=[]
    try:
        parts=path(directory).parts[1:]
        for index,part in enumerate(parts):
            next_fd=os.open(part,flags,dir_fd=current)
            os.close(current);current=next_fd;info=os.fstat(current)
            if index<len(parts)-1:
                if info.st_uid!=0 or info.st_mode&0o022:raise ValueError("credential ancestor custody mismatch")
            elif info.st_uid not in (0,spec["uid"]) or info.st_gid not in (0,spec["uid"]) or stat.S_IMODE(info.st_mode) not in (0o500,0o550):raise ValueError("credential directory custody mismatch")
        filesystem=os.fstatvfs(current)
        required=os.ST_RDONLY|os.ST_NOSUID|os.ST_NODEV
        if filesystem.f_flag&required!=required:raise ValueError("read-only protected systemd credential mount required")
        device=os.fstat(current).st_dev
        for name in ("owner-token","web-password"):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=current)
            opened.append(fd);info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid not in (0,spec["uid"]) or info.st_gid not in (0,spec["uid"]) or stat.S_IMODE(info.st_mode) not in (0o400,0o440) or not 1<=info.st_size<=4096 or info.st_dev!=device:raise ValueError("credential custody mismatch")
        return opened
    except BaseException:
        for fd in opened:os.close(fd)
        raise
    finally:os.close(current)


def _spawn(argv, descriptors):
    import subprocess
    return subprocess.run(argv,stdin=subprocess.DEVNULL,env={},pass_fds=tuple(descriptors),close_fds=True,check=False).returncode


def launch(spec):
    """Foreground owner supervisor; called only from a dedicated managed unit.

    Stops before launching if the UID, whole runtime closure, bounded storage,
    private source custody, or systemd credential contract cannot be proved.
    """
    import os
    import resource
    plan=render(spec)
    if os.getuid()!=plan.uid or os.getgid()!=plan.uid or set(os.getgroups())-{plan.uid}:raise ValueError("dedicated service identity required")
    verify_rootfs(spec["rootfs"],spec["rootfs_sha256"])
    verify_bwrap(spec["bwrap_sha256"])
    verify_owner_storage(spec)
    verify_resources()
    credentials=os.environ.get("CREDENTIALS_DIRECTORY","")
    # Owner service is not socket activated. Drop all descriptors before opening
    # its two credentials; the exec boundary also uses close_fds+an exact allowlist.
    maximum=resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    os.closerange(3,maximum)
    opened=open_credentials(spec,credentials)
    try:
        args=list(plan.argv)
        for i,arg in enumerate(args):
            if arg=="--ro-bind-data":args[i+1]=str(opened[int(args[i+1])-3])
        return _spawn(args,opened)
    finally:
        for fd in opened:os.close(fd)


def verify_resources(proc_root=None,cgroup_root=None):
    from pathlib import Path
    proc=Path(proc_root or "/proc");root=Path(cgroup_root or "/sys/fs/cgroup")
    status=(proc/"self/status").read_text()
    if not any(line.split()==["NoNewPrivs:","1"] for line in status.splitlines()):raise ValueError("NoNewPrivileges required")
    rows=(proc/"self/cgroup").read_text().splitlines()
    unified=[line[3:] for line in rows if line.startswith("0::/")]
    if len(unified)!=1:raise ValueError("cgroup v2 required")
    relative=path(unified[0]);group=root/str(relative).lstrip("/")
    if group.resolve(strict=True)!=group:raise ValueError("invalid cgroup path")
    memory=(group/"memory.max").read_text().strip();pids=(group/"pids.max").read_text().strip()
    swap=(group/"memory.swap.max").read_text().strip();cpu=(group/"cpu.max").read_text().split()
    if memory=="max" or not memory.isdecimal() or not 1<=int(memory)<=4*1024**3:raise ValueError("bounded memory required")
    if pids=="max" or not pids.isdecimal() or not 1<=int(pids)<=256:raise ValueError("bounded processes required")
    if swap!="0":raise ValueError("swap must be disabled for owner")
    if len(cpu)!=2 or not all(value.isdecimal() for value in cpu) or not 0<int(cpu[0])<=2*int(cpu[1]):raise ValueError("bounded CPU required")
