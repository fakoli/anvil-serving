"""One explicitly authorized native maintenance transaction, never drained ZERO.

The HTTP transition can observe/consume an operator-published protected receipt;
it cannot mint policy permission. Ordinary drain/consume/readmit stay strict.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time

from .container_owner import binding, closure, digest, location, require, validate
from .keys import _private_json, _private_created_descriptor, _secure_directory

REMOTE_MEMORY_TERMINAL_UNKNOWN = object()


def _path(store, suffix):
    return Path(str(store.path) + suffix)


def _publish(path, value, *, expected=None):
    """Existing private native publication semantics with exact CAS/exclusivity."""
    _secure_directory(path.parent, create=True)
    if expected is None:
        require(not os.path.lexists(path))
    else:
        require(digest(_private_json(path)) == expected)
    fd, temporary = tempfile.mkstemp(prefix='.maintenance-', dir=path.parent)
    try:
        _private_created_descriptor(fd)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, sort_keys=True, separators=(',', ':'), allow_nan=False)
            output.flush(); os.fsync(output.fileno())
        if expected is None:
            os.link(temporary, path, follow_symlinks=False)
        else:
            require(digest(_private_json(path)) == expected)
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _eligible(owner, *, writer_exclusive=False):
    require(owner._closed and not owner._consumed and not owner._failed and not owner._assembling
            and owner._persist is not None and hasattr(owner, '_usage')
            and hasattr(owner, '_owner_descriptor') and owner._store_writer_readback is not None)
    owner._check(owner._token)
    counts, unknown = owner._drain_counts(_writer_exclusive=writer_exclusive)
    require(not any(counts.values()) and unknown == ['memory'] and owner._remote_memory_gap)
    usage = owner._usage
    store = usage.key_store
    require(usage.owner_config.router_owner_backend == 'managed-container'
            and usage.key_store._router_admission is owner)
    scope = owner.usage_scope()  # Shared native current-LIVE/all-retained-DEAD classifier.
    require(owner.usage_run_id in scope.run_ids)
    record = validate(_private_json(location(store, owner.usage_run_id)))
    require(record['phase'] == 'live' and record['anchor']['run_id'] == owner.usage_run_id
            and record['anchor']['configuration_revision'] == owner.revision
            and record['anchor']['roster_revision'] == owner.roster_revision)
    current = closure(store)
    require(current['closure'] == owner._state())
    legacy = legacy_frontier(store)
    require(legacy is not None)
    return {'legacy_receipt_sha256': legacy, 'run_id':owner.usage_run_id, 'owner_id':usage.owner_config.router_owner_id,
            'domain_id':usage.owner_config.usage_domain_id, 'store_binding':binding(store),
            'anchor_sha256':digest(record['anchor']), 'closure':owner._state(),
            'counts':counts, 'eligible_uncertainty':'remote-memory-terminal-unknown',
            'remote_frontier':'UNKNOWN; backend supplies no terminal operation readback'}


@contextmanager
def _fenced(owner):
    store = owner._usage.key_store
    with store._offline_custody(owner._server_config, producer_descriptor=owner._owner_descriptor,
                               allow_retained=True):
        yield


def preview(owner):
    with owner._condition:
        # First probe includes the actual external writer observer. Only after
        # real exclusive custody is held can this operation omit its own gate.
        _eligible(owner)
        with _fenced(owner):
            observed = _eligible(owner, writer_exclusive=True)
            prior = getattr(owner, '_maintenance_preview', None)
            now = int(time.time())
            if prior is not None and now < prior['expires_at'] and all(prior[k] == v for k,v in observed.items()):
                return prior
            value = {'schema':'router-maintenance-preview/v1', **observed,
                     'nonce':secrets.token_hex(32), 'expires_at':now+60,
                     'observation_revision':secrets.token_hex(32)}
            owner._maintenance_preview = value
            return value


def _permission(value):
    require(type(value) is dict and set(value) == {'schema','operation_id','authorization_sha256',
        'legacy_receipt_sha256','preview'} and value['schema']=='router-maintenance-permission/v1')
    for name in ('operation_id','authorization_sha256','legacy_receipt_sha256'):
        require(type(value[name]) is str and re.fullmatch('[0-9a-f]{64}',value[name]) is not None)
    require(type(value['preview']) is dict)
    return value


def history(store):
    path = _path(store, '.maintenance-last.json')
    if not os.path.lexists(path):
        legacy = legacy_frontier(store)
        return ({'legacy_receipt_sha256': legacy, 'legacy_coverage': 'uninstrumented UNKNOWN',
                 'remote_memory_terminal': 'UNKNOWN', 'drained': False} if legacy else None)
    value = _private_json(path)
    require(type(value) is dict and set(value)=={'schema','operation_id','ack_sha256'}
            and value['schema']=='router-maintenance-last/v1')
    for name in ('operation_id','ack_sha256'):
        require(type(value[name]) is str and re.fullmatch('[0-9a-f]{64}',value[name]) is not None)
    acknowledgement = _private_json(_path(store,'.maintenance-acks')/(value['operation_id']+'.json'))
    require(type(acknowledgement) is dict and set(acknowledgement)=={
        'schema','operation_id','authorization_sha256','legacy_receipt_sha256','preview','acknowledged_at','outcome'}
        and digest(acknowledgement)==value['ack_sha256']
        and type(acknowledgement['acknowledged_at']) is int
        and acknowledgement['outcome']=='acknowledged_remote_uncertainty')
    permission=_permission({k:v for k,v in acknowledgement.items() if k not in {'acknowledged_at','outcome'}})
    require(permission['operation_id']==value['operation_id']
            and permission['legacy_receipt_sha256']==legacy_frontier(store)
            and permission['preview'].get('store_binding')==binding(store)
            and type(permission['preview'].get('expires_at')) is int
            and 0 < acknowledgement['acknowledged_at'] < permission['preview']['expires_at'])
    return {'operation_id':value['operation_id'], 'ack_sha256':value['ack_sha256'],
            'outcome':'acknowledged_interruption', 'remote_memory_terminal':'UNKNOWN',
            'legacy_coverage':'uninstrumented UNKNOWN', 'drained':False}


def readmit(owner):
    with owner._condition:
        _eligible(owner)
        with _fenced(owner):
            observed = _eligible(owner, writer_exclusive=True)
            store = owner._usage.key_store
            permission = _permission(_private_json(_path(store,'.maintenance-permission.json')))
            expected = getattr(owner,'_maintenance_preview',None)
            now = int(time.time())
            require(expected is not None and permission['preview']==expected
                    and now < expected['expires_at'] <= now+60
                    and all(expected[k]==v for k,v in observed.items())
                    and permission['legacy_receipt_sha256']==observed['legacy_receipt_sha256'])
            # Exclusive publication is also durable one-shot operation replay
            # prevention. A failed later resume consumes this acknowledgement;
            # it does not authorize an automatic retry or a future generation.
            acknowledgement = {**permission,'acknowledged_at':now,'outcome':'acknowledged_remote_uncertainty'}
            _publish(_path(store,'.maintenance-acks')/(permission['operation_id']+'.json'), acknowledgement)
            last = _path(store,'.maintenance-last.json')
            old = digest(_private_json(last)) if os.path.lexists(last) else None
            _publish(last, {'schema':'router-maintenance-last/v1','operation_id':permission['operation_id'],
                            'ack_sha256':digest(acknowledgement)}, expected=old)
            saved = owner._state()
            owner._closed = False
            try:
                owner._save()  # Acknowledgement is already durable before opening.
                for resume in owner._on_readmit: resume()
            except BaseException:
                owner._closed=True;owner._token=saved['barrier_token']
                owner._save()
                raise
            owner._token=None;owner._maintenance_preview=None
            owner._condition.notify_all()
            return {'applied':True,'readmitted':True,'drained':False, 'unknown':['memory'],
                    'maintenance':history(store), **owner.status()}


def authorization(value, *, at=None):
    fields={'schema','operation_id','phase','authorization_sha256','expected_container_id',
            'expected_image_id','expected_configuration_revision','preview_sha256','expires_at',
            'legacy_receipt_sha256','acknowledge_uncertainty','ingress_barrier'}
    current = type(value) is dict and value.get('phase') == 'current-instrumented-stop'
    if current: fields |= {'successor'}
    require(type(value) is dict and set(value)==fields
            and value['schema']==('router-current-stop-authorization/v1' if current else 'router-maintenance-authorization/v1')
            and value['phase'] in {'legacy-stop','successor-readmit','current-instrumented-stop'}
            and type(value['expires_at']) is int)
    now = int(time.time()) if at is None else at
    require(type(now) is int and now < value['expires_at'] <= now + 900)
    expected = ('legacy-local-and-remote', 'exact-container-stop') if value['phase']=='legacy-stop' else (
        'remote-memory-terminal-only', 'closed-generation')
    if current: expected = ('remote-memory-terminal-only', 'closed-generation-exact-container-stop')
    require((value['acknowledge_uncertainty'], value['ingress_barrier']) == expected)
    for name in ('operation_id','authorization_sha256','expected_container_id','expected_configuration_revision'):
        require(type(value[name]) is str and re.fullmatch('[0-9a-f]{64}',value[name]) is not None)
    for name in ('preview_sha256','legacy_receipt_sha256'):
        require(value[name] is None or type(value[name]) is str and re.fullmatch('[0-9a-f]{64}',value[name]) is not None)
    image=value['expected_image_id']
    require(type(image) is str and re.fullmatch('sha256:[0-9a-f]{64}',image) is not None)
    if value['phase']=='successor-readmit' or current:require(value['legacy_receipt_sha256'] is not None)
    if current:
        successor = value['successor']
        require(type(successor) is dict and set(successor) == {'image_id', 'configuration_revision', 'compose', 'compose_sha256'})
        require(type(successor['image_id']) is str and re.fullmatch('sha256:[a-f0-9]{64}', successor['image_id']) is not None)
        for name in ('configuration_revision', 'compose_sha256'):
            require(type(successor[name]) is str and re.fullmatch('[a-f0-9]{64}', successor[name]) is not None)
        require(type(successor['compose']) is str and Path(successor['compose']).is_absolute()
                and '..' not in Path(successor['compose']).parts)
    return value


def native_readmit():
    """Fixed private-stdin operator helper; no HTTP permission-minting surface."""
    from .config import load_server_config
    from .keys import KeyStore
    from .container_owner import live_writer
    from ..router_manage import DEFAULT_INSTALLED_CONFIG, _transition_request
    from ..observability.dashboard.contracts import strict_json
    raw=sys.stdin.buffer.read(16385);require(len(raw)<=16384)
    request=strict_json(raw)
    require(type(request) is dict and set(request)=={'authorization','confirm'})
    auth=authorization(request['authorization']);require(auth['phase']=='successor-readmit')
    settings=load_server_config(DEFAULT_INSTALLED_CONFIG)
    require(hashlib.sha256(Path(DEFAULT_INSTALLED_CONFIG).read_bytes()).hexdigest()==auth['expected_configuration_revision'])
    local={'ANVIL_ROUTER_TOKEN':os.environ.get(settings.auth_env or '', '')}
    result=_transition_request('maintenance-preview',scope='router',router_url='http://127.0.0.1:8000',env=local)['result']
    require(result['closure']['configuration_revision']==auth['expected_configuration_revision'])
    if request['confirm'] is not True:
        print(json.dumps(result));return
    require(auth['preview_sha256']==digest(result) and int(time.time())<result['expires_at']
            and auth['legacy_receipt_sha256']==result['legacy_receipt_sha256'])
    store=KeyStore(settings.api_keys_path)
    permission={'schema':'router-maintenance-permission/v1','operation_id':auth['operation_id'],
                'authorization_sha256':auth['authorization_sha256'],
                'legacy_receipt_sha256':auth['legacy_receipt_sha256'],'preview':result}
    with live_writer(store):
        anchor=validate(_private_json(location(store,result['run_id'])))
        require(anchor['phase']=='live' and anchor['anchor']['docker']['container_id']==auth['expected_container_id']
                and anchor['anchor']['docker']['image_id']==auth['expected_image_id']
                and digest(anchor['anchor'])==result['anchor_sha256']
                and closure(store)['closure']==result['closure'])
        path=_path(store,'.maintenance-permission.json')
        old=digest(_private_json(path)) if os.path.lexists(path) else None
        _publish(path,permission,expected=old)
    applied=_transition_request('maintenance-readmit',scope='router',router_url='http://127.0.0.1:8000',
                                confirm=True,dry_run=False,env=local)['result']
    print(json.dumps(applied))


def validate_legacy(receipt):
    from .container_owner import docker_identity
    require(type(receipt) is dict and set(receipt)=={'schema','authorization','preview','stopped',
        'acknowledged_at','drained','legacy_coverage','remote_memory_terminal'}
        and receipt['schema']=='router-legacy-maintenance/v1' and receipt['drained'] is False
        and receipt['legacy_coverage']=='uninstrumented UNKNOWN'
        and receipt['remote_memory_terminal']=='UNKNOWN' and type(receipt['acknowledged_at']) is int)
    auth=authorization(receipt['authorization'], at=receipt['acknowledged_at']);observed=receipt['preview']
    require(auth['phase']=='legacy-stop'
            and auth.get('preview_sha256')==digest(observed)
            and receipt['acknowledged_at']<auth['expires_at'])
    require(type(observed) is dict and set(observed)=={'schema','incarnation','configuration_revision',
        'local_frontier','remote_frontier','drained','compose_path','compose_sha256'}
        and observed['schema']=='router-legacy-maintenance-preview/v1'
        and observed['local_frontier']=='UNKNOWN; legacy lacks complete all-path instrumentation'
        and observed['remote_frontier']=='UNKNOWN' and observed['drained'] is False
        and observed['configuration_revision']==auth['expected_configuration_revision']
        and type(observed['compose_path']) is str and Path(observed['compose_path']).is_absolute()
        and '..' not in Path(observed['compose_path']).parts
        and type(observed['compose_sha256']) is str and re.fullmatch('[0-9a-f]{64}',observed['compose_sha256']) is not None)
    before=docker_identity(observed['incarnation']);stopped=docker_identity(receipt['stopped'],stopped=True)
    require(before['container_id']==auth['expected_container_id'] and before['image_id']==auth['expected_image_id']
            and all(stopped[k]==v for k,v in before.items()))
    return receipt


def record_legacy(store, receipt, configuration_revision):
    """Candidate-native offline migration only; never invent an old run/anchor."""
    validate_legacy(receipt)
    require(getattr(store._writer_context,'offline_custody',False))
    with store._connect() as db:
        # Unanchored retained rows are not covered by legacy risk acceptance.
        require(db.execute('SELECT count(*) FROM usage_runs').fetchone()==(0,))
    info=store.path.stat()
    record={'schema':'router-legacy-frontier/v1','receipt':receipt,
            'store_identity':[info.st_dev,info.st_ino], 'configuration_revision':configuration_revision}
    path=_path(store,'.maintenance-legacy.json')
    if os.path.lexists(path): require(_private_json(path)==record)
    else: _publish(path,record)


def legacy_frontier(store):
    path=_path(store,'.maintenance-legacy.json')
    if not os.path.lexists(path): return None
    record=_private_json(path);info=store.path.stat()
    require(type(record) is dict and set(record)=={'schema','receipt','store_identity','configuration_revision'}
            and record['schema']=='router-legacy-frontier/v1'
            and record['store_identity']==[info.st_dev,info.st_ino]
            and re.fullmatch('[0-9a-f]{64}',str(record['configuration_revision'])) is not None)
    validate_legacy(record['receipt'])
    return digest(record['receipt'])


def _legacy_config(path):
    # Accepted legacy config is non-secret and may be group-writable. Read the
    # exact owned no-link inode with the existing bounded double-read CAS helper;
    # the human acknowledgement binds its digest, never changes its permissions.
    from ..operator_config import _read_bounded, _candidate_identity
    path=Path(path)
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1)
    raw=_read_bounded(path,max_bytes=2*1024**2,expected_identity=_candidate_identity(before))
    return raw, _candidate_identity(before)


# The running predecessor need not contain this newer phase. This fixed helper
# uses only its existing, fenced maintenance observation and custody primitives.
# It never accepts a URL, caller credential, Python fragment or permission path.
_CURRENT_STOP_CODE = r'''
import hashlib,json,os,select,sys
from pathlib import Path
from anvil_serving.router.config import load_server_config
from anvil_serving.router.keys import KeyStore,_private_json
from anvil_serving.router.container_owner import binding,closure,digest,location,require,validate,live_writer
from anvil_serving.router.maintenance import _publish,_path
from anvil_serving.router_manage import DEFAULT_INSTALLED_CONFIG,_transition_request
from anvil_serving.observability.dashboard.contracts import strict_json

def read():
    require(bool(select.select([sys.stdin],[],[],90)[0]))
    raw=sys.stdin.buffer.readline(32769);require(len(raw)<=32768)
    return strict_json(raw)
request=read()
require(type(request)is dict and set(request)=={'configuration_revision'})
settings=load_server_config(DEFAULT_INSTALLED_CONFIG)
require(hashlib.sha256(Path(DEFAULT_INSTALLED_CONFIG).read_bytes()).hexdigest()==request['configuration_revision'])
local={'ANVIL_ROUTER_TOKEN':os.environ.get(settings.auth_env or '', '')}
observed=_transition_request('maintenance-preview',scope='router',router_url='http://127.0.0.1:8000',env=local)['result']
store=KeyStore(settings.api_keys_path)
with live_writer(store):
    record=validate(_private_json(location(store,observed['run_id'])))
    require(record['phase']=='live' and digest(record['anchor'])==observed['anchor_sha256']
            and binding(store)==observed['store_binding'] and closure(store)['closure']==observed['closure'])
    value={'observation':observed,'record':record}
    print(json.dumps(value),flush=True)
    command=read()
    if command!={'release':True}:
        require(type(command)is dict and set(command)=={'commit','marker'} and command['commit']==digest(value))
        marker=command['marker'];auth=marker['authorization']
        require(marker['schema']=='router-current-stop-permission/v1' and marker['native_sha256']==digest(value)
                and auth['phase']=='current-instrumented-stop' and auth['expected_container_id']==record['anchor']['docker']['container_id']
                and auth['expected_image_id']==record['anchor']['docker']['image_id']
                and auth['expected_configuration_revision']==observed['closure']['configuration_revision'])
        import re,time
        require(type(auth['operation_id'])is str and re.fullmatch('[a-f0-9]{64}',auth['operation_id'])
                and type(auth['expires_at'])is int and int(time.time())<min(auth['expires_at'],observed['expires_at']))
        require(closure(store)['closure']==observed['closure'])
        _publish(_path(store,'.maintenance-current-stops')/(auth['operation_id']+'.json'),marker)
        print(json.dumps({'committed':digest(marker)}),flush=True)
        require(read()=={'release':True})
'''


@contextmanager
def _current_stop_session(incarnation, revision, *, _popen=None):
    """Keep the old native writer fence through host CAS and physical stop."""
    import select
    import subprocess
    from ..observability.dashboard.contracts import strict_json
    require(os.name == 'posix')
    process = (_popen or subprocess.Popen)(
        ['docker', 'exec', '-i', incarnation['container_id'], 'python', '-c', _CURRENT_STOP_CODE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    def send(value):
        require(process.poll() is None)
        process.stdin.write(json.dumps(value).encode() + b'\n'); process.stdin.flush()
    def read():
        require(bool(select.select([process.stdout], [], [], 35)[0]))
        raw = process.stdout.readline(32769)
        require(len(raw) <= 32768)
        return strict_json(raw)
    try:
        send({'configuration_revision': revision})
        native = read()
        def commit(marker):
            send({'commit': digest(native), 'marker': marker})
            require(read() == {'committed': digest(marker)} and process.poll() is None)
        yield native, commit, lambda: require(process.poll() is None)
    finally:
        try:
            if process.poll() is None:
                send({'release': True})
                process.wait(timeout=5)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            process.stdin.close(); process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)


def _current_observation(native, auth, incarnation, *, at=None):
    """Closed native vocabulary; zero is local and never remote completion."""
    require(type(native) is dict and set(native) == {'observation', 'record'})
    record = validate(native['record']); observed = native['observation']; anchor = record['anchor']
    require(record['phase'] == 'live' and anchor['docker'] == incarnation
            and incarnation['container_id'] == auth['expected_container_id']
            and incarnation['image_id'] == auth['expected_image_id'])
    require(type(observed) is dict and set(observed) == {
        'schema', 'legacy_receipt_sha256', 'run_id', 'owner_id', 'domain_id', 'store_binding',
        'anchor_sha256', 'closure', 'counts', 'eligible_uncertainty', 'remote_frontier',
        'nonce', 'expires_at', 'observation_revision'})
    require(observed['schema'] == 'router-maintenance-preview/v1'
            and observed['eligible_uncertainty'] == 'remote-memory-terminal-unknown'
            and observed['remote_frontier'] == 'UNKNOWN; backend supplies no terminal operation readback'
            and observed['legacy_receipt_sha256'] == auth['legacy_receipt_sha256'])
    for name in ('nonce', 'observation_revision', 'anchor_sha256'):
        require(type(observed[name]) is str and re.fullmatch('[a-f0-9]{64}', observed[name]) is not None)
    now = int(time.time()) if at is None else at
    require(type(observed['expires_at']) is int and now < observed['expires_at'] <= now + 60)
    counts = observed['counts']; state = observed['closure']
    require(type(counts) is dict and set(counts) == {
        'chat', 'purpose', 'audio', 'memory', 'media', 'internal', 'delivery', 'maintenance'}
        and all(type(v) is int and v == 0 for v in counts.values()))
    require(type(state) is dict and set(state) == {'barrier_token', 'configuration_revision',
        'roster_revision', 'policy_revision', 'generation', 'consumed'} and state['consumed'] is False
        and type(state['generation']) is int and 1 <= state['generation'] < 2**53)
    for name in ('barrier_token', 'configuration_revision', 'roster_revision', 'policy_revision'):
        require(type(state[name]) is str and re.fullmatch('[a-f0-9]{64}', state[name]) is not None)
    require(state['configuration_revision'] == auth['expected_configuration_revision']
            and state['configuration_revision'] == anchor['configuration_revision']
            and state['roster_revision'] == anchor['roster_revision']
            and observed['anchor_sha256'] == digest(anchor)
            and digest(observed['store_binding']) == anchor['store_binding_sha256']
            and all(observed[k] == anchor[k] for k in ('run_id', 'owner_id', 'domain_id')))
    return observed


def _current_stop_proof(values, pending, *, _run):
    """Finalize native death custody, then read its exact immutable proof."""
    from ..router_manage import (_managed_container_custody, _offline_compose_run,
                                 DEFAULT_CONTAINER, DEFAULT_SERVICE)
    native = pending['preview']['native']; run_id = native['observation']['run_id']
    result = _managed_container_custody('dead', values['compose'], DEFAULT_SERVICE, DEFAULT_CONTAINER, _run=_run)
    require(result == {'finalized': 'dead', 'run_id': run_id})
    code = ("from pathlib import Path;from anvil_serving.router.config import load_server_config;"
        "from anvil_serving.router.keys import KeyStore,_private_json;"
        "from anvil_serving.router.container_owner import location,validate,digest,require;"
        "from anvil_serving.router_manage import DEFAULT_INSTALLED_CONFIG;import json;"
        "settings=load_server_config(DEFAULT_INSTALLED_CONFIG);store=KeyStore(settings.api_keys_path)\n"
        "with store._offline_custody(settings,allow_retained=True):\n"
        f" record=validate(_private_json(location(store,{run_id!r})))\n"
        f" marker=_private_json(Path(str(store.path)+'.maintenance-current-stops')/({pending['authorization']['operation_id']!r}+'.json'))\n"
        f" require(digest(marker)=={digest(pending['marker'])!r})\n"
        " print(json.dumps(record))\n")
    record = validate(_offline_compose_run(values['compose'], DEFAULT_SERVICE, DEFAULT_CONTAINER, code, _run=_run))
    require(record['phase'] == 'ready' and record['anchor'] == native['record']['anchor']
            and record['death']['closure_generation'] == native['observation']['closure']['generation']
            and record['death']['closure_consumed'] is False)
    return record


def _current_successor(successor, incarnation, *, _run):
    """Bind declared successor bytes to the actual pinned image/config, without dotenv."""
    from ..router_manage import _compose_argv, DEFAULT_SERVICE, DEFAULT_CONTAINER, DEFAULT_INSTALLED_CONFIG
    from ..control_plane.mcp.auth_file import read_private_auth_file
    from ..observability.dashboard.contracts import strict_json
    result = _run([*_compose_argv(successor['compose'], env_file=os.devnull), 'config',
                   '--no-env-resolution', '--no-interpolate', '--format', 'json'],
                  capture_output=True, text=True, timeout=10)
    require(result.returncode == 0 and len(result.stdout or '') <= 2*1024**2)
    rendered = strict_json(result.stdout)
    require(type(rendered) is dict and rendered.get('name') == incarnation['compose_project'])
    service = rendered.get('services', {}).get(DEFAULT_SERVICE, {})
    require(service.get('container_name') == DEFAULT_CONTAINER)
    image = service.get('image')
    require(type(image) is str and re.fullmatch('[A-Za-z0-9_./:@-]{1,512}', image) is not None and not image.startswith('-'))
    result = _run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], capture_output=True, text=True, timeout=5)
    require(result.returncode == 0 and (result.stdout or '').strip() == successor['image_id'])
    mounts = service.get('volumes')
    require(type(mounts) is list and all(type(x) is dict for x in mounts))
    config = [x for x in mounts if x.get('target') == DEFAULT_INSTALLED_CONFIG]
    require(len(config) == 1 and config[0].get('type') == 'bind' and config[0].get('read_only') is True
            and type(config[0].get('source')) is str and Path(config[0]['source']).is_absolute())
    raw = read_private_auth_file(config[0]['source'], max_bytes=2*1024**2)
    require(hashlib.sha256(raw).hexdigest() == successor['configuration_revision'])


def _run_current_stop(values, auth, config, *, confirm, preview_out, _run):
    from ..router_manage import (_container_incarnation, _restart_custody, _offline_compose_roster,
                                 DEFAULT_CONTAINER, DEFAULT_SERVICE, DEFAULT_INSTALLED_CONFIG)
    from .container_owner import docker_identity
    from ..control_plane.mcp.auth_file import read_private_auth_file
    from ..observability.dashboard.contracts import strict_json
    receipt_path = Path(values['receipt_out']); pending_path = Path(str(receipt_path) + '.pending')
    require(not os.path.lexists(receipt_path))
    successor = auth['successor']
    def unchanged():
        require(strict_json(read_private_auth_file(config, max_bytes=16384)) == values
                and strict_json(read_private_auth_file(values['authorization'], max_bytes=16384)) == auth
                and hashlib.sha256(read_private_auth_file(values['compose'], max_bytes=2*1024**2)).hexdigest() == values['compose_sha256']
                and hashlib.sha256(read_private_auth_file(successor['compose'], max_bytes=2*1024**2)).hexdigest() == successor['compose_sha256'])
    unchanged()
    if os.path.lexists(pending_path):
        # Once pending exists, no path can issue another stop. Recovery is only
        # evidence finalization for the exact already-stopped incarnation.
        require(confirm)
        pending = _private_json(pending_path)
        require(type(pending) is dict and set(pending) == {'schema', 'authorization', 'preview', 'marker', 'acknowledged_at'}
                and pending['schema'] == 'router-current-stop-pending/v1' and pending['authorization'] == auth)
        before = pending['preview']['incarnation']
        _current_successor(successor, before, _run=_run)
        require(type(pending['acknowledged_at']) is int and 0 < pending['acknowledged_at'] < auth['expires_at'])
        authorization(auth, at=pending['acknowledged_at'])
        _current_observation(pending['preview']['native'], auth, before, at=pending['acknowledged_at'])
        require(auth['preview_sha256'] == digest(pending['preview'])
                and pending['preview']['compose_path'] == values['compose']
                and pending['preview']['compose_sha256'] == values['compose_sha256']
                and pending['marker'] == {'schema': 'router-current-stop-permission/v1',
                    'authorization': auth, 'native_sha256': digest(pending['preview']['native'])})
        dead = _container_incarnation(DEFAULT_CONTAINER, _run=_run, stopped=True)
        require(all(dead[k] == v for k, v in before.items()))
    else:
        before = _container_incarnation(DEFAULT_CONTAINER, _run=_run)
        docker_identity(before)
        _current_successor(successor, before, _run=_run)
        require(before['container_id'] == auth['expected_container_id'] and before['image_id'] == auth['expected_image_id'])
        roster = _offline_compose_roster(values['compose'], DEFAULT_SERVICE, DEFAULT_CONTAINER, _run=_run, live_target=True, _metadata_only=True)
        mounted = [m for m in _restart_custody(DEFAULT_CONTAINER, _run)['mounts'] if m['destination'] == DEFAULT_INSTALLED_CONFIG]
        require(len(mounted) == 1 and mounted[0]['type'] == 'bind' and mounted[0]['read_only'] is True)
        raw = read_private_auth_file(mounted[0]['source'], max_bytes=2*1024**2)
        require(hashlib.sha256(raw).hexdigest() == auth['expected_configuration_revision'])
        # The admitted profile, not another same-project file, owns this stop.
        label = _run(['docker', 'inspect', '--format', '{{json (index .Config.Labels "com.docker.compose.project.config_files")}}',
                      before['container_id']], capture_output=True, text=True, timeout=5)
        require(label.returncode == 0 and len(label.stdout or '') <= 4096 and strict_json(label.stdout) == values['compose'])
        with _current_stop_session(before, auth['expected_configuration_revision']) as (native, commit, alive):
            _current_observation(native, auth, before)
            observed = {'schema': 'router-current-stop-preview/v1', 'incarnation': before, 'native': native,
                        'compose_path': values['compose'], 'compose_sha256': values['compose_sha256'],
                        'successor': successor, 'local_frontier': 'instrumented-quiesced',
                        'remote_memory_terminal': 'UNKNOWN', 'drained': False}
            def recheck():
                unchanged(); authorization(auth)
                _current_successor(successor, before, _run=_run)
                require(int(time.time()) < native['observation']['expires_at']
                        and before == _container_incarnation(DEFAULT_CONTAINER, _run=_run)
                        and roster == _offline_compose_roster(values['compose'], DEFAULT_SERVICE, DEFAULT_CONTAINER, _run=_run, live_target=True, _metadata_only=True)
                        and read_private_auth_file(mounted[0]['source'], max_bytes=2*1024**2) == raw)
                alive()
            recheck()
            if not confirm:
                if preview_out: _publish(Path(preview_out), observed)
                return observed
            require(auth['preview_sha256'] == digest(observed))
            marker = {'schema': 'router-current-stop-permission/v1', 'authorization': auth, 'native_sha256': digest(native)}
            pending = {'schema': 'router-current-stop-pending/v1', 'authorization': auth,
                       'preview': observed, 'marker': marker, 'acknowledged_at': int(time.time())}
            commit(marker)  # Canonical durable pending permission, before any host receipt or stop.
            _publish(pending_path, pending)
            recheck()
            stopped = _run(['docker', 'stop', before['container_id']], capture_output=True, text=True, timeout=30)
            require(stopped.returncode == 0)
            dead = _container_incarnation(DEFAULT_CONTAINER, _run=_run, stopped=True)
            require(all(dead[k] == v for k, v in before.items()))
    docker_identity(dead, stopped=True)
    unchanged(); authorization(auth)
    proof = _current_stop_proof(values, pending, _run=_run)
    require(proof['death']['stopped'] == dead
            and dead == _container_incarnation(DEFAULT_CONTAINER, _run=_run, stopped=True))
    unchanged()
    receipt = {'schema': 'router-current-stop/v1', 'authorization': auth, 'preview': pending['preview'],
               'acknowledged_at': pending['acknowledged_at'], 'stopped': dead, 'death_sha256': digest(proof['death']),
               'local_frontier': 'instrumented-quiesced', 'remote_memory_terminal': 'UNKNOWN',
               'legacy_receipt_sha256': auth['legacy_receipt_sha256'], 'drained': False}
    _publish(receipt_path, receipt)
    return {'applied': True, 'stopped': True, 'drained': False, 'unknown': ['memory'],
            'local_frontier': 'instrumented-quiesced', 'remote_memory_terminal': 'UNKNOWN',
            'receipt_sha256': digest(receipt), 'death_sha256': receipt['death_sha256']}


def run(config, *, confirm=False, preview_out=None, _run=None):
    """Phase-specific managed operations selected by one private operator config."""
    import subprocess
    from ..router_manage import (_container_incarnation, _restart_custody, _offline_compose_roster,
        _serving_authority_mutation, DEFAULT_CONTAINER, DEFAULT_SERVICE, DEFAULT_INSTALLED_CONFIG)
    from ..control_plane.mcp.auth_file import read_private_auth_file
    from ..observability.dashboard.contracts import strict_json
    config=Path(config).expanduser().absolute()
    values=strict_json(read_private_auth_file(config,max_bytes=16384))
    require(type(values) is dict and set(values)=={'schema','compose','compose_sha256','authorization','receipt_out'}
            and values['schema']=='router-maintenance-config/v1')
    require(type(values['compose_sha256']) is str and re.fullmatch('[0-9a-f]{64}',values['compose_sha256']) is not None)
    for name in ('compose','authorization','receipt_out'):
        require(type(values[name]) is str and Path(values[name]).is_absolute() and '..' not in Path(values[name]).parts)
    auth_raw=read_private_auth_file(values['authorization'],max_bytes=16384)
    auth=authorization(strict_json(auth_raw))
    native=_run or subprocess.run
    @_serving_authority_mutation
    def operation():
        if auth['phase'] == 'current-instrumented-stop':
            return _run_current_stop(values, auth, config, confirm=confirm, preview_out=preview_out, _run=native)
        compose_raw=read_private_auth_file(values['compose'],max_bytes=2*1024**2)
        require(hashlib.sha256(compose_raw).hexdigest()==values['compose_sha256'])
        pending=Path(values['receipt_out'] + '.pending')
        pending_value=_private_json(pending) if auth['phase']=='legacy-stop' and os.path.lexists(pending) else None
        if pending_value is not None:
            require(type(pending_value) is dict and set(pending_value)=={'schema','authorization','preview'}
                    and pending_value['schema']=='router-legacy-maintenance-pending/v1'
                    and pending_value['authorization']==auth)
            before=pending_value['preview']['incarnation']
        else:
            before=_container_incarnation(DEFAULT_CONTAINER,_run=native)
        require(before['container_id']==auth['expected_container_id'] and before['image_id']==auth['expected_image_id'])
        from ..router_manage import _container_compose_project
        state,_=_container_compose_project(DEFAULT_CONTAINER,_run=native)
        live=state=='running'
        require(live or pending_value is not None and state in {'exited','dead'})
        roster=_offline_compose_roster(values['compose'],DEFAULT_SERVICE,DEFAULT_CONTAINER,_run=native,live_target=live,_metadata_only=auth['phase']=='legacy-stop')
        mounted=[m for m in _restart_custody(DEFAULT_CONTAINER,native)['mounts'] if m['destination']==DEFAULT_INSTALLED_CONFIG]
        require(len(mounted)==1 and mounted[0]['type']=='bind' and mounted[0]['read_only'] is True)
        if auth['phase']=='legacy-stop':
            raw, config_identity = _legacy_config(mounted[0]['source'])
        else:
            raw=read_private_auth_file(mounted[0]['source'],max_bytes=2*1024**2)
        require(hashlib.sha256(raw).hexdigest()==auth['expected_configuration_revision'])
        if auth['phase']=='legacy-stop':
            # Legacy risk acknowledgement binds the actually admitted profile,
            # never a future candidate file merely sharing project labels.
            label=native(['docker','inspect','--format','{{json (index .Config.Labels "com.docker.compose.project.config_files")}}',
                          before['container_id']],capture_output=True,text=True,timeout=5)
            require(label.returncode==0 and len(label.stdout or '')<=4096
                    and strict_json(label.stdout)==values['compose'])
            preview_value={'schema':'router-legacy-maintenance-preview/v1','incarnation':before,
                'configuration_revision':auth['expected_configuration_revision'],
                'compose_path':values['compose'],'compose_sha256':values['compose_sha256'],
                'local_frontier':'UNKNOWN; legacy lacks complete all-path instrumentation',
                'remote_frontier':'UNKNOWN','drained':False}
            if not confirm:
                if preview_out: _publish(Path(preview_out),preview_value)
                return preview_value
            require(auth['preview_sha256']==digest(preview_value) and not os.path.lexists(values['receipt_out']))
            # Protected human acknowledgement accepts the named legacy risk;
            # physical stop establishes the ingress barrier; old submits stay UNKNOWN.
            require(read_private_auth_file(values['authorization'],max_bytes=16384)==auth_raw)
            require(read_private_auth_file(values['compose'],max_bytes=2*1024**2)==compose_raw)
            if pending_value is None:
                _publish(pending,{'schema':'router-legacy-maintenance-pending/v1','authorization':auth,'preview':preview_value})
            require(_legacy_config(mounted[0]['source']) == (raw, config_identity))
            if live:
                require(before==_container_incarnation(DEFAULT_CONTAINER,_run=native)
                        and roster==_offline_compose_roster(values['compose'],DEFAULT_SERVICE,DEFAULT_CONTAINER,
                            _run=native,live_target=True,_metadata_only=True))
                stopped=native(['docker','stop',before['container_id']],capture_output=True,text=True,timeout=30)
                require(stopped.returncode==0)
            dead=_container_incarnation(DEFAULT_CONTAINER,_run=native,stopped=True)
            require(all(dead[k]==v for k,v in before.items()))
            _offline_compose_roster(values['compose'],DEFAULT_SERVICE,DEFAULT_CONTAINER,_run=native,_metadata_only=True)
            require(_legacy_config(mounted[0]['source']) == (raw, config_identity)
                    and read_private_auth_file(values['compose'],max_bytes=2*1024**2)==compose_raw
                    and read_private_auth_file(values['authorization'],max_bytes=16384)==auth_raw)
            receipt={'schema':'router-legacy-maintenance/v1','authorization':auth,'preview':preview_value,
                     'stopped':dead,'acknowledged_at':int(time.time()),'drained':False,
                     'legacy_coverage':'uninstrumented UNKNOWN','remote_memory_terminal':'UNKNOWN'}
            validate_legacy(receipt);_publish(Path(values['receipt_out']),receipt)
            return {'applied':True,'stopped':True,'drained':False,'receipt_sha256':digest(receipt),
                    'legacy_coverage':'uninstrumented UNKNOWN','remote_memory_terminal':'UNKNOWN'}
        require(read_private_auth_file(values['authorization'],max_bytes=16384)==auth_raw)
        request={'authorization':auth,'confirm':bool(confirm)}
        code='from anvil_serving.router.maintenance import native_readmit; native_readmit()'
        result=native(['docker','exec','-i',before['container_id'],'python','-c',code],
                      input=json.dumps(request),capture_output=True,text=True,timeout=30)
        require(result.returncode==0 and len(result.stdout or '')<=16384
                and before==_container_incarnation(DEFAULT_CONTAINER,_run=native)
                and roster==_offline_compose_roster(values['compose'],DEFAULT_SERVICE,DEFAULT_CONTAINER,
                    _run=native,live_target=True))
        require(read_private_auth_file(values['authorization'],max_bytes=16384)==auth_raw
                and read_private_auth_file(values['compose'],max_bytes=2*1024**2)==compose_raw
                and read_private_auth_file(mounted[0]['source'],max_bytes=2*1024**2)==raw)
        response=strict_json(result.stdout)
        if not confirm and preview_out:_publish(Path(preview_out),response)
        return response
    return operation()


def dispatch(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='One exact acknowledged router maintenance operation')
    parser.add_argument('--config',required=True);parser.add_argument('--preview-out');parser.add_argument('--confirm',action='store_true')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(run(args.config,confirm=args.confirm,preview_out=args.preview_out)));return 0
    except (ValueError,OSError):
        print('router maintenance HOLD; native custody or protected acknowledgement unavailable',file=sys.stderr);return 1
