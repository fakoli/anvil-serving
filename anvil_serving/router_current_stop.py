"""Host-only bootstrap from one completed, acknowledged current-owner stop."""
import hashlib
from copy import deepcopy
from pathlib import Path
import time
import tomllib

from . import router_manage as lifecycle
from .control_plane.mcp.auth_file import read_private_auth_file
from .observability.dashboard.contracts import strict_json
from .operator_config import _read_bounded
from .router.container_owner import digest, docker_identity, hex_id, require, validate
from .router.maintenance import authorization, _current_observation, _current_successor


def completed_receipt(path, stopped):
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
    docker_identity(stopped, stopped=True)
    require(receipt['stopped'] == stopped
        and all(stopped[k] == v for k, v in docker_identity(preview['incarnation']).items()))
    _current_observation(preview['native'], auth, preview['incarnation'], at=receipt['acknowledged_at'])
    return receipt


def _snapshot(path, compose, service, container, stopped, *, _run, env_file, execution_env):
    receipt = completed_receipt(path, stopped)
    preview, auth = receipt['preview'], receipt['authorization']
    successor = auth['successor']
    require(compose == successor['compose'] and service == lifecycle.DEFAULT_SERVICE
        and container == lifecycle.DEFAULT_CONTAINER)
    environment = preview.get('environment')
    if environment is None:
        require(env_file is None)
    else:
        require(type(environment) is dict and set(environment) == {'file', 'sha256'}
            and type(environment['file']) is str and Path(environment['file']).is_absolute()
            and '..' not in Path(environment['file']).parts and env_file == environment['file'])
        hex_id(environment['sha256'])
        require(hashlib.sha256(read_private_auth_file(env_file, max_bytes=65536)).hexdigest() == environment['sha256'])
    for file, expected in ((preview['compose_path'], preview['compose_sha256']),
                           (compose, successor['compose_sha256'])):
        require(hashlib.sha256(read_private_auth_file(file, max_bytes=2*1024**2)).hexdigest() == expected)
    candidate = _current_successor(successor, preview['incarnation'], _run=_run)
    old = lifecycle._offline_compose_roster(preview['compose_path'], service, container, _run=_run,
        env_file=env_file, execution_env=execution_env, _metadata_only=True, _owner_state_only=True)
    require(old['owner_storage']['configuration_revision'] == auth['expected_configuration_revision'])
    selected = candidate['services'][service]
    # Config aliases are declared mounts of the same old/new file. Preserve
    # their exact destinations and every other runtime/mount declaration.
    def normalized(rendered):
        result = deepcopy(rendered)
        mounts = result['services'][service]['volumes']
        source = next(m['source'] for m in mounts if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
        aliases = [m for m in mounts if m.get('type') == 'bind'
                   and m.get('read_only') is True and m['source'] == source]
        require(1 <= len(aliases) <= 2 and len({m['target'] for m in aliases}) == len(aliases))
        for mount in mounts:
            if mount.get('type') == 'bind' and mount.get('read_only') is True and mount['source'] == source:
                mount['source'] = '<acknowledged-router-config>'
        return result
    require(normalized(candidate) == normalized(old['rendered'])
        and successor['image_id'] == stopped['image_id'])
    mounts = selected['volumes']
    config = next(m for m in mounts if m['target'] == lifecycle.DEFAULT_INSTALLED_CONFIG)
    def read(argv):
        result = _run(argv, capture_output=True, text=True, timeout=10, env=execution_env)
        require(result.returncode == 0 and len(result.stdout or '') <= lifecycle.MAX_COMPOSE_FILE_BYTES)
        return result.stdout or ''
    scope = lifecycle._owner_storage_scope(candidate, mounts, config, {}, read)
    require(scope['configuration_revision'] == successor['configuration_revision']
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
    require(lifecycle._container_incarnation(container, _run=_run, stopped=True) == stopped)
    return receipt, old, candidate, scope


def require_successor(path, compose, service, container, stopped, *, _run, env_file=None, execution_env=None):
    """Read-only native proof and host CAS; never mint or rewrite custody."""
    def snapshot():
        return _snapshot(path, compose, service, container, stopped, _run=_run,
                         env_file=env_file, execution_env=execution_env)
    before = snapshot()
    receipt = before[0]
    preview, auth = receipt['preview'], receipt['authorization']
    native = preview['native']
    marker = {'schema': 'router-current-stop-permission/v1', 'authorization': auth,
              'native_sha256': digest(native)}
    # Existing image primitives only: no package upgrade or proof publication.
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
    require(snapshot() == before)
    return receipt
