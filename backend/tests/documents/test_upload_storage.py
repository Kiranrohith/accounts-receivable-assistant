"""A stored upload file never outlives a failed save: the file is named by its hash and written before the
database row, so when the row cannot be saved the file must go (unless a row for the same content exists)."""

import asyncio
import hashlib
import uuid
from types import SimpleNamespace

import pytest

from config.settings import get_settings
from core.services.invoices import upload
from data.repositories.unit_of_work import DuplicateRecordError


def run(coro):
    return asyncio.run(coro)


PDF = b"%PDF-1.4\n%%EOF"
ACTOR = SimpleNamespace(id=uuid.uuid4())


class FakeDb:
    def __init__(self):
        self.rolled_back = 0

    async def rollback(self):
        self.rolled_back += 1


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "raw_files_dir", str(tmp_path))
    return tmp_path


def _wire(monkeypatch, *, insert, hash_lookup):
    async def no_existing_row(db, file_hash):
        return hash_lookup.pop(0)

    monkeypatch.setattr(upload.documents_repo, "document_id_by_hash", no_existing_row)
    monkeypatch.setattr(upload.documents_repo, "insert_document", insert)


def test_a_failed_save_removes_the_stored_file(raw_dir, monkeypatch):
    async def insert(db, **kw):
        raise RuntimeError("database went away")

    _wire(monkeypatch, insert=insert, hash_lookup=[None, None])  # before the write, then after the failure
    db = FakeDb()
    with pytest.raises(RuntimeError, match="database went away"):
        run(upload.store_upload(db, ACTOR, "a.pdf", PDF))
    assert list(raw_dir.iterdir()) == []
    assert db.rolled_back == 1


def test_a_lost_race_keeps_the_file_the_winner_uses(raw_dir, monkeypatch):
    winner = uuid.uuid4()
    recorded = []

    async def insert(db, **kw):
        raise DuplicateRecordError("race")

    async def record_duplicate(db, actor, existing, name):
        recorded.append(existing)
        return upload.StoredUpload(existing, duplicate=True)

    _wire(monkeypatch, insert=insert, hash_lookup=[None, winner])
    monkeypatch.setattr(upload, "_record_duplicate", record_duplicate)
    result = run(upload.store_upload(FakeDb(), ACTOR, "a.pdf", PDF))
    assert result.duplicate and recorded == [winner]
    assert [p.name for p in raw_dir.iterdir()] == [hashlib.sha256(PDF).hexdigest() + ".pdf"]


def test_the_file_stays_when_a_row_for_the_same_content_exists_after_the_failure(raw_dir, monkeypatch):
    async def insert(db, **kw):
        raise RuntimeError("boom")

    _wire(monkeypatch, insert=insert, hash_lookup=[None, uuid.uuid4()])  # someone else saved the same bytes meanwhile
    with pytest.raises(RuntimeError):
        run(upload.store_upload(FakeDb(), ACTOR, "a.pdf", PDF))
    assert len(list(raw_dir.iterdir())) == 1


def test_a_cleanup_that_itself_fails_does_not_hide_the_real_error(raw_dir, monkeypatch):
    async def insert(db, **kw):
        raise RuntimeError("the real error")

    class BrokenDb(FakeDb):
        async def rollback(self):
            raise ConnectionError("cannot even roll back")

    _wire(monkeypatch, insert=insert, hash_lookup=[None])
    with pytest.raises(RuntimeError, match="the real error"):
        run(upload.store_upload(BrokenDb(), ACTOR, "a.pdf", PDF))
