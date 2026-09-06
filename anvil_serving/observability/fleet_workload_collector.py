"""Bounded persistent coordination for node workload observations."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .fleet_workload_collection import build_fleet_workloads, normalize_node_workloads
from .workloads import FleetResult, MAX_NODES, NodeResult, WorkloadQuery


_AGGREGATE_SECONDS = 5.0
_NODE_SECONDS = 2.0
_MAX_WORKERS = 4

_Reader = Callable[[str, WorkloadQuery, datetime], NodeResult]


@dataclass
class _Job:
    generation: int
    host: str
    query: WorkloadQuery
    now: datetime
    aggregate_started: float
    aggregate_deadline: float
    claimed: bool = False
    claimed_at: float | None = None
    result_deadline: float | None = None
    expired: bool = False


def _clock_value(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _read_clock(clock: Callable[[], object]) -> float | None:
    try:
        return _clock_value(clock())
    except Exception:
        return None


def _copy_query(query: WorkloadQuery) -> WorkloadQuery:
    return WorkloadQuery(
        query.owner,
        query.kind,
        query.state,
        query.host,
        query.active_only,
        query.recent_seconds,
        query.limit,
    )


class FleetWorkloadCollector:
    """Collect a bounded fleet snapshot with four persistent workers."""

    def __init__(
        self,
        readers: dict[str, _Reader | None],
        *,
        monotonic: Callable[[], object] = time.monotonic,
    ) -> None:
        if type(readers) is not dict or len(readers) > MAX_NODES:
            raise ValueError("invalid fleet workload collector")
        copied: dict[str, _Reader | None] = {}
        for host, reader in readers.items():
            try:
                WorkloadQuery(host=host)
            except Exception:
                raise ValueError("invalid fleet workload collector") from None
            if type(host) is not str or host in copied:
                raise ValueError("invalid fleet workload collector")
            if reader is not None and not callable(reader):
                raise ValueError("invalid fleet workload collector")
            copied[host] = reader
        if not callable(monotonic):
            raise ValueError("invalid fleet workload collector")

        self._hosts = tuple(sorted(copied))
        self._readers = copied
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._workers: dict[int, threading.Thread] = {}
        self._slots: dict[int, _Job | None] = {}
        self._completions: dict[int, tuple[_Job, NodeResult | None]] = {}
        self._generation = 0
        self._active: int | None = None
        self._active_hosts: tuple[str, ...] = ()
        self._active_index = 0
        self._processed: set[str] = set()
        self._accumulator: FleetResult | None = None
        self._invalid_active = False
        self._closed = False

    def _start_worker_locked(self, worker_id: int) -> bool:
        if worker_id in self._workers:
            return True
        worker = threading.Thread(
            target=self._worker,
            args=(worker_id,),
            name=f"anvil-fleet-workload-{worker_id}",
            daemon=True,
        )
        self._workers[worker_id] = worker
        self._slots[worker_id] = None
        try:
            worker.start()
        except Exception:
            self._workers.pop(worker_id, None)
            self._slots.pop(worker_id, None)
            return False
        return True

    def _clear_active_locked(self, generation: int) -> None:
        if self._active != generation:
            return
        for worker_id, job in tuple(self._slots.items()):
            if job is not None and job.generation == generation and not job.claimed:
                self._slots[worker_id] = None
        self._completions.clear()
        self._active = None
        self._active_hosts = ()
        self._active_index = 0
        self._processed.clear()
        self._accumulator = None
        self._invalid_active = False
        self._condition.notify_all()

    def _worker(self, worker_id: int) -> None:
        while True:
            with self._condition:
                while not self._closed and self._slots.get(worker_id) is None:
                    self._condition.wait()
                if self._closed:
                    return
                job = self._slots[worker_id]
                assert job is not None

            claimed_at = _read_clock(self._monotonic)
            with self._condition:
                current = self._slots.get(worker_id)
                usable = (
                    current is job
                    and self._active == job.generation
                    and not self._closed
                    and claimed_at is not None
                    and claimed_at >= job.aggregate_started
                    and claimed_at <= job.aggregate_deadline
                )
                if not usable:
                    if current is job:
                        self._slots[worker_id] = None
                    if (
                        self._active == job.generation
                        and (claimed_at is None or claimed_at < job.aggregate_started)
                    ):
                        self._invalid_active = True
                    self._condition.notify_all()
                    continue
                job.claimed = True
                job.claimed_at = claimed_at
                job.result_deadline = min(
                    job.aggregate_deadline, claimed_at + _NODE_SECONDS
                )

            try:
                raw = self._readers[job.host]
                result = None if raw is None else raw(job.host, job.query, job.now)
            except Exception:
                result = None
            try:
                normalized = normalize_node_workloads(
                    job.host, job.query, job.now, result
                )
            except Exception:
                normalized = None
            completed_at = _read_clock(self._monotonic)

            with self._condition:
                if self._slots.get(worker_id) is job:
                    self._slots[worker_id] = None
                if self._active == job.generation and not self._closed:
                    invalid = (
                        completed_at is None
                        or job.claimed_at is None
                        or completed_at < job.claimed_at
                    )
                    if invalid:
                        self._invalid_active = True
                    elif (
                        not job.expired
                        and job.result_deadline is not None
                        and completed_at <= job.result_deadline
                        and completed_at <= job.aggregate_deadline
                    ):
                        self._completions[worker_id] = (job, normalized)
                    elif not job.expired:
                        self._completions[worker_id] = (job, None)
                self._condition.notify_all()

    def _schedule_locked(
        self,
        generation: int,
        query: WorkloadQuery,
        now: datetime,
        started: float,
        deadline: float,
    ) -> bool:
        busy_hosts = {
            job.host
            for job in self._slots.values()
            if job is not None and job.claimed and job.generation != generation
        }
        for host in self._active_hosts:
            if host in busy_hosts:
                self._processed.add(host)

        unscheduled = sum(
            host not in self._processed
            for host in self._active_hosts[self._active_index :]
        )
        idle = sum(
            self._slots.get(worker_id) is None
            and worker_id not in self._completions
            for worker_id in self._workers
        )
        desired_idle = min(_MAX_WORKERS, unscheduled)
        while idle < desired_idle and len(self._workers) < _MAX_WORKERS:
            worker_id = next(
                value for value in range(_MAX_WORKERS) if value not in self._workers
            )
            if not self._start_worker_locked(worker_id):
                self._invalid_active = True
                return False
            idle += 1

        for worker_id in sorted(self._workers):
            if (
                self._slots.get(worker_id) is not None
                or worker_id in self._completions
            ):
                continue
            while self._active_index < len(self._active_hosts):
                host = self._active_hosts[self._active_index]
                self._active_index += 1
                if host in self._processed:
                    continue
                self._slots[worker_id] = _Job(
                    generation,
                    host,
                    query,
                    now,
                    started,
                    deadline,
                )
                break
        self._condition.notify_all()
        return True

    def collect(self, query: WorkloadQuery, now: datetime) -> FleetResult:
        """Return one bounded canonical fleet observation."""

        fallback = build_fleet_workloads(self._hosts, query, now, {})
        checked_query = _copy_query(query)
        checked_now = fallback.collection_timestamp
        eligible = tuple(
            host
            for host in self._hosts
            if self._readers[host] is not None
            and (checked_query.host is None or checked_query.host == host)
        )
        with self._condition:
            if self._closed or self._active is not None:
                return fallback
            self._generation += 1
            generation = self._generation
            self._active = generation
            self._active_hosts = eligible
            self._active_index = 0
            self._processed = {
                host
                for host in self._hosts
                if host not in eligible
            }
            self._accumulator = None
            self._invalid_active = False

        started = _read_clock(self._monotonic)
        if started is None:
            with self._condition:
                self._clear_active_locked(generation)
            return fallback
        deadline = started + _AGGREGATE_SECONDS
        previous = started

        try:
            with self._condition:
                if self._active != generation or self._closed:
                    self._clear_active_locked(generation)
                    return fallback
                if not self._schedule_locked(
                    generation, checked_query, checked_now, started, deadline
                ):
                    self._clear_active_locked(generation)
                    return fallback

            while True:
                current = _read_clock(self._monotonic)
                if current is None or current < previous:
                    with self._condition:
                        self._clear_active_locked(generation)
                    return fallback
                previous = current

                completion: tuple[_Job, NodeResult | None] | None = None
                with self._condition:
                    if (
                        self._closed
                        or self._active != generation
                        or self._invalid_active
                    ):
                        self._clear_active_locked(generation)
                        return fallback
                    if self._completions:
                        worker_id = min(self._completions)
                        completion = self._completions.pop(worker_id)
                    for job in self._slots.values():
                        if (
                            job is not None
                            and job.generation == generation
                            and job.claimed
                            and not job.expired
                            and job.result_deadline is not None
                            and current > job.result_deadline
                        ):
                            job.expired = True
                            self._processed.add(job.host)

                if completion is not None:
                    job, result = completion
                    with self._condition:
                        accumulator = self._accumulator
                    nodes = (
                        {}
                        if accumulator is None
                        else {node.host: node for node in accumulator.nodes}
                    )
                    nodes[job.host] = result
                    processed_hosts = tuple(sorted((*nodes,)))
                    merged = build_fleet_workloads(
                        processed_hosts, checked_query, checked_now, nodes
                    )
                    with self._condition:
                        if self._active != generation or self._closed:
                            self._clear_active_locked(generation)
                            return fallback
                        self._accumulator = merged
                        self._processed.add(job.host)

                with self._condition:
                    if current < deadline:
                        self._schedule_locked(
                            generation, checked_query, checked_now, started, deadline
                        )
                    finished = len(self._processed) == len(self._hosts)
                    if self._completions:
                        continue
                    if finished or current >= deadline:
                        accumulator = self._accumulator
                        nodes = (
                            {}
                            if accumulator is None
                            else {node.host: node for node in accumulator.nodes}
                        )
                        self._clear_active_locked(generation)
                        break
                    next_deadline = deadline
                    for job in self._slots.values():
                        if (
                            job is not None
                            and job.generation == generation
                            and not job.expired
                            and job.result_deadline is not None
                        ):
                            next_deadline = min(next_deadline, job.result_deadline)
                    self._condition.wait(timeout=max(0.0, next_deadline - current))
        except Exception:
            with self._condition:
                self._clear_active_locked(generation)
            return fallback

        return build_fleet_workloads(self._hosts, checked_query, checked_now, nodes)

    def close(self) -> None:
        """Abandon pending work without waiting for running callbacks."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            generation = self._active
            if generation is not None:
                self._clear_active_locked(generation)
            self._completions.clear()
            for worker_id, job in tuple(self._slots.items()):
                if job is not None and not job.claimed:
                    self._slots[worker_id] = None
            self._condition.notify_all()


__all__ = ["FleetWorkloadCollector"]
