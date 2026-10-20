"""Entry point of the job worker: `python -m jobs.runner` (from backend/, PYTHONPATH=src).

Also used by the API process in dev (RUN_EMBEDDED_WORKER=true) via start_worker().
"""

import asyncio
import logging

from config.settings import get_settings
from core.services.disputes import clarify
from core.services.documents import intake
from core.services.jobs import queue
from core.services.jobs.worker import Worker
from core.services.payments import rematch

# One handler serves the intake types: the job type tells it whether a person already chose the
# kind. A re-match job only matches an already-identified payment.
HANDLERS: dict[str, queue.Handler] = {
    queue.JOB_INTAKE: intake.handle,
    queue.JOB_INVOICE: intake.handle,
    queue.JOB_PO: intake.handle,
    queue.JOB_PAYMENT: intake.handle,
    queue.JOB_DISPUTE: intake.handle,
    queue.JOB_DISPUTE_PAYMENT: intake.handle,
    queue.JOB_DISPUTE_CLARIFY: clarify.handle,
    queue.JOB_PAYMENT_REMATCH: rematch.handle,
}


async def start_worker(concurrency: int | None = None) -> Worker:
    worker = Worker(HANDLERS, concurrency=concurrency or get_settings().worker_concurrency)
    await worker.start()
    return worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = await start_worker()
    try:
        await asyncio.Event().wait()  # until SIGTERM / Ctrl+C cancels this task
    finally:
        await worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
