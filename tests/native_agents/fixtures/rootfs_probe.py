"""Explicit isolated rootfs functional probe, not a managed activation receipt.

Run only against a locally built candidate. No live owner, provider, credential,
or broker is touched. The probe creates and destroys its own temporary owner.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time


def args_for(root):
    return ['/usr/bin/bwrap','--die-with-parent','--new-session','--unshare-user','--disable-userns','--unshare-pid','--unshare-ipc','--unshare-uts','--unshare-cgroup','--unshare-net','--cap-drop','ALL','--clearenv','--ro-bind',str(root),'/','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--tmpfs','/run','--tmpfs','/workspace','--tmpfs','/state']


def tools(root):
    code=r'''import json,subprocess,os,socket,ctypes,pathlib,errno
assert not pathlib.Path('/home/operator').exists()
assert not pathlib.Path('/run/docker.sock').exists()
assert pathlib.Path('/etc/passwd').read_text()=='root:x:0:0:root:/nonexistent:/bin/false\n'
try:pathlib.Path('/app/probe-write').write_text('no');raise AssertionError('writable rootfs')
except OSError as error:assert error.errno in (errno.EACCES,errno.EROFS)
s=socket.socket();s.settimeout(.2)
try:s.connect(('1.1.1.1',80));raise AssertionError('host network reachable')
except OSError:pass
finally:s.close()
assert ctypes.CDLL(None,use_errno=True).unshare(0x10000000)==-1
subprocess.run(['/usr/bin/git','init','--quiet','/tmp/repo'],check=True)
print(json.dumps({'node':subprocess.check_output(['node','--version'],text=True).strip(),'python':subprocess.check_output(['/usr/bin/python3','--version'],text=True).strip(),'shell':subprocess.check_output(['/bin/bash','-c','printf sandbox'],text=True),'filesystem':'denied','network':'denied','nested_userns':'denied','git':'passed'}))
'''
    args=args_for(root)+['--setenv','HOME','/tmp','--setenv','PATH','/runtime/node/bin:/usr/bin:/bin','--setenv','LANG','C.UTF-8','--chdir','/tmp','--','/usr/bin/python3','-c',code]
    result=subprocess.run(args,capture_output=True,text=True,timeout=45,close_fds=True)
    if result.returncode:raise RuntimeError('rootfs tool probe failed: '+result.stderr[-4096:])
    return json.loads(result.stdout)


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path):super().__init__('localhost',timeout=10);self.path=str(path)
    def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(10);self.sock.connect(self.path)


def owner(root,parent):
    with tempfile.TemporaryDirectory(prefix='owner-',dir=parent) as name:
        temporary=Path(name);descriptor=[];process=None;model=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        token=secrets.token_urlsafe(32);password=secrets.token_urlsafe(32)
        try:
            model.bind(str(temporary/'model.sock'));model.listen()
            args=args_for(root)
            for source,target in [('home','/home/agent'),('ingress','/run/anvil/ingress'),('agent','/state/agent'),('sessions','/state/sessions'),('config','/state/config'),('cache','/state/cache'),('data','/state/data'),('project','/workspace/project')]:
                path=temporary/source;path.mkdir(mode=0o700);args+=['--bind',str(path),target]
            args+=['--ro-bind',str(temporary/'model.sock'),'/run/anvil/model.sock','--perms','0700','--dir','/run/credentials']
            for name,value in [('owner-token',token),('web-password',password)]:
                path=temporary/name;path.write_text(value);path.chmod(0o400);fd=os.open(path,os.O_RDONLY);descriptor.append(fd)
                args+=['--perms','0400','--ro-bind-data',str(fd),'/run/credentials/'+name]
            env={'HOME':'/home/agent','PATH':'/runtime/node/bin:/usr/bin:/bin','LANG':'C.UTF-8','XDG_CONFIG_HOME':'/state/config','XDG_CACHE_HOME':'/state/cache','XDG_DATA_HOME':'/state/data','TMPDIR':'/tmp','PI_CODING_AGENT_DIR':'/state/agent','PI_CODING_AGENT_SESSION_DIR':'/state/sessions','PI_WEB_NATIVE_AGENTS_DIRECTORIES':'["/workspace/project"]','PI_WEB_ALLOWED_HOSTS':'127.0.0.1,localhost','PI_WEB_HOSTNAME':'127.0.0.1','PI_WEB_NO_OPEN':'1','PI_WEB_SKIP_VERSION_CHECK':'1','JITI_FS_CACHE':'false','NODE_ENV':'production','ANVIL_OWNER_PORT':'9651','ANVIL_MODEL_BASE_URL':'http://127.0.0.1:9652/v1'}
            for key,value in env.items():args+=['--setenv',key,value]
            args+=['--chdir','/workspace/project','--','/runtime/node/bin/node','/app/native-owner-bootstrap.cjs']
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={},close_fds=True,pass_fds=tuple(descriptor))
            target=temporary/'ingress/owner.sock';deadline=time.monotonic()+60
            while not target.exists():
                if process.poll() is not None:raise RuntimeError('owner startup failed: '+process.stderr.read().decode()[-4096:])
                if time.monotonic()>deadline:raise TimeoutError('owner startup deadline')
                time.sleep(.1)
            def call(body,credential=token,path='/api/native-agents-owner'):
                connection=UnixHTTP(target)
                connection.request('POST',path,json.dumps(body),{'Content-Type':'application/json','Authorization':'Bearer '+credential})
                response=connection.getresponse();status=response.status;data=response.read();connection.close()
                return status,json.loads(data) if data.startswith(b'{') else None
            assert call({'action':'sessions'},credential='invalid')[0]==404
            status,value=call({'action':'sessions'});assert status==200 and value=={'sessions':[]},(status,value)
            session='native-'+secrets.token_hex(32);status,value=call({'action':'ensure','sessionID':session,'cwd':'/workspace/project'});assert status==200,(status,value)
            status,state=call({'action':'state','sessionID':session});assert status==200,(status,state)
            status,catalog=call({'action':'catalog','sessionID':session});assert status==200 and isinstance(catalog.get('commands'),list),(status,catalog)
            return {'native_token':'required','empty_session':'created','state':'available','commands':len(catalog['commands']),'bootstrap':'passed'}
        finally:
            if process:
                process.terminate()
                try:process.communicate(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=5)
            for fd in descriptor:os.close(fd)
            model.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rootfs',required=True);parser.add_argument('--temporary-parent',required=True);args=parser.parse_args()
    print(json.dumps({'kind':'isolated-rootfs-functional-probe','tools':tools(Path(args.rootfs)),'owner':owner(Path(args.rootfs),args.temporary_parent)},sort_keys=True))

if __name__=='__main__':main()
