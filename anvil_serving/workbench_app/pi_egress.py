"""Managed, exact-provider egress for isolated Pi containers.

Only the operator configuration chooses endpoints. Setup freezes DNS addresses
and an immutable proxy image; recovery verifies the effective network and proxy.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import tempfile
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.parse import urlsplit


class PiEgressError(RuntimeError):
    pass


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _address(value):
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) in {"100.100.100.200", "168.63.129.16"}:
        raise PiEgressError("Pi egress excludes loopback, metadata, link-local and multicast addresses")
    return str(ip)


def targets(endpoints, *, resolve=socket.getaddrinfo):
    result = []
    if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= 8:
        raise PiEgressError("Declare one to eight exact provider origins")
    for endpoint in endpoints:
        url = urlsplit(endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment or "*" in url.hostname:
            raise PiEgressError("Pi egress accepts exact HTTP(S) origins only")
        port = url.port or (443 if url.scheme == "https" else 80)
        addresses = sorted({_address(row[4][0]) for row in resolve(url.hostname, port, type=socket.SOCK_STREAM)})
        if not addresses:
            raise PiEgressError("Pi provider origin did not resolve")
        result.append({"scheme": url.scheme, "hostname": url.hostname, "port": port, "addresses": addresses})
    return result


class PiEgress:
    def __init__(self, config, *, run=subprocess.run):
        self.config, self.run = config, run
        self.engine = config["engine_binary"]
        self.root = Path(config["state_root"]) / "egress"

    def _command(self, *args):
        result = self.run([self.engine, *args], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise PiEgressError("Pi egress engine operation failed; no unverified egress is enabled")
        return result.stdout

    def _inspect(self, kind, identity):
        if kind == "container":
            return json.loads(self._command("inspect", "--type", "container", identity))[0]
        return json.loads(self._command(kind, "inspect", identity))[0]

    def record_path(self, provider, session_id=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", provider):
            raise PiEgressError("Invalid Pi provider identity")
        if session_id is not None and not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise PiEgressError("Invalid Pi session identity")
        return self.root / (provider + ("--" + session_id if session_id else "") + ".json")

    def _retire_legacy(self, provider):
        """Remove the pre-release shared gateway only after proving it is idle."""
        policy = self.root / (provider + "-allowlist.json")
        if not policy.exists():
            return
        suffix = hashlib.sha256((str(self.root) + provider).encode()).hexdigest()[:16]
        network = self._inspect("network", "isolated-pi-" + suffix)
        proxy = self._inspect("container", "anvil-pi-proxy-" + suffix)
        config, host = proxy["Config"], proxy["HostConfig"]
        digest = hashlib.sha256(policy.read_bytes()).hexdigest()
        mounts = proxy.get("Mounts", [])
        if (network.get("Internal") is not True or network.get("Labels", {}).get("anvil.pi.egress") != suffix
                or set(network.get("Containers", {})) != {proxy["Id"]}
                or config.get("Labels", {}).get("anvil.pi.egress") != suffix
                or config.get("Labels", {}).get("anvil.pi.egress.policy") != digest
                or config.get("Entrypoint") != ["node"]
                or config.get("Cmd") != ["/opt/pi-runner/proxy.cjs", "/policy/allowlist.json"]
                or len(mounts) != 1 or mounts[0].get("Source") != str(policy)
                or mounts[0].get("Destination") != "/policy/allowlist.json" or mounts[0].get("RW") is not False
                or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False
                or host.get("CapDrop") != ["ALL"] or host.get("CapAdd") or host.get("PortBindings")):
            raise PiEgressError("Legacy Pi gateway cannot be proven idle and owned")
        self._command("stop", "--time", "5", proxy["Id"])
        self._command("rm", proxy["Id"])
        self._command("network", "rm", network["Id"])
        policy.unlink()

    def setup(self, provider, *, confirm=False, session_id=None, runner_name=None):
        endpoints = self.config.get("provider_egress", {}).get(provider)
        if provider not in self.config["models"] or not endpoints:
            raise PiEgressError("Declare this provider's exact egress origins in private configuration")
        endpoint = self.config["provider_endpoints"][provider]
        parsed = urlsplit(endpoint["base_url"])
        origin = parsed.scheme + "://" + parsed.netloc
        secret = Path(self.config["provider_secret_refs"][provider][endpoint["credential_env"]][5:])
        if secret.is_symlink() or not secret.is_file() or secret.stat().st_mode & 0o077:
            raise PiEgressError("Provider credential must be a protected regular file")
        policy = {"provider": provider, "targets": targets([origin]), "api": endpoint["api"], "base_path": parsed.path,
                  "models": self.config["models"][provider], "max_tokens": endpoint.get("max_tokens", 4096)}
        if session_id is None:
            # Installation approves exact provider policy. Per-session gateways
            # are created only by the server-owned runner lifecycle.
            result = {"provider": provider, "confirmed": confirm, "policy": policy}
            if confirm:
                self._retire_legacy(provider)
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = self.record_path(provider)
                result["image"] = self.config["image"]
                path.write_bytes(_encoded(result)); path.chmod(0o600)
            return result
        if not re.fullmatch(r"[a-f0-9]{32}", session_id) or not re.fullmatch(r"anvil-pi-[a-f0-9]{20}", runner_name or ""):
            raise PiEgressError("Gateway requires an exact server-generated session identity")
        approved = json.loads(self.record_path(provider).read_text())
        if approved.get("policy") != policy or approved.get("image") != self.config["image"]:
            raise PiEgressError("Provider gateway approval changed; run managed egress setup")
        key = provider + "--" + session_id
        digest = hashlib.sha256(_encoded(policy)).hexdigest()
        suffix = hashlib.sha256((str(self.root) + key).encode()).hexdigest()[:16]
        network, proxy = "isolated-pi-" + suffix, "anvil-pi-proxy-" + suffix
        if not confirm:
            return {"confirmed": False, "provider": provider, "network": network, "proxy": proxy, "targets": policy["targets"], "policy_digest": digest}
        path = self.record_path(provider, session_id)
        if path.exists():
            record = json.loads(path.read_text())
            if record.get("phase") != "creating":
                self.verify(provider, session_id=session_id)
                return {"confirmed": True, "provider": provider, "changed": False}
        else:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            policy_path = self.root / (key + "-allowlist.json")
            policy_path.write_bytes(_encoded(policy)); policy_path.chmod(0o600)
            health = "require('http').get('http://127.0.0.1:3128/health',r=>{let s='';r.on('data',c=>s+=c);r.on('end',()=>process.exit(JSON.parse(s).digest==='" + digest + "'?0:1))}).on('error',()=>process.exit(1))"
            record = {"provider": provider, "origins": endpoints, "network": network, "network_id": None,
                      "proxy": proxy, "proxy_id": None, "image": self.config["image"], "session_id": session_id,
                      "runner_name": runner_name, "secret_path": str(secret), "policy_path": str(policy_path),
                      "digest": digest, "suffix": suffix, "health": 'node -e "' + health + '"', "phase": "creating"}
            # Persist ownership intent before the first engine mutation. Partial
            # setup then retains its capacity and can be reconciled without replay.
            self._save(path, record)
        self._complete_creation(record, path)
        for _ in range(20):
            try:
                self.verify(provider, session_id=session_id)
                return {"confirmed": True, "provider": provider, "changed": True, "network": network}
            except PiEgressError:
                time.sleep(0.25)
        raise PiEgressError("Pi provider gateway failed its readiness and isolation proof")

    @staticmethod
    def _save(path, value):
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(_encoded(value)); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _optional(self, kind, name):
        arguments = ["inspect", "--type", "container", name] if kind == "container" else ["network", "inspect", name]
        result = self.run([self.engine, *arguments], capture_output=True, text=True, timeout=20)
        if result.returncode:
            errors = {f"Error: No such container: {name}", f"Error response from daemon: No such container: {name}",
                      f"Error response from daemon: network {name} not found", f"Error: No such network: {name}"}
            if result.stderr.strip() in errors:
                return None
            raise PiEgressError("Cannot prove absence of a pending Pi gateway resource")
        return json.loads(result.stdout)[0]

    def _complete_creation(self, record, path):
        network = self._optional("network", record["network"])
        if network is None:
            if record["network_id"]:
                raise PiEgressError("Recorded Pi gateway network disappeared")
            self._command("network", "create", "--internal", "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated", "--opt", "com.docker.network.bridge.gateway_mode_ipv6=isolated", "--label", "anvil.pi.egress=" + record["suffix"], record["network"])
            network = self._inspect("network", record["network"])
        if (network.get("Internal") is not True or network.get("Labels", {}).get("anvil.pi.egress") != record["suffix"]
                or record["network_id"] not in (None, network["Id"])):
            raise PiEgressError("Pending Pi gateway network ownership differs")
        record["network_id"] = network["Id"]; self._save(path, record)
        proxy = self._optional("container", record["proxy"])
        if proxy is None:
            if record["proxy_id"] or network.get("Containers"):
                raise PiEgressError("Pending Pi gateway membership differs")
            self._command("run", "--detach", "--name", record["proxy"], "--label", "anvil.pi.egress=" + record["suffix"], "--label", "anvil.pi.egress.policy=" + record["digest"],
                "--log-driver", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f'{self.config["uid"]}:{self.config["gid"]}',
                "--cpus", "0.25", "--memory", str(96 * 1024**2), "--pids-limit", "32", "--network", record["network"],
                "--mount", f'type=bind,source={record["policy_path"]},target=/policy/allowlist.json,readonly',
                "--mount", f'type=bind,source={record["secret_path"]},target=/policy/credential,readonly',
                "--entrypoint", "node", "--health-cmd", record["health"], "--health-interval", "1s", "--health-timeout", "2s", "--health-retries", "5",
                record["image"], "/opt/pi-runner/proxy.cjs", "/policy/allowlist.json")
            proxy = self._inspect("container", record["proxy"])
        if record["proxy_id"] not in (None, proxy["Id"]):
            raise PiEgressError("Pending Pi gateway container identity differs")
        record["proxy_id"] = proxy["Id"]; self._save(path, record)
        self.verify(record["provider"], session_id=record["session_id"], require_ready=False, allow_partial=True)
        if "bridge" not in proxy.get("NetworkSettings", {}).get("Networks", {}):
            self._command("network", "connect", "bridge", record["proxy_id"])
        record["phase"] = "ready"; self._save(path, record)

    def remove(self, provider, *, confirm=False, session_id=None):
        path = self.record_path(provider, session_id)
        if session_id is None:
            records = list(self.root.glob(provider + "--*.json"))
            records = [p for p in records if not p.name.endswith("-allowlist.json")]
            for record in records:
                self.remove(provider, confirm=confirm, session_id=json.loads(record.read_text())["session_id"])
            if confirm:
                path.unlink(missing_ok=True)
            return {"confirmed": confirm, "removed": confirm, "provider": provider}
        record = json.loads(path.read_text())
        self.verify(provider, session_id=session_id, require_ready=False)
        network = self._inspect("network", record["network"])
        if set(network.get("Containers", {})) != {record["proxy_id"]}:
            raise PiEgressError("Stop the session runner before removing its gateway")
        if confirm:
            self._command("stop", "--time", "5", record["proxy_id"])
            self._command("rm", record["proxy_id"])
            self._command("network", "rm", record["network_id"])
            path.unlink()
            Path(record["policy_path"]).unlink()
        return {"confirmed": confirm, "removed": confirm, "provider": provider}

    def verify(self, provider, *, session_id, require_isolated=True, require_ready=True, allow_partial=False):
        key = provider + "--" + session_id
        try:
            record_path = self.record_path(provider, session_id)
            if record_path.is_symlink():
                raise PiEgressError("Pi egress record cannot be a symlink")
            record = json.loads(record_path.read_text())
            policy_path = self.root / (key + "-allowlist.json")
            if policy_path.is_symlink() or record["policy_path"] != str(policy_path) or hashlib.sha256(policy_path.read_bytes()).hexdigest() != record["digest"]:
                raise PiEgressError("Pi egress allowlist differs from its approved setup")
            if record["origins"] != self.config.get("provider_egress", {}).get(provider) or record["image"] != self.config["image"]:
                raise PiEgressError("Pi egress configuration changed; reconcile setup explicitly")
            approved = json.loads(self.record_path(provider).read_text())
            endpoint = self.config["provider_endpoints"][provider]
            secret = self.config["provider_secret_refs"][provider][endpoint["credential_env"]]
            if (approved.get("policy") != json.loads(policy_path.read_text()) or approved.get("image") != record["image"]
                    or record["session_id"] != session_id or record["secret_path"] != secret.removeprefix("file:")):
                raise PiEgressError("Pi gateway no longer matches its provider approval")
            network = self._inspect("network", record["network"])
            proxy = self._inspect("container", record["proxy"])
            config, host = proxy["Config"], proxy["HostConfig"]
            if network["Id"] != record["network_id"] or network.get("Internal") is not True or network.get("Labels", {}).get("anvil.pi.egress") != record["suffix"]:
                raise PiEgressError("Pi provider network is not the approved internal network")
            if require_isolated and any(network.get("Options", {}).get("com.docker.network.bridge.gateway_mode_" + family) != "isolated" for family in ("ipv4", "ipv6")):
                raise PiEgressError("Pi provider network must remove host gateway access")
            if proxy["Id"] != record["proxy_id"] or config.get("Image") != record["image"] or proxy.get("Image") != self._inspect("image", record["image"])["Id"]:
                raise PiEgressError("Pi provider proxy identity differs from setup")
            if config.get("Entrypoint") != ["node"] or config.get("Cmd") != ["/opt/pi-runner/proxy.cjs", "/policy/allowlist.json"] or config.get("User") != f'{self.config["uid"]}:{self.config["gid"]}':
                raise PiEgressError("Pi provider proxy execution differs from policy")
            if config.get("Env", []) != self._inspect("image", record["image"]).get("Config", {}).get("Env", []):
                raise PiEgressError("Pi gateway has an unexpected execution environment")
            mounts = proxy.get("Mounts", [])
            expected = {"/policy/allowlist.json": str(policy_path), "/policy/credential": record["secret_path"]}
            if len(mounts) != 2 or any(m.get("Type") != "bind" or m.get("Source") != expected.get(m.get("Destination")) or m.get("RW") is not False for m in mounts):
                raise PiEgressError("Pi proxy must mount only its immutable provider allowlist")
            if host.get("LogConfig", {}).get("Type") != "none" or host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False or host.get("CapDrop") != ["ALL"] or host.get("CapAdd") or host.get("SecurityOpt") not in (["no-new-privileges"], ["no-new-privileges=true"]) or host.get("Memory") != 96*1024**2 or host.get("NanoCpus") != 250000000 or host.get("PidsLimit") != 32 or host.get("PortBindings") or host.get("DeviceRequests"):
                raise PiEgressError("Pi proxy resource or privilege policy differs from setup")
            membership = proxy.get("NetworkSettings", {}).get("Networks", {})
            if set(membership) not in ([{record["network"]}, {record["network"], "bridge"}] if allow_partial else [{record["network"], "bridge"}]) or membership[record["network"]]["NetworkID"] != record["network_id"]:
                raise PiEgressError("Pi proxy network membership differs from setup")
            members = network.get("Containers", {})
            if record["proxy_id"] not in members or len(members) > 2 or any(cid != record["proxy_id"] and entry.get("Name") != record["runner_name"] for cid, entry in members.items()):
                raise PiEgressError("Pi session network contains an unrelated peer")
            for cid in members:
                if cid != record["proxy_id"]:
                    peer = self._inspect("container", cid)
                    if peer.get("Config", {}).get("Labels", {}).get("anvil.pi.session") != session_id:
                        raise PiEgressError("Pi network peer session identity differs")
            if require_ready and (config.get("Healthcheck", {}).get("Test") != ["CMD-SHELL", record["health"]] or not proxy.get("State", {}).get("Running") or proxy["State"].get("Health", {}).get("Status") != "healthy"):
                raise PiEgressError("Pi provider proxy is not ready with the approved allowlist")
            return record | {"proxy_url": "http://" + record["proxy"] + ":3128"}
        except (KeyError, IndexError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise PiEgressError("Pi egress proof is unavailable") from exc
