"""Upload gate and storage for uploaded documents (invoices and purchase orders; Flow 1, steps 0-1).

Gate: reject zero-byte, over MAX_FILE_MB, unsupported type, magic bytes that do not
match the extension, and PDFs over MAX_PAGES. Accepted files are stored on disk under
their sha256 and get a source_documents row; an identical file already stored is
reported as a duplicate and nothing new is written.
"""

import asyncio
import hashlib
import io
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from constants.constants import MAX_FILE_MB, MAX_PAGES
from core.exceptions.errors import UploadRejected
from core.services.jobs import queue
from core.services.invoices.parsing import SUPPORTED_EXTENSIONS
from data.models.postgres import User
from data.models.postgres.enums import DocType, UploadedVia
from data.repositories import unit_of_work
from data.repositories.invoices import documents_repo, invoices_repo
from data.repositories.unit_of_work import DuplicateRecordError

MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


@dataclass(frozen=True)
class StoredUpload:
    source_document_id: uuid.UUID
    duplicate: bool


async def _record_duplicate(
    db: AsyncSession, actor: User, original_id: uuid.UUID, file_name: str
) -> StoredUpload:
    """An identical file is turned away at the door (409), and the attempt is kept on the rejected
    list, pointing at the original; nothing new is stored."""
    original = await documents_repo.get_document(db, original_id)
    await invoices_repo.insert_rejected(
        db,
        source_document_id=None,
        duplicate_of_source_document_id=original_id,
        attempted_file_name=Path(file_name).name,
        extracted_snapshot={"kind": "duplicate"},
        rejection_reasons=[
            {
                "code": "duplicate_upload",
                "field": None,
                "severity": "fail",
                "message": f"This file was already uploaded as '{original.file_name if original else original_id}'",
            }
        ],
        created_by=actor.id,
        updated_by=actor.id,
    )
    await unit_of_work.commit(db)
    return StoredUpload(original_id, duplicate=True)


def signature_matches(ext: str, data: bytes) -> bool:
    if ext == ".pdf":
        return data.startswith(b"%PDF-")
    if ext == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            return False
        try:
            return "word/document.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return False
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if ext in {".tif", ".tiff"}:
        return data.startswith((b"II*\x00", b"MM\x00*"))
    if ext == ".bmp":
        return data.startswith(b"BM")
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _pdf_page_count(data: bytes) -> int | None:
    try:
        return len(pdfium.PdfDocument(data))
    except Exception:
        return None  # unreadable PDFs pass the gate and fail at parse time


def check_upload(file_name: str, data: bytes) -> DocType:
    """Raise UploadRejected unless the file passes the gate; return its doc_type."""
    ext = Path(file_name).suffix.lower()
    if not data:
        raise UploadRejected("File is empty")
    if len(data) > MAX_FILE_BYTES:
        raise UploadRejected(f"File is larger than {MAX_FILE_MB} MB")
    if ext not in SUPPORTED_EXTENSIONS:
        raise UploadRejected(
            f"Unsupported file type '{ext or file_name}'; "
            f"supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if not signature_matches(ext, data):
        raise UploadRejected(f"File content does not look like a {ext} file")
    if ext == ".pdf":
        pages = _pdf_page_count(data)
        if pages is not None and pages > MAX_PAGES:
            raise UploadRejected(f"PDF has {pages} pages; the limit is {MAX_PAGES}")
    if ext == ".pdf":
        return DocType.PDF
    return DocType.DOCX if ext == ".docx" else DocType.IMAGE


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def store_upload(
    db: AsyncSession, actor: User, file_name: str, data: bytes
) -> StoredUpload:
    """Gate, store under the sha256 and record. The document kind is not known yet: the
    classifier (or a person) decides it later; `doc_kind` starts at its default."""
    doc_type = await asyncio.to_thread(check_upload, file_name, data)
    file_hash = hashlib.sha256(data).hexdigest()

    # Name by hash, never by the client's file name (no path tricks, no collisions).
    path = Path(get_settings().raw_files_dir) / f"{file_hash}{Path(file_name).suffix.lower()}"

    existing = await documents_repo.document_id_by_hash(db, file_hash)
    if existing:
        return await _record_duplicate(db, actor, existing, file_name)
    await asyncio.to_thread(_write_file, path, data)

    try:
        document = await documents_repo.insert_document(
            db,
            doc_type=doc_type,
            uploaded_via=UploadedVia.UPLOAD,
            file_name=Path(file_name).name,
            file_path=str(path),
            file_hash=file_hash,
            created_by=actor.id,
        )
        # The job is written in the same transaction as the document: no document without work.
        await queue.enqueue(
            db, job_type=queue.JOB_INTAKE, entity_id=document.id, created_by=actor.id
        )
        await unit_of_work.commit(db)
    except DuplicateRecordError:  # concurrent identical upload won the race on file_hash
        winner = await documents_repo.document_id_by_hash(db, file_hash)
        if winner is None:
            await _drop_unrecorded_file(db, path, file_hash)
            raise
        return await _record_duplicate(db, actor, winner, file_name)
    except Exception:  # the row was not saved (database trouble): do not leave the file on disk with no row
        await _drop_unrecorded_file(db, path, file_hash)
        raise
    return StoredUpload(document.id, duplicate=False)


async def _drop_unrecorded_file(db: AsyncSession, path: Path, file_hash: str) -> None:
    """The save failed: delete the stored file unless a row for the same content exists by now (a concurrent
    identical upload shares the file, since it is named by its hash). Never hides the error being handled."""
    try:
        await db.rollback()
        if await documents_repo.document_id_by_hash(db, file_hash) is None:
            await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        pass  # cleanup is best effort; the original error is what the caller must see
