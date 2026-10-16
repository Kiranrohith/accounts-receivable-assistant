import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.exceptions.errors import ConflictError
from core.services.documents.intake import process_document
from core.services.documents.queries import get_upload, list_uploads
from core.services.invoices.upload import MAX_FILE_BYTES, store_upload
from data.clients.db import get_db
from data.models.postgres import User
from schemas.documents.uploads import DocumentUploadDetail, DocumentUploadSummary
from schemas.invoices.responses import UploadResult

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=UploadResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an invoice or a purchase order",
    description=(
        "Requires invoices.upload. The one upload entry for invoices and purchase orders: you "
        "do not say which it is. Send one PDF, Word (.docx) or image file as multipart form "
        "data (field name `file`). The file is checked (not empty, at most 20 MB, supported "
        "type, content matches the extension, PDFs at most 30 pages), saved on disk under its "
        "sha256 hash and recorded in source_documents, then parsed once and classified in the "
        "background as an invoice, a purchase order or neither. A confident invoice or "
        "purchase order is extracted and checked; a confident 'neither' is rejected with the "
        "file kept; a doubtful one waits in GET /documents/type-reviews for you to pick the "
        "type. Poll GET /documents/uploads/{source_document_id} for progress. A rejected file "
        "returns 422; a file identical to one already uploaded returns 409 with the existing "
        "document's id."
    ),
    responses={
        409: {"description": "Identical file already uploaded"},
        422: {"description": "File failed the upload checks"},
    },
)
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(
        ...,
        description="The invoice or purchase order file (PDF, .docx or image)",
        json_schema_extra={"format": "binary"},  # makes Swagger UI show a file chooser
    ),
    actor: User = Depends(require_permission("invoices.upload")),
    db: AsyncSession = Depends(get_db),
) -> UploadResult:
    name = file.filename or "unnamed"
    data = await file.read(MAX_FILE_BYTES + 1)  # never buffer more than the limit
    stored = await store_upload(db, actor, name, data)  # raises UploadRejected -> 422
    if stored.duplicate:
        raise ConflictError(
            f"This file was already uploaded (source_document_id={stored.source_document_id})"
        )
    background.add_task(process_document, stored.source_document_id)
    return UploadResult(
        file_name=name, status="accepted", source_document_id=stored.source_document_id
    )


@router.get(
    "/uploads",
    response_model=list[DocumentUploadSummary],
    summary="List uploaded documents",
    description=(
        "Requires invoices.view. Every uploaded document, invoice or purchase order, newest "
        "first. `status` is the one label to show; `document_kind` says what it turned out to be."
    ),
)
async def uploads(
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentUploadSummary]:
    return await list_uploads(db, limit)


@router.get(
    "/uploads/{source_document_id}",
    response_model=DocumentUploadDetail,
    summary="Uploaded document status and detail",
    description=(
        "Requires invoices.view. The same answer whatever the document turns out to be: status, "
        "the reader's classification, the extracted fields, every validation reason and the "
        "step timeline."
    ),
)
async def upload_detail(
    source_document_id: uuid.UUID,
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> DocumentUploadDetail:
    return await get_upload(db, source_document_id)
