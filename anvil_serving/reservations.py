"""ADR-0017 GPU residency reservations — the declarative per-`gpu_role` VRAM ledger.

The ledger arbitrates VRAM on a multi-tenant GPU (the RTX 5090 hosting voice
sidecars + purpose models + the fast LLM tier) where the driver cannot:
per-process VRAM attribution is impossible under WSL2 passthrough, so the
accounting is DECLARATIVE — each `[[serve]]` manifest entry may declare
`gpu_role`/`vram_mib`/`residency` (parsed by :mod:`anvil_serving.serves`), and
the serves manifest may declare `[[gpu_roles]]` capacity rows (`id`,
`vram_mib` capacity, `reserve_mib` display/system reserve — mirroring the
operator topology's `[[gpu_roles]]` fields from ADR-0017 §2).

Ledger state is DERIVED, never stored: committed VRAM for a role is the sum of
declared reservations of manifest serves whose container is currently
running/paused/restarting (docker is the source of truth — a paused container
still pins 100% of its VRAM). There is no state file; `serves down` releases a
reservation simply by stopping the container.

Enforcement happens at the lifecycle verbs (`serves up`, and `voice audio up`
which delegates to the same `cmd_up`): :func:`deny_over_budget` is consulted
BEFORE any container-mutating docker command runs, and an over-budget request
fails the whole command with the ledger printed. When the requester is
`on-demand` and the operator opted in (`serves up --evict`),
:func:`plan_eviction` chooses the committed `evictable` reservations whose
release would admit the request (ADR-0017 §5); `serves.cmd_up` then composes
the ADR-0018 transition per victim — quiesce its router tier, bounded drain
via the router's `AdmissionLease` accounting, `serves down` — and the stopped
container IS the reservation release. `resident` reservations are never
candidates: this module plans or refuses (loudly, with the ledger); it never
operates containers or the router itself.

Terminology (ADR-0017): these are *reservations*, never `*Lease` —
`AdmissionLease` in `router/admission.py` is the request-admission layer;
`GpuReservation` is the VRAM capacity layer beneath it.

Stdlib-only; this module never invokes docker itself — callers inject a
`state_of(container) -> str` probe (serves.py's `docker_state`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# Container states whose declared VRAM reservation is COMMITTED. A paused
# container still pins its VRAM; 'restarting' is a running container caught
# mid-backoff (it will be 'running' moments later). Everything else —
# exited/created/dead/absent — holds no VRAM, and 'error' (docker state
# undeterminable) is treated as not-committed: admission never blocks on
# uncertainty here because the subsequent `up` fails loudly on the same error.
RESERVED_STATES = ("running", "paused", "restarting")

# Private key under which serves.load_manifest attaches the manifest's
# [[gpu_roles]] capacity table to each serve dict (like `_manifest_dir`), so
# the budgets travel with the parsed serves through every existing call path
# (voice audio up, promotions) without signature changes. Only attached when
# the manifest actually declares [[gpu_roles]] — pre-reservation manifests
# keep parsing byte-for-byte unchanged (T001 contract).
GPU_ROLES_KEY = "_gpu_roles"


@dataclass(frozen=True)
class GpuRoleBudget:
    """Declared VRAM capacity of one `gpu_role` (ADR-0017 §2).

    `vram_mib` is the card's capacity; `reserve_mib` is the never-reservable
    display/system reserve (the 5090 is also the Windows display GPU). Both are
    declared identity, not measured state.
    """

    gpu_role: str
    vram_mib: int
    reserve_mib: int = 0

    @property
    def budget_mib(self) -> int:
        return self.vram_mib - self.reserve_mib


@dataclass(frozen=True)
class GpuReservation:
    """One serve's declared VRAM reservation (ADR-0017 §1).

    `state` is the docker container state observed when the ledger snapshot was
    taken (None before a snapshot).
    """

    serve: str
    container: str
    gpu_role: str
    vram_mib: int
    residency: Optional[str] = None
    state: Optional[str] = None

    @property
    def committed(self) -> bool:
        return self.state in RESERVED_STATES

    def describe(self) -> str:
        parts = ["%s %d MiB" % (self.serve, self.vram_mib)]
        detail = [d for d in (self.residency, self.state) if d]
        if detail:
            parts.append("(%s)" % ", ".join(detail))
        return " ".join(parts)


@dataclass(frozen=True)
class RoleLedger:
    """Point-in-time ledger for one gpu_role: budget + committed reservations."""

    budget: GpuRoleBudget
    reservations: tuple[GpuReservation, ...] = field(default_factory=tuple)

    @property
    def committed_mib(self) -> int:
        return sum(r.vram_mib for r in self.reservations if r.committed)

    @property
    def free_mib(self) -> int:
        return self.budget.budget_mib - self.committed_mib

    def describe(self) -> str:
        return (
            "gpu_role %r: capacity %d MiB, reserve %d MiB, committed %d MiB, "
            "free %d MiB" % (
                self.budget.gpu_role, self.budget.vram_mib,
                self.budget.reserve_mib, self.committed_mib, self.free_mib,
            )
        )


def parse_gpu_roles(data: dict) -> dict[str, GpuRoleBudget]:
    """Parse a serves manifest's `[[gpu_roles]]` capacity rows.

    Field rules mirror the topology schema (T001): `vram_mib` is a required
    positive integer, `reserve_mib` a non-negative integer that must not exceed
    `vram_mib` (default 0), `id` a unique non-empty string. Raises ValueError
    on the first violation — a manifest that declares capacity wrong should
    fail loudly at parse time, not admit serves against a garbage budget.
    """
    budgets: dict[str, GpuRoleBudget] = {}
    for raw in data.get("gpu_roles", []):
        role_id = raw.get("id")
        if not isinstance(role_id, str) or not role_id.strip():
            raise ValueError(f"gpu_roles entry id must be a non-empty string: {raw!r}")
        role_id = role_id.strip()
        if role_id in budgets:
            raise ValueError(f"duplicate gpu_roles id {role_id!r}")
        vram = raw.get("vram_mib")
        if isinstance(vram, bool) or not isinstance(vram, int) or vram <= 0:
            raise ValueError(
                f"gpu_roles entry vram_mib must be a positive integer (MiB): {raw!r}"
            )
        reserve = raw.get("reserve_mib", 0)
        if isinstance(reserve, bool) or not isinstance(reserve, int) or reserve < 0:
            raise ValueError(
                f"gpu_roles entry reserve_mib must be a non-negative integer (MiB): {raw!r}"
            )
        if reserve > vram:
            raise ValueError(
                f"gpu_roles entry reserve_mib must not exceed vram_mib: {raw!r}"
            )
        unknown = set(raw) - {"id", "vram_mib", "reserve_mib"}
        if unknown:
            raise ValueError(
                f"gpu_roles entry has unknown field(s) {sorted(unknown)}: {raw!r}"
            )
        budgets[role_id] = GpuRoleBudget(role_id, vram, reserve)
    return budgets


def derive_gpu_memory_utilization(vram_mib: int, budget: GpuRoleBudget) -> float:
    """Engine-enforced budget for a reserved vLLM/SGLang serve (ADR-0017 §4).

    `serves render` derives the engine's memory fraction
    (`--gpu-memory-utilization` for vLLM, `--mem-fraction-static` for SGLang)
    from `vram_mib / (capacity - reserve)` so the declared reservation is what
    the engine actually respects — a serve can no longer hand-tune a fraction
    that quietly exceeds what the ledger admitted.

    Raises ValueError when the reservation can never fit its role's budget
    (`vram_mib > capacity - reserve`, or a zero budget): rendering a compose
    the admission check (`deny_over_budget`) is guaranteed to reject would just
    defer the failure to `serves up`.
    """
    if isinstance(vram_mib, bool) or not isinstance(vram_mib, int) or vram_mib <= 0:
        raise ValueError(
            f"reservation vram_mib must be a positive integer (MiB): {vram_mib!r}"
        )
    if budget.budget_mib <= 0:
        raise ValueError(
            "gpu_role %r has no reservable budget (capacity %d MiB - reserve %d MiB)"
            % (budget.gpu_role, budget.vram_mib, budget.reserve_mib)
        )
    if vram_mib > budget.budget_mib:
        raise ValueError(
            "reservation %d MiB exceeds gpu_role %r budget %d MiB "
            "(capacity %d MiB - reserve %d MiB); shrink vram_mib or raise capacity"
            % (vram_mib, budget.gpu_role, budget.budget_mib,
               budget.vram_mib, budget.reserve_mib)
        )
    return round(vram_mib / budget.budget_mib, 4)


def reservation_of(serve: dict) -> Optional[GpuReservation]:
    """The serve's declared reservation, or None if it doesn't participate.

    Participation needs BOTH `gpu_role` (which ledger) and `vram_mib` (how
    much); a serve declaring neither — or only one — stays outside the ledger,
    so pre-reservation manifests keep their exact behavior (ADR-0017: adoption
    is incremental).
    """
    gpu_role = serve.get("gpu_role")
    vram_mib = serve.get("vram_mib")
    if not gpu_role or not isinstance(vram_mib, int):
        return None
    return GpuReservation(
        serve=serve["name"],
        container=serve["container"],
        gpu_role=gpu_role,
        vram_mib=vram_mib,
        residency=serve.get("residency"),
    )


def budgets_of(serves: Iterable[dict]) -> dict[str, GpuRoleBudget]:
    """The manifest's capacity table as attached by serves.load_manifest."""
    for serve in serves:
        table = serve.get(GPU_ROLES_KEY)
        if table:
            return table
    return {}


def build_ledger(
    serves: Iterable[dict],
    state_of: Callable[[str], str],
    budgets: Optional[dict[str, GpuRoleBudget]] = None,
) -> dict[str, RoleLedger]:
    """Derive the per-role ledger from declared fields + observed docker state.

    One `state_of` probe per reservation-declaring serve; serves without a
    reservation (or on a role with no declared capacity) are never probed.
    """
    if budgets is None:
        budgets = budgets_of(serves)
    per_role: dict[str, list[GpuReservation]] = {role: [] for role in budgets}
    seen: set[str] = set()
    for serve in serves:
        if serve["name"] in seen:
            continue
        seen.add(serve["name"])
        reservation = reservation_of(serve)
        if reservation is None or reservation.gpu_role not in budgets:
            continue
        snapshot = GpuReservation(
            serve=reservation.serve,
            container=reservation.container,
            gpu_role=reservation.gpu_role,
            vram_mib=reservation.vram_mib,
            residency=reservation.residency,
            state=state_of(reservation.container),
        )
        per_role[snapshot.gpu_role].append(snapshot)
    return {
        role: RoleLedger(budget=budgets[role], reservations=tuple(rows))
        for role, rows in per_role.items()
    }


def ledger_summary(ledger: dict[str, RoleLedger]) -> dict:
    """JSON-able ledger snapshot for `serves status` and MCP (T004).

    One row per gpu_role — capacity, reserve, derived budget, committed, free —
    plus every declared reservation with its observed docker state. The shape
    is intentionally simple and stable: the MCP `reservation_status` tool
    returns exactly this, so agents can answer "can model X fit right now?"
    without scraping the human table (ADR-0017 consequences).
    """
    return {
        "gpu_roles": [
            {
                "gpu_role": role,
                "capacity_mib": role_ledger.budget.vram_mib,
                "reserve_mib": role_ledger.budget.reserve_mib,
                "budget_mib": role_ledger.budget.budget_mib,
                "committed_mib": role_ledger.committed_mib,
                "free_mib": role_ledger.free_mib,
                "reservations": [
                    {
                        "serve": r.serve,
                        "container": r.container,
                        "vram_mib": r.vram_mib,
                        "residency": r.residency,
                        "state": r.state,
                        "committed": r.committed,
                    }
                    for r in role_ledger.reservations
                ],
            }
            for role, role_ledger in sorted(ledger.items())
        ],
    }


def deny_over_budget(
    serves: list[dict],
    targets: list[dict],
    state_of: Callable[[str], str],
) -> Optional[list[str]]:
    """Admission check for `serves up` / `voice audio up` (ADR-0017 §3).

    Returns printable denial lines when acquiring the targets' reservations
    would exceed any gpu_role budget — the caller must then run NO
    container-mutating docker command and exit non-zero. Returns None when
    admitted (including every manifest without reservation fields: those flows
    run zero extra docker probes and stay byte-for-byte unchanged).

    A target whose container is already running/paused holds its reservation
    already (committed), so re-running `up` on it requests nothing new.
    """
    budgets = budgets_of(serves)
    if not budgets:
        return None
    requesting = [
        t for t in targets
        if (r := reservation_of(t)) is not None and r.gpu_role in budgets
    ]
    if not requesting:
        return None

    ledger = build_ledger(serves, state_of, budgets=budgets)
    target_names = {t["name"] for t in requesting}
    lines: list[str] = []
    for role, role_ledger in sorted(ledger.items()):
        requested = [
            r for r in role_ledger.reservations
            if r.serve in target_names and not r.committed
        ]
        requested_mib = sum(r.vram_mib for r in requested)
        if not requested or requested_mib <= role_ledger.free_mib:
            continue
        lines.append(
            "reservation denied: gpu_role %r is over budget by %d MiB" % (
                role, requested_mib - role_ledger.free_mib)
        )
        lines.append(role_ledger.describe())
        committed = [r for r in role_ledger.reservations if r.committed]
        if committed:
            lines.append(
                "committed: " + "; ".join(r.describe() for r in committed)
            )
        for r in requested:
            lines.append(
                "offending reservation: %s > free %d MiB" % (
                    r.describe(), role_ledger.free_mib)
            )
    if not lines:
        return None
    lines.append(
        "no container command was run; free VRAM with `serves down <name>` "
        "or shrink the declared vram_mib"
    )
    return lines


def plan_eviction(
    serves: list[dict],
    targets: list[dict],
    state_of: Callable[[str], str],
) -> tuple[Optional[list[GpuReservation]], list[str]]:
    """Choose the `evictable` reservations whose release admits the targets
    (ADR-0017 §5, gpu-reservations:T005).

    Returns ``(victims, lines)``:

    - ``([], [])`` — nothing to evict: the targets already fit (a concurrent
      release can make an earlier denial stale — the ledger is re-derived
      here), or the manifest has no ledger.
    - ``(victims, lines)`` — stopping the returned committed `evictable`
      reservations (largest-first per role, so the fewest serves are
      disturbed; name tie-break for determinism) frees enough VRAM. `lines`
      describe the plan.
    - ``(None, lines)`` — eviction cannot admit the targets, with the ledger:
      a requester that is not `on-demand` may never evict (ADR-0017 §1/§3),
      and only `evictable` reservations are candidates — `resident` (and any
      other non-`evictable`) committed reservations are never stopped, so a
      deficit exceeding every evictable reservation combined is a loud
      refusal, not a deeper eviction.

    Planning only: no docker command runs here, and executing the plan is the
    caller's job (serves.cmd_up composes the ADR-0018 quiesce/drain transition
    before each victim's container is stopped).
    """
    budgets = budgets_of(serves)
    if not budgets:
        return [], []
    requesting = [
        t for t in targets
        if (r := reservation_of(t)) is not None and r.gpu_role in budgets
    ]
    if not requesting:
        return [], []
    residency_of = {t["name"]: t.get("residency") for t in requesting}
    target_names = set(residency_of)
    ledger = build_ledger(serves, state_of, budgets=budgets)
    victims: list[GpuReservation] = []
    lines: list[str] = []
    refused = False
    for role, role_ledger in sorted(ledger.items()):
        requested = [
            r for r in role_ledger.reservations
            if r.serve in target_names and not r.committed
        ]
        deficit = sum(r.vram_mib for r in requested) - role_ledger.free_mib
        if not requested or deficit <= 0:
            continue
        non_on_demand = [
            r for r in requested if residency_of.get(r.serve) != "on-demand"
        ]
        if non_on_demand:
            refused = True
            lines.append(
                "eviction refused: gpu_role %r is over budget by %d MiB and "
                "only `on-demand` requesters may evict (ADR-0017)" % (
                    role, deficit)
            )
            lines.append(role_ledger.describe())
            for r in non_on_demand:
                lines.append(
                    "requester %s declares residency %s" % (
                        r.describe(),
                        repr(residency_of.get(r.serve)) if residency_of.get(r.serve)
                        else "(none)",
                    )
                )
            continue
        candidates = sorted(
            (
                r for r in role_ledger.reservations
                if r.committed and r.residency == "evictable"
                and r.serve not in target_names
            ),
            key=lambda r: (-r.vram_mib, r.serve),
        )
        evictable_mib = sum(r.vram_mib for r in candidates)
        if evictable_mib < deficit:
            refused = True
            protected = [
                r for r in role_ledger.reservations
                if r.committed and r.residency != "evictable"
            ]
            lines.append(
                "eviction refused: gpu_role %r needs %d MiB but every "
                "evictable reservation combined frees only %d MiB" % (
                    role, deficit, evictable_mib)
            )
            lines.append(role_ledger.describe())
            for r in protected:
                lines.append(
                    "never evicted (residency %s): %s" % (
                        repr(r.residency) if r.residency else "(none)",
                        r.describe(),
                    )
                )
            continue
        freed = 0
        for r in candidates:
            if freed >= deficit:
                break
            victims.append(r)
            freed += r.vram_mib
            lines.append(
                "evict %s: frees %d MiB on gpu_role %r" % (
                    r.describe(), r.vram_mib, role)
            )
    if refused:
        lines.append("no container command was run; the ledger stands")
        return None, lines
    return victims, lines
