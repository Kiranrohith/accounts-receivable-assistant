"""The worker: N claim loops plus a watchdog, in one process.

Run by `python -m jobs.runner` (its own container in production) or, in dev, started by the API
process itself. Many workers can run at once: claiming is FOR UPDATE SKIP LOCKED.
"""

import asyncio
import logging
import os
import random
import socket
import uuid
from contextlib import suppress

from constants.constants import SCHEDULER_TICK_SECONDS, WATCHDOG_INTERVAL_SECONDS, WORKER_POLL_SECONDS
from core.services.jobs import queue, scheduler
from data.clients.db import session_scope

logger = logging.getLogger(__name__)

# Module-level so tests can shorten them.
poll_seconds: tuple[float, float] = WORKER_POLL_SECONDS
watchdog_interval_seconds: float = WATCHDOG_INTERVAL_SECONDS
scheduler_tick_seconds: float = SCHEDULER_TICK_SECONDS


class Worker:
    def __init__(
        self, handlers: dict[str, queue.Handler], *, concurrency: int = 2, run_schedules: bool = True
    ) -> None:
        self.handlers = handlers
        self.concurrency = max(1, concurrency)
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:4]}"
        self._stopping = asyncio.Event()
        self._slots: list[asyncio.Task] = []
        self._watchdog: asyncio.Task | None = None
        self._scheduler: asyncio.Task | None = None
        self.run_schedules = run_schedules

    async def start(self) -> None:
        """Reclaim whatever a previous process left behind, then start working."""
        await self._watch_once()  # the boot-time pass
        self._slots = [
            asyncio.create_task(self._slot(n), name=f"{self.worker_id}-{n}")
            for n in range(self.concurrency)
        ]
        self._watchdog = asyncio.create_task(self._watch_forever(), name=f"{self.worker_id}-watchdog")
        if self.run_schedules:
            await self._seed_schedules()
            self._scheduler = asyncio.create_task(self._schedule_forever(), name=f"{self.worker_id}-scheduler")
        logger.info("worker %s started with %d slots", self.worker_id, self.concurrency)

    async def stop(self, grace_seconds: float = 20) -> None:
        """Stop claiming; give running jobs `grace_seconds` to finish, then put them back."""
        self._stopping.set()
        if self._slots:
            _, pending = await asyncio.wait(self._slots, timeout=grace_seconds)
            for task in pending:
                task.cancel()  # execute() requeues the job it was running
            await asyncio.gather(*pending, return_exceptions=True)
        for task in (self._watchdog, self._scheduler):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        logger.info("worker %s stopped", self.worker_id)

    async def _sleep(self, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)

    async def _slot(self, number: int) -> None:
        slot_id = f"{self.worker_id}-{number}"
        while not self._stopping.is_set():
            try:
                async with session_scope() as db:
                    job = await queue.claim(db, slot_id)
            except Exception:  # database unreachable: wait and try again
                logger.exception("claim failed")
                await self._sleep(random.uniform(*poll_seconds))
                continue
            if job is None:
                await self._sleep(random.uniform(*poll_seconds))
                continue
            try:
                await queue.execute(job.id, slot_id, self.handlers)
            except asyncio.CancelledError:
                raise
            except Exception:  # execute() records job failures itself; this only keeps the slot alive
                logger.exception("job %s: could not record its outcome", job.id)

    async def _watch_once(self) -> None:
        try:
            async with session_scope() as db:
                result = await queue.watchdog_pass(db)
            if result.requeued or result.dead_lettered or result.timed_out:
                logger.warning("watchdog: %s", result)
        except Exception:
            logger.exception("watchdog pass failed")

    async def _watch_forever(self) -> None:
        while True:
            await asyncio.sleep(watchdog_interval_seconds)
            await self._watch_once()

    async def _seed_schedules(self) -> None:
        try:
            await scheduler.seed()
        except Exception:
            logger.exception("could not seed the schedules")

    async def _schedule_forever(self) -> None:
        """Every few seconds run whatever recurring work is due (see core.services.jobs.scheduler)."""
        while True:
            try:
                ran = await scheduler.tick()
                if ran:
                    logger.info("schedules run: %s", ", ".join(ran))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(scheduler_tick_seconds)
