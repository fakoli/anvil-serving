"""Bounded concurrent coordination for canonical node-workload sources."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .workload_collection import build_node_workloads
from .workloads import NodeResult, WorkloadOwner, WorkloadQuery


_COLLECTION_SECONDS = 1.5
_MAX_READERS = 6

_Reader = Callable[[str, WorkloadQuery, datetime], object]


@dataclass
class _Job:
    collection_id: int
    query: WorkloadQuery
    now: datetime
    started_at: float
    deadline: float
    claimed: bool = False


def _valid_clock(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _read_clock(clock: Callable[[], object]) -> float | None:
    try:
        return _valid_clock(clock())
    except Exception:
        return None


def _valid_host(host: object) -> str:
    if type(host) is not str:
        raise ValueError("invalid node workload collector")
    try:
        WorkloadQuery(host=host)
    except Exception:
        raise ValueError("invalid node workload collector") from None
    return host


def _copy_query(query: WorkloadQuery) -> WorkloadQuery:
    return WorkloadQuery(
        owner=query.owner,
        kind=query.kind,
        state=query.state,
        host=query.host,
        active_only=query.active_only,
        recent_seconds=query.recent_seconds,
        limit=query.limit,
    )


class NodeWorkloadCollector:
    """Coordinate at most one bounded callback per configured workload owner."""

    def __init__(
        self,
        host: str,
        readers: dict[WorkloadOwner, _Reader | None],
        *,
        monotonic: Callable[[], object] = time.monotonic,
    ) -> None:
        self._host = _valid_host(host)
        if type(readers) is not dict or len(readers) > _MAX_READERS:
            raise ValueError("invalid node workload collector")
        copied: dict[WorkloadOwner, _Reader | None] = {}
        for owner, reader in readers.items():
            if type(owner) is not WorkloadOwner or owner in copied:
                raise ValueError("invalid node workload collector")
            if reader is not None and not callable(reader):
                raise ValueError("invalid node workload collector")
            copied[owner] = reader
        if not callable(monotonic):
            raise ValueError("invalid node workload collector")
        self._readers = copied
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._jobs: dict[WorkloadOwner, _Job | None] = {
            owner: None for owner, reader in copied.items() if reader is not None
        }
        self._workers: dict[WorkloadOwner, threading.Thread] = {}
        self._results: dict[int, dict[WorkloadOwner, object | None]] = {}
        self._invalid_collections: set[int] = set()
        self._collection_id = 0
        self._active: int | None = None
        self._closed = False

    def _start_worker_locked(self, owner: WorkloadOwner) -> bool:
        if owner in self._workers:
            return True
        worker = threading.Thread(
            target=self._worker,
            args=(owner,),
            name="anvil-node-workload-" + owner.value,
            daemon=True,
        )
        self._workers[owner] = worker
        try:
            worker.start()
        except Exception:
            self._workers.pop(owner, None)
            return False
        return True

    def _abandon_locked(self, collection_id: int) -> None:
        if self._active == collection_id:
            self._active = None
        self._results.pop(collection_id, None)
        self._invalid_collections.discard(collection_id)
        for owner, job in tuple(self._jobs.items()):
            if job is not None and job.collection_id == collection_id and not job.claimed:
                self._jobs[owner] = None
        self._condition.notify_all()

    def _worker(self, owner: WorkloadOwner) -> None:
        reader = self._readers[owner]
        assert reader is not None
        while True:
            with self._condition:
                while not self._closed and self._jobs[owner] is None:
                    self._condition.wait()
                if self._closed:
                    return
                job = self._jobs[owner]
                assert job is not None
            claimed_at = _read_clock(self._monotonic)
            with self._condition:
                active = self._active == job.collection_id and not self._closed
                invalid_clock = claimed_at is None or claimed_at < job.started_at
                usable = (
                    not invalid_clock
                    and claimed_at <= job.deadline
                )
                if not active or not usable or self._jobs[owner] is not job:
                    if self._jobs[owner] is job:
                        self._jobs[owner] = None
                    if invalid_clock and active:
                        self._invalid_collections.add(job.collection_id)
                    self._condition.notify_all()
                    continue
                job.claimed = True
            try:
                raw_result: object | None = reader(self._host, job.query, job.now)
            except Exception:
                raw_result = None
            if raw_result is None:
                result = None
            else:
                try:
                    node = build_node_workloads(
                        self._host,
                        job.query,
                        job.now,
                        {owner: raw_result},
                    )
                    result = next(
                        source for source in node.sources if source.owner is owner
                    )
                except Exception:
                    result = None
            completed_at = _read_clock(self._monotonic)
            with self._condition:
                if self._jobs[owner] is job:
                    self._jobs[owner] = None
                if (
                    (completed_at is None or completed_at < job.started_at)
                    and not self._closed
                    and self._active == job.collection_id
                ):
                    self._invalid_collections.add(job.collection_id)
                timely = (
                    completed_at is not None
                    and completed_at >= job.started_at
                    and completed_at <= job.deadline
                )
                if (
                    timely
                    and not self._closed
                    and self._active == job.collection_id
                ):
                    self._results.setdefault(job.collection_id, {})[owner] = result
                self._condition.notify_all()

    def collect(self, query: WorkloadQuery, now: datetime) -> NodeResult:
        """Return one canonical node result without waiting beyond 1.5 seconds."""
        fallback = build_node_workloads(self._host, query, now, {})
        copied_query = _copy_query(query)
        copied_now = fallback.collection_timestamp
        collection_id: int | None = None
        with self._condition:
            if self._closed or self._active is not None:
                return fallback
            self._collection_id += 1
            collection_id = self._collection_id
            self._active = collection_id
            self._results[collection_id] = {}
        try:
            started_at = _read_clock(self._monotonic)
            if started_at is None:
                with self._condition:
                    self._abandon_locked(collection_id)
                return fallback
            deadline = started_at + _COLLECTION_SECONDS
            with self._condition:
                if self._closed or self._active != collection_id:
                    self._abandon_locked(collection_id)
                    return fallback
                expected: set[WorkloadOwner] = set()
                for owner in self._jobs:
                    if self._jobs[owner] is None:
                        self._jobs[owner] = _Job(
                            collection_id,
                            copied_query,
                            copied_now,
                            started_at,
                            deadline,
                        )
                        if self._start_worker_locked(owner):
                            expected.add(owner)
                        else:
                            self._jobs[owner] = None
                self._condition.notify_all()

            previous_clock = started_at
            while True:
                current = _read_clock(self._monotonic)
                if current is None or current < previous_clock:
                    with self._condition:
                        self._abandon_locked(collection_id)
                    return fallback
                previous_clock = current
                with self._condition:
                    if (
                        self._closed
                        or self._active != collection_id
                        or collection_id in self._invalid_collections
                    ):
                        self._abandon_locked(collection_id)
                        return fallback
                    completed = self._results.get(collection_id, {})
                    if expected <= completed.keys():
                        results = dict(completed)
                        self._abandon_locked(collection_id)
                        break
                    remaining = deadline - current
                    if remaining <= 0:
                        results = dict(completed)
                        self._abandon_locked(collection_id)
                        break
                    self._condition.wait(timeout=remaining)
        except Exception:
            with self._condition:
                if collection_id is not None:
                    self._abandon_locked(collection_id)
            return fallback

        return build_node_workloads(self._host, copied_query, copied_now, results)

    def close(self) -> None:
        """Abandon pending work without waiting for callbacks to finish."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._results.clear()
            self._invalid_collections.clear()
            for owner, job in tuple(self._jobs.items()):
                if job is not None and not job.claimed:
                    self._jobs[owner] = None
            self._active = None
            self._condition.notify_all()


__all__ = ["NodeWorkloadCollector"]
