import copy
import json
import pytest
from anvil_serving.native_agents.sandbox import render,BOOTSTRAP_JS


def declaration():
    return {"id":"workspace-one","deployment_id":"test","uid":61001,"rootfs":"/opt/anvil-native-agents/test/rootfs-"+"a"*64,"rootfs_sha256":"a"*64,"bwrap_sha256":"b"*64,
            "directories":[{"id":"project"}],"owner_port":9501,"model_port":9502,"storage_limit_bytes":1024**3}


def test_plan_confines_whole_owner_with_fixed_namespace_mount_and_environment_contract():
    spec=declaration();plan=render(spec);args=list(plan.argv);env=dict(plan.environment)
    assert args[-3:]==["--","/runtime/node/bin/node","/app/native-owner-bootstrap.cjs"]
    for flag in ("--unshare-user","--disable-userns","--unshare-pid","--unshare-ipc","--unshare-uts","--unshare-cgroup","--unshare-net","--clearenv"): assert flag in args
    assert not any("-try" in arg for arg in args)
    assert args[args.index("--ro-bind")+1:args.index("--ro-bind")+3]==[spec["rootfs"],"/"]
    assert env["PI_CODING_AGENT_DIR"]=="/state/agent"
    assert env["PI_CODING_AGENT_SESSION_DIR"]=="/state/sessions"
    assert env["PI_WEB_NATIVE_AGENTS_DIRECTORIES"]=='["/workspace/project"]'
    assert not set(env)&{"SSH_AUTH_SOCK","DOCKER_HOST","HTTP_PROXY","HTTPS_PROXY","OPENAI_API_KEY","PI_WEB_NATIVE_AGENTS_TOKEN","PI_WEB_PASSWORD"}
    assert plan.credential_fds==((3,"owner-token"),(4,"web-password"))
    assert plan.public()["activation"]=="requires-live-qualification"
    assert plan.public()["uid"]==61001 and plan.public()["id"]=="workspace-one"
    assert "/var/lib" not in json.dumps(plan.public()) and "/opt/" not in json.dumps(plan.public())
    assert any("bounded filesystem" in item for item in plan.supervisor_requirements)
    assert any("inherited descriptor" in item for item in plan.supervisor_requirements)


def test_no_arbitrary_arguments_environment_or_host_mounts():
    spec=declaration();spec["argv"]=["/bin/sh"]
    with pytest.raises(ValueError):render(spec)
    spec=declaration();spec["directories"][0]["path"]="/etc"
    with pytest.raises(ValueError):render(spec)
    spec=declaration();spec["directories"].append(copy.deepcopy(spec["directories"][0]))
    with pytest.raises(ValueError):render(spec)


@pytest.mark.parametrize("key,value",[(None,None),("memory.max","max"),("pids.max","1000"),("cpu.max","max 100000"),("memory.swap.max","max")])
def test_effective_resource_limits_required(tmp_path,key,value):
    from anvil_serving.native_agents.sandbox import verify_resources
    proc=tmp_path/"proc";group=tmp_path/"cgroup/unit";group.mkdir(parents=True);(proc/"self").mkdir(parents=True)
    (proc/"self/status").write_text("NoNewPrivs:\t1\n");(proc/"self/cgroup").write_text("0::/unit\n")
    for name,default in {"memory.max":str(4*1024**3),"pids.max":"256","cpu.max":"200000 100000","memory.swap.max":"0"}.items():(group/name).write_text(value if name==key else default)
    if key:
        with pytest.raises(ValueError):verify_resources(proc,tmp_path/"cgroup")
    else:verify_resources(proc,tmp_path/"cgroup")


def credential_fixture(tmp_path,monkeypatch):
    import os
    from types import SimpleNamespace
    spec=declaration();directory=f"/run/credentials/anvil-native-{spec['deployment_id']}-{spec['id']}.service"
    root=tmp_path/directory.lstrip('/');root.mkdir(parents=True)
    (tmp_path/'run').chmod(0o755);root.parent.chmod(0o755)
    for name in ('owner-token','web-password'):
        entry=root/name;entry.write_text('fixture-'+name);entry.chmod(0o440)
    root.chmod(0o550)
    original_open=os.open;original_stat=os.fstat
    def opening(name,flags,*args,**kwargs):
        return original_open(str(tmp_path) if name=='/' else name,flags,*args,**kwargs)
    def info(fd):
        value=original_stat(fd)
        return SimpleNamespace(st_uid=0,st_gid=0,st_mode=value.st_mode,st_dev=value.st_dev,st_size=value.st_size,st_nlink=value.st_nlink)
    monkeypatch.setattr(os,'open',opening);monkeypatch.setattr(os,'fstat',info)
    monkeypatch.setattr(os,'fstatvfs',lambda _:SimpleNamespace(f_flag=os.ST_RDONLY|os.ST_NOSUID|getattr(os,'ST_NODEV',4)))
    monkeypatch.setattr(os,'ST_NODEV',getattr(os,'ST_NODEV',4),raising=False)
    return spec,directory,root


def test_systemd_credentials_open_with_pinned_directory_and_no_inherited_flag(tmp_path,monkeypatch):
    import os
    from anvil_serving.native_agents.sandbox import open_credentials
    spec,directory,root=credential_fixture(tmp_path,monkeypatch)
    opened=open_credentials(spec,directory)
    try:
        assert [os.read(fd,64) for fd in opened]==[b'fixture-owner-token',b'fixture-web-password']
        assert not any(os.get_inheritable(fd) for fd in opened)
    finally:
        for fd in opened:os.close(fd)


@pytest.mark.parametrize('kind',['path','symlink-parent','symlink-file','fifo','hardlink','writeable-directory','writeable-file','world-readable','writeable-mount','writeable-ancestor'])
def test_credential_custody_failures_rejected_without_fifo_block(tmp_path,monkeypatch,kind):
    import os
    from types import SimpleNamespace
    from anvil_serving.native_agents.sandbox import open_credentials
    spec,directory,root=credential_fixture(tmp_path,monkeypatch)
    if kind=='path':directory='/tmp/credentials'
    elif kind=='symlink-parent':
        root.chmod(0o750);root.rename(root.with_name('actual'));root.symlink_to(root.with_name('actual'))
    elif kind=='writeable-directory':root.chmod(0o750)
    elif kind=='writeable-ancestor':root.parent.chmod(0o777)
    elif kind=='writeable-mount':monkeypatch.setattr(os,'fstatvfs',lambda _:SimpleNamespace(f_flag=os.ST_NOSUID|os.ST_NODEV))
    else:
        root.chmod(0o750);entry=root/'owner-token'
        if kind=='symlink-file':entry.unlink();entry.symlink_to(root/'web-password')
        elif kind=='fifo':entry.unlink();os.mkfifo(entry)
        elif kind=='hardlink':os.link(entry,root/'duplicate')
        elif kind=='writeable-file':entry.chmod(0o640)
        elif kind=='world-readable':entry.chmod(0o444)
        root.chmod(0o550)
    with pytest.raises((ValueError,OSError)):open_credentials(spec,directory)


@pytest.mark.parametrize('kind',['valid','digest','version','mode','uid','hardlink','symlink','fifo'])
def test_bubblewrap_custody_and_digest_precede_execution(tmp_path,monkeypatch,kind):
    import hashlib
    import os
    import subprocess
    from types import SimpleNamespace
    from anvil_serving.native_agents.sandbox import verify_bwrap
    executable=tmp_path/'bwrap';executable.write_bytes(b'qualified binary fixture');executable.chmod(0o755)
    digest=hashlib.sha256(executable.read_bytes()).hexdigest();calls=[]
    if kind=='digest':digest='f'*64
    if kind=='mode':executable.chmod(0o777)
    if kind=='hardlink':os.link(executable,tmp_path/'other')
    if kind=='symlink':executable.rename(tmp_path/'target');executable.symlink_to(tmp_path/'target')
    if kind=='fifo':executable.unlink();os.mkfifo(executable)
    original_open=os.open;original_stat=os.fstat
    monkeypatch.setattr(os,'open',lambda p,f:original_open(executable,f))
    def metadata(fd):
        v=original_stat(fd)
        return SimpleNamespace(st_mode=v.st_mode,st_uid=61001 if kind=='uid' else 0,st_nlink=v.st_nlink,st_size=v.st_size)
    monkeypatch.setattr(os,'fstat',metadata)
    monkeypatch.setattr('anvil_serving.native_agents.sandbox._root_custody',lambda _:None)
    def run(argv,**kwargs):calls.append(argv);return SimpleNamespace(returncode=0,stdout=b'bubblewrap 0.10.0\n' if kind=='version' else b'bubblewrap 0.11.1\n')
    monkeypatch.setattr(subprocess,'run',run)
    if kind=='valid':verify_bwrap(digest);assert calls==[['/usr/bin/bwrap','--version']]
    else:
        with pytest.raises((ValueError,OSError)):verify_bwrap(digest)
        assert len(calls)==(1 if kind=='version' else 0)


@pytest.mark.parametrize('kind',['valid','manifest-digest','tamper','extra','symlink','hardlink','mode','bootstrap'])
def test_rootfs_manifest_qualifies_exact_content_and_bootstrap(tmp_path,monkeypatch,kind):
    import hashlib
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from anvil_serving.native_agents.sandbox import verify_rootfs
    root=tmp_path.resolve()/'rootfs';root.mkdir()
    files={'runtime/node/bin/node':'node fixture','app/native-owner-bootstrap.cjs':BOOTSTRAP_JS,'app/package.json':'{}'}
    if kind=='bootstrap':files['app/native-owner-bootstrap.cjs']='unreviewed entrypoint'
    for name,value in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value);p.chmod(0o644)
    raw=json.dumps({'schema':'anvil-native-owner-rootfs/v1','files':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}},sort_keys=True).encode()
    (root/'rootfs.manifest.json').write_bytes(raw);(root/'rootfs.manifest.json').chmod(0o644);digest=hashlib.sha256(raw).hexdigest()
    for p in root.rglob('*'):
        if p.is_dir():p.chmod(0o755)
    if kind=='manifest-digest':digest='f'*64
    if kind=='tamper':(root/'app/package.json').write_text('changed')
    if kind=='extra':(root/'extra').write_text('not declared')
    if kind=='symlink':(root/'app/package.json').unlink();(root/'app/package.json').symlink_to(root/'runtime/node/bin/node')
    if kind=='hardlink':os.link(root/'app/package.json',root/'alias')
    if kind=='mode':(root/'app/package.json').chmod(0o666)
    monkeypatch.setattr('anvil_serving.native_agents.sandbox._root_custody',lambda p:Path(p))
    original=Path.lstat
    def info(p):
        v=original(p);return SimpleNamespace(**{k:(0 if k=='st_uid' else getattr(v,k)) for k in dir(v) if k.startswith('st_')})
    monkeypatch.setattr(Path,'lstat',info)
    original_fstat=os.fstat
    def fd_info(fd):
        v=original_fstat(fd);return SimpleNamespace(**{k:(0 if k=='st_uid' else getattr(v,k)) for k in dir(v) if k.startswith('st_')})
    monkeypatch.setattr(os,'fstat',fd_info)
    if kind=='valid':verify_rootfs(root,digest)
    else:
        with pytest.raises(ValueError):verify_rootfs(root,digest)
