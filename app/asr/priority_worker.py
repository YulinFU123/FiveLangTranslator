from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import count
from time import monotonic
from typing import Awaitable, Callable


@dataclass(order=True, slots=True)
class ASRJob:
    priority: int
    sequence: int
    segment_id: str = field(compare=False)
    segment: object = field(compare=False)
    future: asyncio.Future = field(compare=False)
    enqueued_at: float = field(compare=False, default=0.0)


class _DraftPreempted(Exception):
    """Internal signal: an in-flight DRAFT was discarded by preemption."""


class ASRPriorityWorker:
    """Single ASR execution lane with FINAL-first priority, draft coalescing,
    and interruptible DRAFT preemption by FINAL results."""

    FINAL_PRIORITY = 0
    DRAFT_PRIORITY = 10

    def __init__(self, handler: Callable[[object], Awaitable[object]],
                 max_queue_wait_ms: float = 1500.0) -> None:
        self.handler = handler
        self.queue: asyncio.PriorityQueue[ASRJob] = asyncio.PriorityQueue()
        self.sequence = count()
        self.pending_drafts: dict[str, ASRJob] = {}
        self.worker_task: asyncio.Task | None = None
        self.running_job: ASRJob | None = None
        self.closed = False
        # DRAFT segments waiting longer than this are skipped: they are interim
        # and a stale interim frame only delays the next FINAL on this single
        # lane. FINAL segments are never skipped.
        self.max_queue_wait_ms = max_queue_wait_ms
        self.last_queue_wait_ms: float = 0.0
        # Preemption state: a FINAL enqueue increments pending_finals and arms
        # the preempt event so an in-flight DRAFT can be interrupted (<10ms)
        # instead of blocking the lane until it finishes. FINAL is never
        # preemptible, so in-flight FINALs are not interrupted.
        self.pending_finals = 0
        self._preempt_event: asyncio.Event = asyncio.Event()
        self._inflight_task: asyncio.Task | None = None
        self._draft_start: float = 0.0
        # Cumulative preemption telemetry (接入 metrics_changed 统计体系).
        self.preemption_count: int = 0
        self.draft_discarded_count: int = 0
        self.preemption_save_ms: float = 0.0

    def start(self) -> None:
        if self.worker_task is None or self.worker_task.done():
            self.closed = False
            self.worker_task = asyncio.create_task(self._run())

    def submit(self, segment) -> asyncio.Future:
        if self.closed:
            raise RuntimeError("ASR worker is closed")
        self.start()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        is_final = bool(getattr(segment, "is_final", True))
        priority = self.FINAL_PRIORITY if is_final else self.DRAFT_PRIORITY
        if not is_final:
            old = self.pending_drafts.get(segment.segment_id)
            if old and not old.future.done():
                old.future.cancel()
        else:
            old = self.pending_drafts.pop(segment.segment_id, None)
            if old and not old.future.done():
                old.future.cancel()
            # A FINAL arrival arms preemption: any queued/in-flight DRAFT is now
            # superseded and an in-flight DRAFT should be interrupted. Sweep
            # the queue so every waiting interim frame is dropped at once.
            self.pending_finals += 1
            self._preempt_event.set()
            self._sweep_queued_drafts()
        job = ASRJob(priority, next(self.sequence), segment.segment_id, segment, future, monotonic())
        if not is_final:
            self.pending_drafts[segment.segment_id] = job
        self.queue.put_nowait(job)
        return future

    def _sweep_queued_drafts(self) -> None:
        # Discard every DRAFT still waiting in the queue: a FINAL supersedes all
        # interim frames, so they must not consume a recognition pass. FINAL
        # jobs are preserved (re-enqueued) in their original order.
        drained: list[ASRJob] = []
        while not self.queue.empty():
            drained.append(self.queue.get_nowait())
        for job in drained:
            if job.priority == self.DRAFT_PRIORITY:
                if not job.future.done():
                    job.future.cancel()
                    self.draft_discarded_count += 1
            else:
                self.queue.put_nowait(job)

    async def close(self) -> None:
        self.closed = True
        for job in self.pending_drafts.values():
            if not job.future.done():
                job.future.cancel()
        self.pending_drafts.clear()
        if self.running_job and not self.running_job.future.done():
            self.running_job.future.cancel()
        if self._inflight_task and not self._inflight_task.done():
            self._inflight_task.cancel()
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
            self.worker_task = None

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job.future.cancelled():
                    continue
                if self.pending_drafts.get(job.segment_id) is job:
                    self.pending_drafts.pop(job.segment_id, None)
                # Separate queue-wait from compute latency so recognition
                # variance can be attributed to backlog vs. model speed.
                wait_ms = (monotonic() - job.enqueued_at) * 1000.0
                self.last_queue_wait_ms = wait_ms
                is_final = job.priority == self.FINAL_PRIORITY
                if is_final:
                    # Committing a FINAL: account for it and disarm preemption
                    # once no more FINALs are pending (resumes normal DRAFTs).
                    self.pending_finals = max(0, self.pending_finals - 1)
                    if self.pending_finals == 0:
                        self._preempt_event.clear()
                else:
                    # Preemption discard: a FINAL is pending, or this interim
                    # frame is stale. Drop it — never preempt a FINAL.
                    if self.pending_finals > 0 or wait_ms > self.max_queue_wait_ms:
                        self.draft_discarded_count += 1
                        if not job.future.done():
                            job.future.cancel()
                        continue
                self.running_job = job
                if is_final:
                    result = await self.handler(job.segment)
                    if not job.future.done():
                        job.future.set_result(result)
                else:
                    try:
                        result = await self._run_draft(job)
                    except _DraftPreempted:
                        continue
                    if not job.future.done():
                        job.future.set_result(result)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self.running_job = None
                self.queue.task_done()

    async def _run_draft(self, job: ASRJob):
        """Run a DRAFT recognition cooperatively, allowing a pending FINAL to
        preempt it mid-flight (response <= one event-loop tick, ~<10ms).

        Preemption is decided by the preempt flag, not by which future happened
        to finish first: if a FINAL is pending the DRAFT result is discarded
        even when recognition completes in the same tick, so the outcome is
        deterministic rather than dependent on scheduling order.
        """
        self._draft_start = monotonic()
        inflight = asyncio.ensure_future(self.handler(job.segment))
        self._inflight_task = inflight
        event_wait = asyncio.ensure_future(self._preempt_event.wait())
        try:
            done, pending = await asyncio.wait(
                {inflight, event_wait}, return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if event_wait in pending:
                event_wait.cancel()
        if self._preempt_event.is_set():
            # A FINAL is pending: interrupt/discard this DRAFT at once. Cancel the
            # recognition task if it has not finished; we must not await a
            # cancelled task, or we would swallow a worker cancellation on close().
            if not inflight.done():
                inflight.cancel()
            self._inflight_task = None
            elapsed = monotonic() - self._draft_start
            self.preemption_count += 1
            self.draft_discarded_count += 1
            # Approximate latency saved: processing time of the abandoned DRAFT
            # that the FINAL no longer has to wait behind on this single lane.
            self.preemption_save_ms += elapsed
            if not job.future.done():
                job.future.cancel()
            self._preempt_event.clear()
            raise _DraftPreempted()
        # No pending FINAL: the DRAFT finished normally.
        self._inflight_task = None
        return await inflight

    @property
    def depth(self) -> int:
        return self.queue.qsize()
