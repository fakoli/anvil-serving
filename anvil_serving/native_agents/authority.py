"""Managed, expiring projection of Connect's native Pi resource grants."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time

from ..connect import application_roles, manage, users
from ..connect.render import _authelia
from .auth import MAX_PRINCIPALS


def project(manifest, *, resource, output, reader_gid, apply=False, runner=None, clock=time.time):
    users._require_root()
    data = application_roles._read_declaration(manifest)
    declared = {item['rule']['id']: item['rule'] for item in data['gateway']['gateway']['resources']}
    if (resource not in declared or declared[resource]['access'] != 'browser'
            or type(reader_gid) is not int or not 1 <= reader_gid <= 2**31 - 1):
        raise manage.ManageError('Declare a browser resource and dedicated native reader group.')
    path = Path(output)
    if (not path.is_absolute() or any((p / '.git').exists() for p in (path.parent, *path.parents))
            or path == Path(manifest) or path == Path(data['config_root'])
            or Path(data['config_root']) in path.parents):
        raise manage.ManageError('Native authority must use a protected path outside rendered state and Git.')
    manage._safe_root_ancestors(path)
    if not path.parent.is_dir():
        raise manage.ManageError('Prepare the protected native authority directory first.')
    if os.path.lexists(path):
        from .auth import strict_json
        existing = strict_json(application_roles._protected_file(str(path), maximum=65536))
        if (type(existing) is not dict or existing.get('schema') != 'anvil-native-agents-authority/v1'
                or existing.get('issuer') != data['gateway']['oidc']['issuer']
                or existing.get('resource') != 'native-agents' or existing.get('source_resource') != resource):
            raise manage.ManageError('Existing output is not this native authority projection.')
    root = Path(data['config_root'])
    with manage._deployment_lock(root):
        manage._require_no_authelia_upgrade(root)
        config = root / 'authelia/configuration.yml'
        if (manage._read_regular(config, users._MAX_FILE) != _authelia(data).encode()
                or manage._digest(Path(data['components']['authelia'])) != manage._component_lock()['authelia']):
            raise manage.ManageError('Installed identity authority differs from its declaration.')
        started = clock()
        def bounded_runner(argv, timeout, identity=None):
            remaining = 20 - (clock() - started)
            if remaining <= 0:
                raise manage.ManageError('Native authority projection deadline exceeded.')
            return manage._run(runner, argv, min(timeout, remaining), identity)
        first = application_roles.roster(data, manifest, resource, bounded_runner)
        latest = application_roles.roster(data, manifest, resource, bounded_runner)
        if first != latest or application_roles._read_declaration(manifest) != data or clock() - started >= 20:
            raise manage.ManageError('Native authority changed while projecting grants.')
        principals = {entry['principal']: int(entry['generation']) for entry in latest
                      if entry['role'] in {'member', 'admin'} and entry['generation'] is not None}
        if len(principals) > MAX_PRINCIPALS:
            raise manage.ManageError('Native Agents authority exceeds its 64-principal capacity; publication refused.')
        issued = int(started)
        value = {'schema':'anvil-native-agents-authority/v1', 'issuer':data['gateway']['oidc']['issuer'],
                 'resource':'native-agents', 'source_resource':resource, 'issued_at':issued, 'expires_at':issued + 30, 'principals':principals}
        if apply:
            # Set final custody before atomic publication so live readers cannot
            # see an unreadable intermediate file during refresh.
            temporary = path.with_name('.native-authority-' + secrets.token_hex(16))
            try:
                manage._write_atomic(temporary, (json.dumps(value, sort_keys=True) + '\n').encode(), 0o600)
                os.chown(temporary, 0, reader_gid)
                os.chmod(temporary, 0o640)
                os.replace(temporary, path)
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                if temporary.exists(): temporary.unlink()
        return {'schema':'anvil-native-agents-projection-result/v1', 'applied':apply,
                'resource':resource, 'principals':len(principals), 'lifetime_seconds':30}
