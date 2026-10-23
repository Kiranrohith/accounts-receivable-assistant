"""Test helper: run a real worker in-process and make uploads wait for their job.

The API no longer processes documents itself, so a test needs a worker. `install(client)` makes
POST /documents/upload return only when the document's jobs are finished, which is how the old
background-task flow behaved under httpx's ASGI transport.
"""

import asyncio
import sys

sys.path.insert(0, "src")
from sqlalchemy import text

import core.services.jobs.queue as queue
import core.services.jobs.worker as worker_module
from data.clients.db import SessionLocal
from jobs.runner import start_worker as _start_worker

DONE = ("Succeeded", "Failed", "DeadLettered")


async def start_worker(concurrency: int = 2):
    worker_module.poll_seconds = (0.05, 0.15)
    queue.retry_delays_seconds = (0.2, 0.2, 0.2)  # production waits 60s / 300s / 900s
    return await _start_worker(concurrency)


async def wait_idle(doc_id: str, timeout: float = 900) -> None:
    for _ in range(int(timeout * 4)):
        async with SessionLocal() as db:
            states = (
                await db.execute(text("select state::text from jobs where entity_id=:i"), {"i": doc_id})
            ).scalars().all()
        if states and all(s in DONE for s in states):
            return
        await asyncio.sleep(0.25)


def install(client) -> None:  # noqa: ANN001
    real_post = client.post

    async def post(url, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        response = await real_post(url, *args, **kwargs)
        if str(url).endswith("/documents/upload") and response.status_code == 202:
            await wait_idle(response.json()["source_document_id"])
        return response

    client.post = post
