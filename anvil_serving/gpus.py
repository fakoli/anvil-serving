"""Shared GPU enumeration + index<->UUID resolution (genericity:T007).

Single source of truth for talking to `nvidia-smi`, reused by `multiplexer`
(backend launch, `--gpus all` isolation) and `deploy` (compose render). Kept
here — not duplicated per caller — so the two never drift.

Why UUID pinning at all: Docker Desktop's WSL2 backend IGNORES
`--gpus device=N` (it exposes ALL GPUs to the container), so index-only
pinning does NOT isolate — two serves could land on one card. The proven
reliable isolation (examples/fakoli-dark gotcha, CLAUDE.md gotcha #13) is
`--gpus all` + `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=<uuid>`.

Every function here is IMPURE (shells out to `nvidia-smi`) but accepts an
injectable `_run` seam so callers/tests can run with no GPU present.
"""
import re
import subprocess
from collections.abc import Iterable, Mapping

_UUID_PREFIX = "GPU-"
_GPU_UUID_RE = re.compile(
    r"^GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class GpuRoleResolutionError(ValueError):
    """A configured GPU role cannot be matched safely to observed hardware."""


def list_gpus(_run=subprocess.check_output):
    """[{"index": int, "uuid": str, "name": str}, ...] via `nvidia-smi`.

    Returns `[]` if `nvidia-smi` is absent, not on PATH, or errors (no GPU
    visible / driver not installed) — never raises.
    """
    try:
        out = _run(
            ["nvidia-smi", "--query-gpu=index,uuid,name", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL, encoding="utf-8")
    except Exception:
        return []
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            gpus.append({
                "index": int(parts[0]),
                "uuid": parts[1],
                "name": parts[2] if len(parts) > 2 else "",
            })
    return gpus


def gpu_uuid(index, _run=subprocess.check_output):
    """IMPURE: map a GPU index -> its stable UUID via `nvidia-smi`, else None.

    Returns None if `nvidia-smi` is missing or the index isn't found (caller
    then falls back to the `--gpus device=N` / `device_ids: ["<index>"]`
    form).
    """
    for g in list_gpus(_run=_run):
        if g["index"] == index:
            return g["uuid"]
    return None


def canonical_gpu_uuid(value):
    """Return the canonical ``GPU-`` UUID form or raise a resolution error."""
    if not isinstance(value, str) or not _GPU_UUID_RE.fullmatch(value):
        raise GpuRoleResolutionError(
            f"GPU UUID {value!r} must use the NVIDIA GPU- UUID form with hexadecimal 8-4-4-4-12 groups"
        )
    return f"GPU-{value[4:].lower()}"


def resolve_gpu_roles(roles: Iterable[object], _run=subprocess.check_output):
    """Resolve declared GPU roles against ``nvidia-smi`` by UUID, never index.

    ``roles`` accepts topology-style objects with ``id`` and ``uuid`` attributes
    or mappings containing those fields.  The returned rows are suitable for
    human or JSON command context: ``role``, canonical ``uuid``, observed
    runtime ``index``, and observed device ``name``.  An unavailable, duplicate,
    malformed, or mismatched UUID raises before a caller can start a model serve.
    """
    configured = []
    role_ids = set()
    uuids = set()
    for role in roles:
        role_id = _role_value(role, "id")
        if not isinstance(role_id, str) or not role_id:
            raise GpuRoleResolutionError("GPU role is missing a non-empty id")
        if role_id in role_ids:
            raise GpuRoleResolutionError(f"duplicate GPU role id {role_id!r}")
        uuid_value = _role_value(role, "uuid")
        if uuid_value is None:
            raise GpuRoleResolutionError(f"GPU role {role_id!r} is missing a UUID")
        uuid = canonical_gpu_uuid(uuid_value)
        if uuid in uuids:
            raise GpuRoleResolutionError(f"duplicate configured GPU UUID {uuid!r}")
        role_ids.add(role_id)
        uuids.add(uuid)
        configured.append((role_id, uuid))

    observed = {}
    indexes = set()
    for device in list_gpus(_run=_run):
        try:
            uuid = canonical_gpu_uuid(device["uuid"])
            index = device["index"]
        except (KeyError, GpuRoleResolutionError) as exc:
            raise GpuRoleResolutionError(f"nvidia-smi reported an invalid GPU record: {exc}") from None
        if uuid in observed:
            raise GpuRoleResolutionError(f"nvidia-smi reported duplicate GPU UUID {uuid!r}")
        if index in indexes:
            raise GpuRoleResolutionError(f"nvidia-smi reported duplicate GPU index {index!r}")
        observed[uuid] = device
        indexes.add(index)

    resolved = []
    for role_id, uuid in configured:
        device = observed.get(uuid)
        if device is None:
            raise GpuRoleResolutionError(
                f"GPU role {role_id!r} requires UUID {uuid!r}, which nvidia-smi did not report"
            )
        resolved.append(
            {
                "role": role_id,
                "uuid": uuid,
                "index": device["index"],
                "name": device["name"],
            }
        )
    return resolved


def _role_value(role, field):
    if isinstance(role, Mapping):
        return role.get(field)
    return getattr(role, field, None)


def _looks_like_uuid(spec):
    s = str(spec)
    return s.upper().startswith(_UUID_PREFIX) or s.count("-") >= 2


def resolve_gpu(spec, _run=subprocess.check_output):
    """Resolve a `--gpu` spec (an integer index, a numeric-string index, or a
    `GPU-...` UUID string) against `nvidia-smi`.

    Returns `(uuid, warning)`:
      - `uuid`: the resolved GPU UUID string, or None if it could not be
        resolved (nvidia-smi absent, or the index/uuid isn't reported).
      - `warning`: a human-readable message to print (via `print(...,
        file=sys.stderr)`) when `uuid` is None, else None.

    Callers fall back to the bare `spec` (e.g. `device_ids: [str(spec)]`, no
    `CUDA_VISIBLE_DEVICES` env block) when `uuid` is None — this NEVER
    raises, so a box with no `nvidia-smi` (dev laptop, CI) still renders.
    """
    gpus = list_gpus(_run=_run)
    spec_str = str(spec)

    if _looks_like_uuid(spec_str):
        for g in gpus:
            if g["uuid"] == spec_str:
                return spec_str, None
        if not gpus:
            return None, (
                f"nvidia-smi not found; cannot verify GPU UUID {spec_str!r} — "
                f"falling back to unpinned `device_ids: [{spec_str!r}]` "
                f"(isolation is not guaranteed on Docker Desktop/WSL2)."
            )
        # UUID not reported by this box's nvidia-smi (different host, typo,
        # or a UUID copied from elsewhere) — accept as-is, no crash.
        return spec_str, (
            f"nvidia-smi did not report GPU UUID {spec_str!r} on this host; "
            f"using it as given, unverified."
        )

    try:
        idx = int(spec_str)
    except ValueError:
        return None, (
            f"--gpu {spec!r} is neither an integer index nor a GPU-UUID; "
            f"falling back to it as a literal device id."
        )

    if not gpus:
        return None, (
            f"nvidia-smi not found; cannot resolve GPU index {idx} to a UUID "
            f"— falling back to `device={idx}` pinning (Docker Desktop/WSL2 "
            f"does not honor this; isolation is not guaranteed)."
        )
    for g in gpus:
        if g["index"] == idx:
            return g["uuid"], None
    return None, (
        f"nvidia-smi did not report GPU index {idx}; falling back to "
        f"`device={idx}` pinning (isolation is not guaranteed)."
    )
