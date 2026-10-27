"""Host-only bootstrap from one completed, acknowledged current-owner stop."""
import hashlib
from copy import deepcopy
from pathlib import Path
import time
import tempfile
import tomllib

from . import router_manage as lifecycle
from .control_plane.mcp.auth_file import read_private_auth_file
from .observability.dashboard.contracts import strict_json
from .operator_config import _read_bounded
from .router.container_owner import digest, docker_identity, hex_id, require, validate
from .router.maintenance import authorization, _current_observation, _current_successor


def completed_receipt(path, stopped=None):
    receipt = strict_json(read_private_auth_file(path, max_bytes=32768))
    require(type(receipt) is dict and set(receipt) == {'schema', 'authorization', 'preview',
        'acknowledged_at', 'stopped', 'death_sha256', 'local_frontier', 'remote_memory_terminal',
        'legacy_receipt_sha256', 'drained'} and receipt['schema'] == 'router-current-stop/v1'
        and receipt['local_frontier'] == 'instrumented-quiesced'
        and receipt['remote_memory_terminal'] == 'UNKNOWN' and receipt['drained'] is False
        and type(receipt['acknowledged_at']) is int
        and 0 < receipt['acknowledged_at'] <= int(time.time()))
    auth = authorization(receipt['authorization'], at=receipt['acknowledged_at'])
    require(auth['phase'] == 'current-instrumented-stop'
        and receipt['legacy_receipt_sha256'] == auth['legacy_receipt_sha256'])
    hex_id(receipt['death_sha256'])
    preview = receipt['preview']
    fields = {'schema', 'incarnation', 'native', 'compose_path', 'compose_sha256', 'successor',
              'local_frontier', 'remote_memory_terminal', 'drained'}
    require(type(preview) is dict and set(preview) in (fields, fields | {'environment'})
        and preview['schema'] == 'router-current-stop-preview/v1'
        and digest(preview) == auth['preview_sha256'] and preview['successor'] == auth['successor']
        and all(preview[k] == receipt[k] for k in ('local_frontier', 'remote_memory_terminal', 'drained')))
    old = Path(preview['compose_path'])
    require(old.is_absolute() and '..' not in old.parts)
    hex_id(preview['compose_sha256'])
    recorded_stop = docker_identity(receipt['stopped'], stopped=True)
    if stopped is not None:
        docker_identity(stopped, stopped=True)
        require(recorded_stop == stopped)
    require(all(recorded_stop[k] == v for k, v in docker_identity(preview['incarnation']).items()))
    _current_observation(preview['native'], auth, preview['incarnation'], at=receipt['acknowledged_at'])
    return receipt


def _snapshot(path, compose, service, container, stopped, *, _run, env_file, execution_env,
              rollback_to_predecessor=False, running_incarnation=None):
    receipt = completed_receipt(path, stopped)
    preview, auth = receipt['preview'], receipt['authorization']
    successor = auth['successor']
    require(service == lifecycle.DEFAULT_SERVICE and container == lifecycle.DEFAULT_CONTAINER)
    if rollback_to_predecessor:
        target = {'compose': preview['compose_path'], 'image_id': stopped['image_id'],
                  'configuration_revision': auth['expected_configuration_revision'],
                  'compose_sha256': preview['compose_sha256']}
        require(compose == target['compose'])
        expected_compose_sha256 = preview['compose_sha256']
    else:
        target = successor
        require(compose == target['compose'])
        expected_compose_sha256 = target['compose_sha256']
    environment = preview.get('environment')
    if environment is None:
        require(env_file is None)
    else:
        require(type(environment) is dict and set(environment) == {'file', 'sha256'}
            and type(environment['file']) is str and Path(environment['file']).is_absolute()
            and '..' not in Path(environment['file']).parts and env_file == environment['file'])
        hex_id(environment['sha256'])
        require(hashlib.sha256(read_private_auth_file(env_file, max_bytes=65536)).hexdigest() == environment['sha256'])
    for target_path, expected_digest in ((preview['compose_path'], preview['compose_sha256']),
                                         (compose, expected_compose_sha256)):
        require(hashlib.sha256(read_private_auth_file(target_path, max_bytes=2*1024**2)).hexdigest()
                == expected_digest)
    def read(argv):
        result = _run(argv, capture_output=True, text=True, timeout=10, env=execution_env)
        require(result.returncode == 0 and len(result.stdout or '') <= lifecycle.MAX_COMPOSE_FILE_BYTES)
        return result.stdout or ''
    candidate = _current_successor(target, preview['incarnation'], _run=_run)
    if running_incarnation is None:
        old = lifecycle._offline_compose_roster(preview['compose_path'], service, container, _run=_run,
            env_file=env_file, execution_env=execution_env, _metadata_only=True, _owner_state_only=True)
    else:
        prior_target = {'compose': preview['compose_path'], 'image_id': stopped['image_id'],
                        'configuration_revision': auth['expected_configuration_revision']}
        old_rendered = _current_successor(prior_target, preview['incarnation'], _run=_run)
        old_mounts = old_rendered['services'][service]['volumes']
        old_config = next(m for m in old_mounts if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
        old_scope = lifecycle._owner_storage_scope(old_rendered, old_mounts, old_config, {}, read)
        old = {'rendered': old_rendered, 'owner_storage': old_scope}
    require(old['owner_storage']['configuration_revision'] == auth['expected_configuration_revision'])
    if rollback_to_predecessor:
        require(candidate == old['rendered'] and target['image_id'] == stopped['image_id'])
    selected = candidate['services'][service]
    # Config aliases are declared mounts of the same old/new file. Preserve
    # their exact destinations and every other runtime/mount declaration.
    def normalized(rendered):
        result = deepcopy(rendered)
        # The exact successor image is independently pinned by _current_successor.
        result['services'][service]['image'] = '<acknowledged-router-image>'
        mounts = result['services'][service]['volumes']
        source = next(m['source'] for m in mounts if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
        aliases = [m for m in mounts if m.get('type') == 'bind'
                   and m.get('read_only') is True and m['source'] == source]
        require(1 <= len(aliases) <= 2 and len({m['target'] for m in aliases}) == len(aliases))
        for mount in mounts:
            if mount.get('type') == 'bind' and mount.get('read_only') is True and mount['source'] == source:
                mount['source'] = '<acknowledged-router-config>'
        return result
    require(normalized(candidate) == normalized(old['rendered']))
    mounts = selected['volumes']
    config = next(m for m in mounts if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
    scope = lifecycle._owner_storage_scope(candidate, mounts, config, {}, read)
    require(scope['configuration_revision'] == target['configuration_revision']
        and all(scope[k] == old['owner_storage'][k] for k in ('sources', 'directories', 'lineages')))
    from .router.config import _parse_server_config
    def settings(rendered):
        source = next(m['source'] for m in rendered['services'][service]['volumes']
                      if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
        return _parse_server_config(tomllib.loads(_read_bounded(Path(source),
            max_bytes=lifecycle.MAX_ROUTER_CONFIG_BYTES).decode()), source, container_paths=True)
    before, after = settings(old['rendered']), settings(candidate)
    require(before.router_owner_backend == after.router_owner_backend == 'managed-container'
        and all(getattr(before, k) == getattr(after, k) for k in ('api_keys_path', 'admission_state_path',
            'router_owner_id', 'router_owner_roster', 'usage_domain_id')))
    if running_incarnation is None:
        require(lifecycle._container_incarnation(container, _run=_run, stopped=True) == stopped)
    else:
        active = lifecycle._offline_compose_roster(compose, service, container, _run=_run,
            env_file=env_file, execution_env=execution_env, live_target=True, _owner_state_only=True)
        require(active['target'] is not None
            and all(active['target'].get(k) == running_incarnation[k] for k in
                ('container_id', 'image_id', 'started_at', 'restart_count', 'compose_project', 'compose_service'))
            and all(active['owner_storage'][k] == scope[k] for k in ('sources', 'directories', 'lineages'))
            and lifecycle._container_incarnation(container, _run=_run) == running_incarnation)
    return receipt, old, candidate, scope


def require_successor(path, compose, service, container, stopped, *, _run, env_file=None, execution_env=None):
    """Read-only native proof and host CAS; never mint or rewrite custody."""
    def snapshot():
        return _snapshot(path, compose, service, container, stopped, _run=_run,
                         env_file=env_file, execution_env=execution_env)
    before = snapshot()
    receipt = before[0]
    preview, auth = receipt['preview'], receipt['authorization']
    _verify_unconsumed_death(receipt, stopped, _run=_run, service=service, container=container,
        env_file=env_file, execution_env=execution_env)
    if auth['successor']['image_id'] != stopped['image_id']:
        # An image update is bounded to unchanged config bytes. Probe the exact
        # new image with the old bind sources so generic incumbent custody stays
        # strict; the actual successor's source relocation is separately bound.
        require(auth['successor']['configuration_revision'] == auth['expected_configuration_revision'])
        probe = strict_json(read_private_auth_file(preview['compose_path'], max_bytes=2*1024**2))
        probe['services'][service]['image'] = before[2]['services'][service]['image']
        from .router.maintenance import _publish
        with tempfile.TemporaryDirectory(prefix='anvil-router-successor-probe-') as directory:
            probe_path = Path(directory) / 'compose.json'
            _publish(probe_path, probe)
            lifecycle.require_router_offline(str(probe_path), service, container=container, _run=_run,
                env_file=env_file, execution_env=execution_env)
    require(snapshot() == before)
    return receipt


def _verify_unconsumed_death(receipt, stopped, *, _run, service, container, env_file,
                             execution_env):
    preview, auth = receipt['preview'], receipt['authorization']
    native = preview['native']
    marker = {'schema': 'router-current-stop-permission/v1', 'authorization': auth,
              'native_sha256': digest(native)}
    # This helper runs the predecessor image and only reads its retained store.
    code = ("import json;from pathlib import Path;"
        "from anvil_serving.router.config import load_server_config;"
        "from anvil_serving.router.keys import KeyStore,_private_json;"
        "from anvil_serving.router.container_owner import binding,closure,digest,location,require,validate;"
        "from anvil_serving.router_manage import DEFAULT_INSTALLED_CONFIG,_offline_router_start;"
        "settings=load_server_config(DEFAULT_INSTALLED_CONFIG);store=KeyStore(settings.api_keys_path)\n"
        "with store._offline_custody(settings,allow_retained=True):\n"
        f" record=validate(_private_json(location(store,{native['observation']['run_id']!r})))\n"
        f" marker=_private_json(Path(str(store.path)+'.maintenance-current-stops')/({auth['operation_id']!r}+'.json'))\n"
        f" require(marker=={marker!r} and binding(store)=={native['observation']['store_binding']!r})\n"
        f" require(closure(store)['closure']=={native['observation']['closure']!r})\n"
        " require(record['death']['closure_sha256']==digest(closure(store)))\n"
        "_offline_router_start()\nprint(json.dumps(record))\n")
    record = validate(lifecycle._offline_compose_run(preview['compose_path'], service, container, code,
        _run=_run, env_file=env_file, execution_env=execution_env, _owner_state_only=True))
    require(record['phase'] == 'ready' and record['anchor'] == native['record']['anchor']
        and record['death']['stopped'] == stopped and digest(record['death']) == receipt['death_sha256']
        and record['death']['closure_generation'] == native['observation']['closure']['generation']
        and record['death']['closure_consumed'] is False)


def require_predecessor(path, compose, service, container, stopped, *, _run, env_file=None,
                        execution_env=None):
    """Validate and select only the byte-pinned predecessor as an explicit rollback."""
    before = _snapshot(path, compose, service, container, stopped, _run=_run,
        env_file=env_file, execution_env=execution_env, rollback_to_predecessor=True)
    receipt = before[0]
    _verify_unconsumed_death(receipt, stopped, _run=_run, service=service, container=container,
        env_file=env_file, execution_env=execution_env)
    lifecycle.require_router_offline(compose, service, container=container, _run=_run,
        env_file=env_file, execution_env=execution_env)
    require(_snapshot(path, compose, service, container, stopped, _run=_run,
        env_file=env_file, execution_env=execution_env, rollback_to_predecessor=True) == before)
    return receipt


def _running_successor_code(receipt, docker):
    """Return a bounded native readback for an already-started exact successor."""
    native = receipt['preview']['native']
    auth = receipt['authorization']
    old_record = native['record']
    old_closure = {'schema': 'router-admission/v1', 'owner_id': native['observation']['owner_id'],
                   'closure': native['observation']['closure']}
    marker = {'schema': 'router-current-stop-permission/v1', 'authorization': auth,
              'native_sha256': digest(native)}
    return ("import json,os;from pathlib import Path;"
        "from anvil_serving.router.config import load_server_config;"
        "from anvil_serving.router.keys import KeyStore,_private_json;"
        "from anvil_serving.router.usage_store import UsageStore;"
        "from anvil_serving.router.container_owner import binding,closure,digest,live_record,live_writer,location,require,validate;"
        "from anvil_serving.router_manage import DEFAULT_INSTALLED_CONFIG,_transition_request;"
        "settings=load_server_config(DEFAULT_INSTALLED_CONFIG);store=KeyStore(settings.api_keys_path);"
        "usage=UsageStore(store);usage.owner_config=settings\n"
        "with live_writer(store):\n"
        f" proposed=live_record(usage,{docker!r})\n"
        f" prior=validate(_private_json(location(store,{old_record['anchor']['run_id']!r})))\n"
        f" marker=_private_json(Path(str(store.path)+'.maintenance-current-stops')/({auth['operation_id']!r}+'.json'))\n"
        f" require(marker=={marker!r} and binding(store)=={native['observation']['store_binding']!r})\n"
        f" require(prior['phase']=='transferred' and prior['anchor']=={old_record['anchor']!r})\n"
        f" require(digest(prior['death'])=={receipt['death_sha256']!r} and prior['death']['stopped']=={receipt['stopped']!r})\n"
        f" require(prior['death']['closure_sha256']==digest({old_closure!r}) and prior['death']['closure_generation']=={native['observation']['closure']['generation']!r} and prior['death']['closure_consumed'] is False)\n"
        " current=closure(store)['closure']\n"
        " require(type(current) is dict and current['consumed'] is False)\n"
        " token=os.environ.get(settings.auth_env or '', '')\n"
        " require(bool(token))\n"
        " status=_transition_request('status',scope='router',router_url='http://127.0.0.1:8000',env={'ANVIL_ROUTER_TOKEN':token}).get('result',{})\n"
        " expected_counts={'chat','purpose','audio','memory','media','internal','delivery','maintenance'}\n"
        " counts=status.get('counts')\n"
        " require(status.get('state')=='quiesced' and status.get('durable') is True and status.get('cutover_pending') is False and status.get('generation')==current['generation'] and status.get('configuration_revision')==current['configuration_revision'] and status.get('roster_revision')==current['roster_revision'])\n"
        " require(type(counts) is dict and set(counts)==expected_counts and all(type(value) is int and value==0 for value in counts.values()))\n"
        " transfer=prior['transfer']\n"
        " require(transfer=={'successor_run_id':proposed['anchor']['run_id'],'successor_roster_revision':proposed['anchor']['roster_revision'],'successor_configuration_revision':proposed['anchor']['configuration_revision'],'successor_generation':current['generation']})\n"
        " require(current['generation']==(prior['death']['closure_generation'] or 0)+1 and current['roster_revision']==proposed['anchor']['roster_revision'] and current['configuration_revision']==proposed['anchor']['configuration_revision'])\n"
        " path=location(store,proposed['anchor']['run_id'])\n"
        " existing=validate(_private_json(path)) if os.path.lexists(path) else None\n"
        " phase='absent' if existing is None else existing['phase']\n"
        " require(phase in {'absent','live-pending','live'} and (existing is None or existing['anchor']==proposed['anchor']))\n"
        " print(json.dumps({'ok':True,'phase':phase,'run_id':proposed['anchor']['run_id']}),flush=True)\n")


def _verify_running_successor(receipt, incarnation, *, _run, env_file, execution_env,
                              require_live_anchor=False):
    code = _running_successor_code(receipt, incarnation)
    result = _run(['docker', 'exec', incarnation['container_id'], 'python', '-c', code],
                  capture_output=True, text=True, encoding='utf-8', timeout=15, env=execution_env)
    require(result.returncode == 0 and len(result.stdout or '') <= 1024)
    value = strict_json((result.stdout or '').encode())
    require(type(value) is dict and set(value) == {'ok', 'phase', 'run_id'} and value['ok'] is True
            and value['phase'] in {'absent', 'live-pending', 'live'}
            and (not require_live_anchor or value['phase'] == 'live'))
    from .router.usage_store import _uuid
    _uuid(value['run_id'])
    return value


def require_running_successor(path, compose, service, container, *, _run, env_file=None,
                             execution_env=None, require_live_anchor=False):
    """Validate an already-running exact successor without stopping or recreating it."""
    receipt = completed_receipt(path)
    stopped = receipt['stopped']
    auth = receipt['authorization']
    require(compose == auth['successor']['compose'] and service == lifecycle.DEFAULT_SERVICE
            and container == lifecycle.DEFAULT_CONTAINER)
    before_incarnation = lifecycle._container_incarnation(container, _run=_run)
    require(before_incarnation['image_id'] == auth['successor']['image_id'])
    before = _snapshot(path, compose, service, container, stopped, _run=_run,
        env_file=env_file, execution_env=execution_env, running_incarnation=before_incarnation)
    proof = _verify_running_successor(receipt, before_incarnation, _run=_run,
        env_file=env_file, execution_env=execution_env, require_live_anchor=require_live_anchor)
    require(_snapshot(path, compose, service, container, stopped, _run=_run,
        env_file=env_file, execution_env=execution_env, running_incarnation=before_incarnation) == before)
    return {'receipt': receipt, 'incarnation': before_incarnation, **proof}
