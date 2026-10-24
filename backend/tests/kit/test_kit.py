"""The seed kit, run through the real matcher: `pytest -m kit` (the plain `pytest` leaves it out because it
needs Postgres and the kit folder). The runner does the work in a subprocess against a throwaway database
created here and dropped afterwards; see runner.py for what runs and what is still pending.

The pending list is the contract with the modules not built yet: every non-payment e-mail waits for triage, and each future
module (mail triage, the reader's corrupt-file path, the mail worker's hash pre-check) removes its ids from the declared
list as part of its own acceptance, until it is empty. A new pending id, or one that quietly starts running, fails
the test, so nothing is ever skipped silently.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[2]
KIT = Path(os.environ.get("KIT_DIR") or Path(__file__).resolve().parent / "data")

# Pending = every e-mail that is not a payment advice (disputes, purchase orders, general mail wait for triage), read from
# the kit's own key, plus these payment advices that wait for another module.
EXPECTED_PENDING_PAYMENTS = {
    "P09": "reader on a corrupt PDF",
    "P32": "mail worker attachment-hash pre-check",
    "P39": "triage",
    "P43": "triage 'both'",
}
# Score differences between the key and the engine that are not decided yet (everything else about them matches).
EXPECTED_RULINGS = {"P13b", "P14b"}

pytestmark = pytest.mark.kit


async def _admin(sql: str, database_url: str) -> None:
    url = make_url(database_url).set(database="postgres")
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(sql)
    await engine.dispose()


@pytest.fixture(scope="module")
def kit_result(tmp_path_factory):
    from config.settings import get_settings

    base_url = get_settings().database_url
    if not (KIT / "payment_advices" / "payments_expected.json").exists():
        pytest.fail(f"the seed kit is not at {KIT} (set KIT_DIR): the kit run cannot be skipped")
    name = f"ar_kit_{uuid.uuid4().hex[:8]}"
    url = str(make_url(base_url).set(database=name).render_as_string(hide_password=False))
    out = tmp_path_factory.mktemp("kit") / "result.json"
    asyncio.run(_admin(f"CREATE DATABASE {name}", base_url))
    try:
        env = {**os.environ, "DATABASE_URL": url, "KIT_DIR": str(KIT), "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
        run = subprocess.run(
            [sys.executable, "-m", "tests.kit.runner", "--json", str(out)], cwd=BACKEND, env=env, capture_output=True, text=True
        )
        report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        yield run, report
    finally:
        asyncio.run(_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", base_url))


def test_the_kit_runs_and_every_payment_matches_its_answer_key(kit_result):
    run, report = kit_result
    assert report is not None, run.stdout[-2000:] + run.stderr[-2000:]
    failed = [r for r in report["results"] if r["status"] == "fail"]
    assert not failed, "\n".join(f"{r['id']}: " + "; ".join(r["diffs"]) for r in failed)


def _declared_pending() -> set[str]:
    key = json.loads((KIT / "payment_advices" / "payments_expected.json").read_text(encoding="utf-8"))["emails"]
    return {e["id"] for e in key if e["kind"] != "payment_advice"} | set(EXPECTED_PENDING_PAYMENTS)


def test_what_is_pending_is_exactly_what_is_declared(kit_result):
    _, report = kit_result
    pending = {r["id"] for r in report["results"] if r["status"] == "pending"}
    declared = _declared_pending()
    assert pending == declared, (
        f"pending changed: new {sorted(pending - declared)}, now running {sorted(declared - pending)}; "
        "update EXPECTED_PENDING_PAYMENTS with the module that did it"
    )


def test_the_undecided_score_differences_are_exactly_the_declared_ones(kit_result):
    _, report = kit_result
    rulings = {r["id"] for r in report["results"] if r["status"] == "ruling"}
    assert rulings == EXPECTED_RULINGS, f"score rulings changed: {sorted(rulings ^ EXPECTED_RULINGS)}"


def test_the_money_identities_hold_after_the_whole_kit(kit_result):
    _, report = kit_result
    row = next(r for r in report["results"] if r["id"] == "money-identities")
    assert row["status"] == "pass", row["diffs"]
