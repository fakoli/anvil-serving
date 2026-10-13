"""Bounded projection of Connect authority through explicit native app owners.

The adapter owns native identity linkage, role updates and session revocation.
This module never handles application credentials or writes application storage.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time
import uuid

from . import manage, users
from .config import _json_load
from .render import _authelia

SCHEMA = 'anvil-connect.application-roles/v1'
RESULT_SCHEMA = 'anvil-connect.application-role-result/v1'
MAX_ACCOUNTS = 64
MAX_INPUT = 64 * 1024
MAX_OUTPUT = 16 * 1024
MAX_ADAPTER_BYTES = 1024 * 1024
ROOT_UID = 0


def _error(*, partial=False):
    return manage.ManageError('Application role reconciliation did not complete; inspect adapter custody and native authority.',
                              may_have_executed=partial)


def _protected_file(path: str, *, maximum: int, digest: str | None = None, configuration=False) -> bytes:
    target = Path(path)
    manage._safe_root_ancestors(target)
    if any((parent / '.git').exists() for parent in target.parents):
        raise _error()
    descriptor = os.open(target, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size < 1 or info.st_size > maximum
                or configuration and stat.S_IMODE(info.st_mode) != 0o600):
            raise _error()
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        latest = os.fstat(descriptor)
        if (len(raw) > maximum or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != (latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns)
                or digest is not None and hashlib.sha256(raw).hexdigest() != digest):
            raise _error()
        return bytes(raw)
    finally:
        os.close(descriptor)


def _read_declaration(manifest):
    raw = _protected_file(manifest, maximum=1024 * 1024)
    from .config import parse_manifest_text
    return parse_manifest_text(raw.decode('utf-8'))


def verify_binding(binding):
    _protected_file(binding['adapter_path'], maximum=MAX_ADAPTER_BYTES, digest=binding['adapter_sha256'])
    _protected_file(binding['configuration_path'], maximum=MAX_INPUT,
                    digest=binding['configuration_sha256'], configuration=True)


def roster(data, manifest, resource, runner):
    """Read exact public OIDC identities and their native resource authority."""
    raw, _ = users._read_users(data)
    accounts = users._database(raw)['users']
    config = Path(data['config_root']) / 'authelia/configuration.yml'
    identifiers = users._oidc_identifiers(data, config, runner, missing_ok=True)
    records = [{**row, 'identifier': str(uuid.UUID(row['identifier']))}
               for row in identifiers if row['service'] == 'openid' and row['sector_id'] == '']
    if len(accounts) > MAX_ACCOUNTS or len(records) > MAX_ACCOUNTS:
        raise _error()
    operators = data['gateway']['gateway'].get('browser_administration', {}).get('operators', [])
    declared = {item['rule']['id'] for item in data['gateway']['gateway']['resources']}
    seen_names, seen_subjects = set(), set()
    result = []
    for record in sorted(records, key=lambda row: row['identifier']):
        username, subject = record['username'], record['identifier']
        if username in seen_names or subject in seen_subjects:
            raise _error()
        seen_names.add(username)
        seen_subjects.add(subject)
        principal = users._principal(data['gateway']['oidc']['issuer'], subject)
        operation = 'operator-inspect' if principal in operators else 'human-inspect'
        response = users._human_admin_read(data, manifest, {'operation': operation, 'principal': principal}, runner)
        if response.get('operation') != operation or type(response.get('found')) is not bool:
            raise _error()
        if not response['found'] and response.get('human') is not None:
            raise _error()
        entry = {'subject': subject, 'principal': principal, 'generation': None, 'role': 'none'}
        if response['found']:
            human = response.get('human')
            # Native legacy/browser-created records can omit username. The
            # principal remains bound to the exact exported OIDC issuer/subject.
            if (type(human) is not dict or human.get('id') != principal
                    or 'username' in human and human['username'] != username
                    or type(human.get('generation')) is not int or not 1 <= human['generation'] < 2**64
                    or type(human.get('disabled')) is not bool or type(human.get('resources')) is not list):
                raise _error()
            resources, application_roles = human['resources'], human.get('application_roles', {})
            if (any(type(item) is not str or item not in declared for item in resources)
                    or len(resources) != len(set(resources)) or type(application_roles) is not dict
                    or any(key not in resources or value not in ('member', 'admin') for key, value in application_roles.items())):
                raise _error()
            entry['generation'] = str(human['generation'])
            account = accounts.get(username)
            if (account is not None and account.get('disabled', False) is False and not human['disabled']
                    and not human.get('deletion_request') and resource in resources):
                entry['role'] = application_roles.get(resource, 'none')
        result.append(entry)
    return result


def invoke(adapter_path, payload, *, timeout=45):
    """Fixed isolated Python argv; bounded duplex pipes and a killable group."""
    if type(payload) is not bytes or len(payload) > MAX_INPUT or not 0 < timeout <= 45:
        raise _error()
    process = subprocess.Popen(('/usr/bin/python3', '-I', adapter_path), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
    selector = selectors.DefaultSelector()
    streams = (process.stdin, process.stdout, process.stderr)
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    offset = 0
    deadline = time.monotonic() + timeout
    try:
        for stream, name, event in ((process.stdout, 'stdout', selectors.EVENT_READ),
                                    (process.stderr, 'stderr', selectors.EVENT_READ),
                                    (process.stdin, 'stdin', selectors.EVENT_WRITE)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _error()
            for key, _ in selector.select(min(remaining, .05)):
                stream, name = key.fileobj, key.data
                if name == 'stdin':
                    try:
                        if offset < len(payload):
                            offset += os.write(stream.fileno(), payload[offset:offset + 8192])
                        if offset == len(payload):
                            selector.unregister(stream)
                            stream.close()
                    except BrokenPipeError:
                        selector.unregister(stream)
                        stream.close()
                        raise _error()
                else:
                    chunk = os.read(stream.fileno(), 8192)
                    if not chunk:
                        selector.unregister(stream)
                    elif len(buffers[name]) + len(chunk) > MAX_OUTPUT:
                        raise _error()
                    else:
                        buffers[name].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise _error()
        return bytes(buffers['stdout'])
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise manage.ManageError('Application role adapter exceeded its bound or failed.') from None
    finally:
        selector.close()
        for stream in streams:
            if not stream.closed:
                stream.close()


def response(raw, request):
    try:
        value = _json_load(raw.decode('utf-8'))
        supplied = _json_load(request.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise _error() from None
    counts = {'checked', 'changed', 'pending_first_login', 'retired', 'sessions_revoked', 'failed'}
    if (type(value) is not dict or set(value) != counts | {'schema', 'resource', 'mode', 'request_sha256'}
            or value['schema'] != RESULT_SCHEMA or value['resource'] != supplied['resource']
            or value['mode'] != supplied['mode'] or value['request_sha256'] != hashlib.sha256(request).hexdigest()
            or any(type(value[key]) is not int or not 0 <= value[key] <= 4096 for key in counts)
            or value['checked'] != len(supplied['accounts']) + value['retired']
            or any(value[key] > value['checked'] for key in counts - {'checked'})):
        raise _error()
    return value


def sync_roles(manifest=users.DEFAULT_MANIFEST, *, apply=False, runner=None, adapter_runner=None):
    """Reconcile configured adapters; return only resource IDs and counts."""
    users._require_root()
    data = _read_declaration(manifest)
    bindings = data.get('application_role_bindings', [])
    result = {'schema': 'anvil-connect.application-role-sync/v1', 'enabled': bool(bindings),
              'applied': False, 'bindings': [], 'complete': True}
    if not bindings:
        return result
    root = Path(data['config_root'])
    deadline = time.monotonic() + 90
    attempted = False
    def bounded_runner(argv, timeout, identity=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _error()
        return manage._run(runner, argv, min(timeout, remaining), identity)
    try:
        with manage._deployment_lock(root, deadline=deadline):
            manage._require_no_authelia_upgrade(root)
            manage._verify_owned_tree(root)
            if (manage._read_regular(root / 'authelia/configuration.yml', users._MAX_FILE) != _authelia(data).encode()
                    or manage._digest(Path(data['components']['authelia'])) != manage._component_lock()['authelia']):
                raise _error()
            for binding in bindings:
                verify_binding(binding)
                first = roster(data, manifest, binding['resource'], bounded_runner)
                latest = roster(data, manifest, binding['resource'], bounded_runner)
                if latest != first or _read_declaration(manifest) != data:
                    raise _error()
                verify_binding(binding)
                payload = (json.dumps({'schema': SCHEMA, 'mode': 'apply' if apply else 'check',
                    'resource': binding['resource'], 'issuer': data['gateway']['oidc']['issuer'],
                    'configuration_path': binding['configuration_path'],
                    'configuration_sha256': binding['configuration_sha256'], 'complete': True,
                    'accounts': latest}, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
                remaining = min(45, deadline - time.monotonic())
                if remaining <= 0:
                    raise _error()
                attempted = apply
                raw = (adapter_runner or invoke)(binding['adapter_path'], payload, timeout=remaining)
                observed = response(raw, payload)
                result['bindings'].append({key: observed[key] for key in ('resource', 'checked', 'changed',
                    'pending_first_login', 'retired', 'sessions_revoked', 'failed')})
                verify_binding(binding)
                if (roster(data, manifest, binding['resource'], bounded_runner) != latest
                        or _read_declaration(manifest) != data or observed['failed']):
                    raise _error()
                result['applied'] = apply
                if observed['pending_first_login']:
                    result['complete'] = False
    except Exception as exc:
        result['complete'] = False
        error = _error(partial=attempted)
        error.recovery = result
        raise error from exc
    return result
