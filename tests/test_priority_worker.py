import asyncio
from dataclasses import dataclass

from app.asr.priority_worker import ASRPriorityWorker


@dataclass
class Segment:
    segment_id: str
    is_final: bool
    value: str


def test_normal_draft_stream_not_preempted():
    """A pure DRAFT stream is processed serially, in submission order, with no
    preemption and no discarded fragments."""
    async def scenario():
        order = []

        async def handler(seg):
            order.append(seg.value)
            return seg.value

        worker = ASRPriorityWorker(handler)
        futures = [worker.submit(Segment(str(i), False, f"d{i}")) for i in range(5)]
        for fut in futures:
            await fut
        assert order == [f"d{i}" for i in range(5)]
        assert worker.preemption_count == 0
        assert worker.draft_discarded_count == 0
        await worker.close()

    asyncio.run(scenario())


def test_final_preempts_in_flight_draft():
    """Single in-flight DRAFT + a FINAL: the in-flight DRAFT is interrupted and
    all queued DRAFTs are swept; the FINAL is serviced next."""
    async def scenario():
        order = []
        gate = asyncio.Event()

        async def handler(seg):
            if seg.value == "running":
                await gate.wait()
            order.append(seg.value)
            return seg.value

        worker = ASRPriorityWorker(handler)
        running = worker.submit(Segment("a", False, "running"))
        await asyncio.sleep(0)            # running starts (in-flight, blocked)
        queued = worker.submit(Segment("b", False, "queued"))
        final = worker.submit(Segment("c", True, "final"))
        await asyncio.sleep(0)            # preemption triggers
        assert queued.cancelled()         # queued DRAFT swept (synchronous)
        assert await final == "final"
        assert running.cancelled()        # in-flight DRAFT discarded
        assert order == ["final"]
        assert worker.preemption_count == 1
        assert worker.draft_discarded_count == 2
        assert worker.preemption_save_ms >= 0.0
        await worker.close()

    asyncio.run(scenario())


def test_final_does_not_preempt_in_flight_final():
    """A FINAL in-flight must never be preempted; a subsequent FINAL queues
    normally and is processed in order."""
    async def scenario():
        gate = asyncio.Event()

        async def handler(seg):
            if seg.value == "first":
                await gate.wait()
            return seg.value

        worker = ASRPriorityWorker(handler)
        first = worker.submit(Segment("a", True, "first"))
        await asyncio.sleep(0)
        second = worker.submit(Segment("b", True, "second"))
        await asyncio.sleep(0)
        assert not first.cancelled()      # in-flight FINAL not preempted
        gate.set()
        assert await first == "first"
        assert await second == "second"
        assert worker.preemption_count == 0
        assert worker.draft_discarded_count == 0
        await worker.close()

    asyncio.run(scenario())


def test_final_cancels_pending_draft_same_segment():
    """A FINAL for a segment cancels that segment's pending DRAFT (coalescing)
    and preempts an unrelated in-flight DRAFT."""
    async def scenario():
        gate = asyncio.Event()

        async def handler(seg):
            if seg.value == "block":
                await gate.wait()
            return seg.value

        worker = ASRPriorityWorker(handler)
        block = worker.submit(Segment("block", False, "block"))   # in-flight
        await asyncio.sleep(0)
        same_pending = worker.submit(Segment("same", False, "same-pending"))
        same_final = worker.submit(Segment("same", True, "same-final"))
        await asyncio.sleep(0)
        assert same_pending.cancelled()    # coalesced by same-segment FINAL
        assert await same_final == "same-final"
        assert block.cancelled()           # in-flight DRAFT preempted
        assert worker.preemption_count == 1
        assert worker.draft_discarded_count == 1
        await worker.close()

    asyncio.run(scenario())


def test_resume_draft_after_preemption():
    """After a FINAL is serviced, a newly arriving DRAFT resumes normal
    processing — no residual preemption state."""
    async def scenario():
        order = []
        gate = asyncio.Event()

        async def handler(seg):
            if seg.value == "block":
                await gate.wait()
            order.append(seg.value)
            return seg.value

        worker = ASRPriorityWorker(handler)
        block = worker.submit(Segment("a", False, "block"))
        await asyncio.sleep(0)
        final = worker.submit(Segment("f", True, "final"))
        await asyncio.sleep(0)
        assert await final == "final"
        assert block.cancelled()
        after = worker.submit(Segment("b", False, "after"))
        assert await after == "after"
        assert order == ["final", "after"]
        assert worker.preemption_count == 1
        await worker.close()

    asyncio.run(scenario())


def test_preemption_stress_100():
    """100 consecutive in-flight DRAFT preemptions by FINALs must hold up:
    counters stay accurate, the queue drains, and no task/resource leaks."""
    async def scenario():
        gate = asyncio.Event()

        async def handler(seg):
            if seg.value == "block":
                await gate.wait()
            return seg.value

        worker = ASRPriorityWorker(handler)
        for i in range(100):
            block = worker.submit(Segment(f"b{i}", False, "block"))
            await asyncio.sleep(0)            # block is in-flight (blocked forever)
            final = worker.submit(Segment(f"f{i}", True, f"final{i}"))
            await asyncio.sleep(0)
            assert await final == f"final{i}"  # delivery implies preemption done
            assert block.cancelled()
        assert worker.preemption_count == 100
        assert worker.draft_discarded_count == 100
        assert worker.queue.qsize() == 0
        assert worker._inflight_task is None
        await worker.close()
        await asyncio.sleep(0)                # let the worker task settle

    asyncio.run(scenario())
