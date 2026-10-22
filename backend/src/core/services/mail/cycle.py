"""The `gmail_poll` schedule: finish what a crash left halfway, then take new mail.

A mail that is classified but not Processed (the process died between the decision and the hand-off, or it was
classified before hand-off existed) is handed off first. That runs even when polling is off: the mails are already ours.
"""

import logging
from datetime import datetime

from core.services.mail import handoff, ingest
from data.clients.db import session_scope
from data.repositories.mail import emails_repo

logger = logging.getLogger(__name__)


async def sweep() -> int:
    handed = 0
    async with session_scope() as db:
        waiting = await emails_repo.list_classified_waiting(db)
    for email_id in waiting:
        try:
            async with session_scope() as db:
                email = await emails_repo.lock_email(db, email_id)
                if email is None or email.status is not None or email.classification is None:
                    continue
                result = await handoff.hand_off(db, email, email.classification)
                await db.commit()
            await handoff.label_all(email, result.labels)
            handed += 1
        except Exception:
            logger.exception("could not hand off mail %s", email_id)
    return handed


async def run(now: datetime) -> dict:
    swept = await sweep()
    polled = await ingest.run_scheduled(now)
    return {"handed_off": swept, **polled}
