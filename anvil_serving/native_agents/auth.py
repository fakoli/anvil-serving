"""Native OIDC admission with fresh, Connect-owned workspace authority.

Provider credentials remain on the host. Tokens are never decoded as authority,
logged, written to receipts, or accepted solely on the strength of userinfo.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

from .types import NativeAdmission

ROOT_UID = 0
MAX_AUTHORITY_AGE = 30
PROVIDER_RECHECK = 15
TOKENS_PER_PRINCIPAL = 8
MAX_PRINCIPALS = 64
MAX_HELD_TOKENS = MAX_PRINCIPALS * TOKENS_PER_PRINCIPAL


class AdmissionDenied(ValueError):
    def __init__(self):
        super().__init__("Native admission unavailable")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AdmissionDenied()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AdmissionDenied()))
    except (ValueError, UnicodeError, TypeError):
        raise AdmissionDenied() from None


def protected_read(filename, maximum):
    """Root-owned, non-symlink custody; a service group may read the file."""
    path = Path(filename)
    if not path.is_absolute():
        raise AdmissionDenied()
    try:
        for parent in reversed(path.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022:
                raise AdmissionDenied()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != ROOT_UID or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) not in {0o400, 0o440, 0o600, 0o640}
                    or not 1 <= before.st_size <= maximum):
                raise AdmissionDenied()
            chunks, size = [], 0
            while size <= maximum:
                part = os.read(fd, min(65536, maximum + 1 - size))
                if not part:
                    break
                chunks.append(part); size += len(part)
            after = os.fstat(fd)
            if (size > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise AdmissionDenied()
            return b''.join(chunks)
        finally:
            os.close(fd)
    except OSError:
        raise AdmissionDenied() from None


def validate_config(value):
    fields = {'issuer', 'introspection_endpoint', 'userinfo_endpoint', 'introspection_client_id',
              'secret_file', 'native_client_ids', 'authority_file', 'resource', 'source_resource'}
    if type(value) is not dict or set(value) != fields or value['resource'] != 'native-agents':
        raise AdmissionDenied()
    if type(value['source_resource']) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value['source_resource']):
        raise AdmissionDenied()
    def endpoint(raw):
        if type(raw) is not str or len(raw) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise AdmissionDenied()
        try:
            p = urlsplit(raw)
            if (p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None
                    or p.fragment or p.query or p.port is not None and not 1 <= p.port <= 65535):
                raise AdmissionDenied()
            return p
        except ValueError:
            raise AdmissionDenied() from None
    issuer = endpoint(value['issuer'])
    for name in ('introspection_endpoint', 'userinfo_endpoint'):
        p = endpoint(value[name])
        if (p.hostname, p.port or 443) != (issuer.hostname, issuer.port or 443) or not p.path:
            raise AdmissionDenied()
    ids = value['native_client_ids']
    if (type(ids) is not list or not 1 <= len(ids) <= 16
            or any(type(item) is not str or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', item) for item in ids)
            or len(set(ids)) != len(ids)
            or type(value['introspection_client_id']) is not str
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', value['introspection_client_id'])):
        raise AdmissionDenied()
    for name in ('secret_file', 'authority_file'):
        if type(value[name]) is not str or not Path(value[name]).is_absolute() or '\x00' in value[name]:
            raise AdmissionDenied()
    return {**value, 'native_client_ids': tuple(ids)}


def provider_request(url, method, headers, body=None):
    """Fixed HTTPS endpoint, no proxies/redirects, total wall-clock bound."""
    p = urlsplit(url)
    connection = http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=5)
    held_socket = [None]
    expired = threading.Event()
    def cancel():
        expired.set()
        sock = connection.sock or held_socket[0]
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(6, cancel); timer.daemon = True; timer.start()
    try:
        connection.connect(); held_socket[0] = connection.sock
        if expired.is_set():
            raise AdmissionDenied()
        connection.request(method, p.path or '/', body=body, headers={
            'User-Agent': 'AnvilConnect-NativeAgents/1.0',
            'Accept': 'application/json', **headers})
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536 or expired.is_set():
            raise AdmissionDenied()
        value = strict_json(raw)
        if type(value) is not dict:
            raise AdmissionDenied()
        return value
    except (OSError, http.client.HTTPException, ValueError):
        raise AdmissionDenied() from None
    finally:
        timer.cancel(); connection.close()


def authority(config, read, now):
    value = strict_json(read(config['authority_file'], 65536))
    if (type(value) is not dict or set(value) != {'schema','issuer','resource','source_resource','issued_at','expires_at','principals'}
            or value['schema'] != 'anvil-native-agents-authority/v1' or value['issuer'] != config['issuer']
            or value['resource'] != config['resource'] or value['source_resource'] != config['source_resource']
            or type(value['issued_at']) is not int
            or type(value['expires_at']) is not int or not 0 < value['expires_at'] - value['issued_at'] <= MAX_AUTHORITY_AGE
            or value['issued_at'] > now + 2 or not value['issued_at'] - 2 <= now < value['expires_at']
            or type(value['principals']) is not dict or len(value['principals']) > MAX_PRINCIPALS):
        raise AdmissionDenied()
    for principal, generation in value['principals'].items():
        if (not re.fullmatch(r'human:[0-9a-f]{64}', principal) or type(generation) is not int
                or not 1 <= generation < 2**64):
            raise AdmissionDenied()
    return value['principals']


@dataclass
class _HeldToken:
    admission: NativeAdmission
    token: str = field(repr=False)
    checked_at: float = 0
    lock: object = field(default_factory=threading.Lock, repr=False)


class NativeAuthenticator:
    def __init__(self, config, *, read=protected_read, request=provider_request, clock=time.time):
        self.config = validate_config(config)
        self.read, self.request, self.clock = read, request, clock
        self._held = OrderedDict()
        self._lock = threading.RLock()
        self._provider_slots = threading.BoundedSemaphore(8)
        self._pending = {}

    def _provider_identity(self, token):
        if not self._provider_slots.acquire(timeout=1):
            raise AdmissionDenied()
        try:
            return self._read_provider_identity(token)
        finally:
            self._provider_slots.release()

    def _read_provider_identity(self, token):
        secret = self.read(self.config['secret_file'], 4096).decode('ascii').rstrip('\n')
        if not secret or any(ord(c) < 33 or ord(c) > 126 for c in secret):
            raise AdmissionDenied()
        basic = base64.b64encode((quote(self.config['introspection_client_id'], safe='') + ':'
                                  + quote(secret, safe='')).encode()).decode()
        value = self.request(self.config['introspection_endpoint'], 'POST',
            {'Authorization': 'Basic ' + basic, 'Content-Type':'application/x-www-form-urlencoded', 'Accept':'application/json'},
            urlencode({'token':token, 'token_type_hint':'access_token'}).encode())
        now = self.clock()
        if (value.get('active') is not True or value.get('client_id') not in self.config['native_client_ids']
                or value.get('iss') != self.config['issuer']
                or type(value.get('exp')) is not int or not now < value['exp'] <= now + 86400
                or type(value.get('scope')) is not str or 'openid' not in value['scope'].split()
                or type(value.get('sub')) is not str or not 1 <= len(value['sub'].encode()) <= 1024):
            raise AdmissionDenied()
        user = self.request(self.config['userinfo_endpoint'], 'GET',
                            {'Authorization':'Bearer ' + token, 'Accept':'application/json'})
        if user.get('sub') != value['sub']:
            raise AdmissionDenied()
        principal = 'human:' + hashlib.sha256((self.config['issuer'] + '\0' + value['sub']).encode()).hexdigest()
        return principal, value['exp']

    def authorize(self, bearer):
        if (type(bearer) is not str or not 1 <= len(bearer) <= 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in bearer)):
            raise AdmissionDenied()
        digest = hashlib.sha256(bearer.encode()).hexdigest()
        try:
            with self._lock:
                held = self._held.get(digest)
            if held is not None and self.recheck(held.admission):
                return held.admission
            with self._lock:
                pending = self._pending.get(digest)
                leader = pending is None
                if leader:
                    if len(self._pending) >= 64:
                        raise AdmissionDenied()
                    pending = threading.Event()
                    self._pending[digest] = pending
            if not leader:
                if not pending.wait(14):
                    raise AdmissionDenied()
                with self._lock:
                    held = self._held.get(digest)
                if held is None or not self.recheck(held.admission):
                    raise AdmissionDenied()
                return held.admission
            try:
                # A prior leader may have finished between our initial cache
                # lookup and reserving this miss. Do not duplicate verification.
                with self._lock:
                    held = self._held.get(digest)
                if held is not None and self.recheck(held.admission):
                    return held.admission
                return self._authorize_new(bearer, digest)
            finally:
                with self._lock:
                    self._pending.pop(digest, None)
                    pending.set()
        except (ValueError, TypeError, KeyError, UnicodeError, OSError):
            raise AdmissionDenied() from None

    def _authorize_new(self, bearer, digest):
        try:
            now = self.clock()
            authority(self.config, self.read, now)
            # No shared cache lock is held over provider I/O. A slow principal
            # cannot serialize all accounts and their active stream checks.
            principal, expiry = self._provider_identity(bearer)
            with self._lock:
                # Re-read after network calls: a revoke during introspection is denial.
                grants = authority(self.config, self.read, self.clock())
                generation = grants.get(principal)
                if generation is None:
                    raise AdmissionDenied()
                admission = NativeAdmission(principal=principal, generation=generation, expires_at=expiry,
                                            resource='native-agents', credential_id=digest)
                for key in list(self._held):
                    if self._held[key].admission.expires_at <= self.clock():
                        del self._held[key]
                owned = [key for key, value in self._held.items() if value.admission.principal == principal and key != digest]
                while len(owned) >= TOKENS_PER_PRINCIPAL:
                    del self._held[owned.pop(0)]
                if digest not in self._held and len(self._held) >= MAX_HELD_TOKENS:
                    raise AdmissionDenied()
                self._held[digest] = _HeldToken(admission, bearer, self.clock())
                self._held.move_to_end(digest)
                return admission
        except (ValueError, TypeError, KeyError, UnicodeError, OSError):
            raise AdmissionDenied() from None

    def recheck(self, admission):
        digest = getattr(admission, 'credential_id', '')
        with self._lock:
            held = self._held.get(digest)
        if held is None:
            return False
        with held.lock:
            try:
                now = self.clock()
                if (held.admission != admission or not math.isfinite(now)
                        or not now < admission.expires_at or now < held.checked_at - 2
                        or authority(self.config, self.read, now).get(admission.principal) != admission.generation):
                    raise AdmissionDenied()
                if now - held.checked_at >= PROVIDER_RECHECK:
                    principal, expiry = self._provider_identity(held.token)
                    if principal != admission.principal or expiry != admission.expires_at:
                        raise AdmissionDenied()
                    held.checked_at = self.clock()
                    if authority(self.config, self.read, self.clock()).get(principal) != admission.generation:
                        raise AdmissionDenied()
                with self._lock:
                    return self._held.get(digest) is held
            except (ValueError, TypeError, KeyError, UnicodeError, OSError, AttributeError):
                with self._lock:
                    if self._held.get(digest) is held:
                        self._held.pop(digest, None)
                return False
