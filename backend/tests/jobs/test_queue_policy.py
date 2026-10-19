import pytest
from sqlalchemy.exc import OperationalError

from constants.constants import (
    BACKOFF_JITTER,
    HEARTBEAT_SECONDS,
    OUTAGE_BACKOFF_SECONDS,
    STALE_HEARTBEAT_SECONDS,
    WATCHDOG_INTERVAL_SECONDS,
)
from core.services.jobs import queue


def test_attempts_are_the_first_run_plus_one_per_backoff_step():
    assert queue.MAX_ATTEMPTS == 1 + len(OUTAGE_BACKOFF_SECONDS) == 4


def test_the_timing_constants_keep_their_documented_relationships():
    # a worker that missed two beats is still alive; the watchdog looks often enough to be quick
    assert STALE_HEARTBEAT_SECONDS >= 3 * HEARTBEAT_SECONDS
    assert WATCHDOG_INTERVAL_SECONDS < STALE_HEARTBEAT_SECONDS


@pytest.mark.parametrize("base", OUTAGE_BACKOFF_SECONDS)
def test_backoff_is_jittered_within_30_percent(base):
    spread = float(BACKOFF_JITTER)
    delays = [queue._jittered(base) for _ in range(200)]
    assert all(base * (1 - spread) <= d <= base * (1 + spread) for d in delays)
    assert len(set(delays)) > 1  # actually random, not a constant


def test_only_infrastructure_blips_are_transient():
    assert queue._is_transient(ConnectionError("reset"))
    assert queue._is_transient(TimeoutError())
    assert queue._is_transient(OperationalError("select 1", {}, Exception("server closed")))
    assert not queue._is_transient(ValueError("a bug"))
    assert not queue._is_transient(KeyError("x"))


def test_job_types_are_distinct_and_documented():
    assert len({queue.JOB_INTAKE, queue.JOB_INVOICE, queue.JOB_PO}) == 3
