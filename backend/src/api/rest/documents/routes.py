import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.rest.dependencies import require_permission
from core.exceptions.errors import ConflictError
from core.services.documents.files import find_original
from core.services.documents.queries import get_upload, list_uploads
from core.services.documents.rerun import rerun_upload
from core.services.invoices.upload import MAX_FILE_BYTES, store_upload
from data.clients.db import get_db
from data.models.postgres import User
from schemas.documents.uploads import DocumentUploadDetail, DocumentUploadSummary, RerunResult
from schemas.invoices.responses import UploadResult

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=UploadResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an invoice, a purchase order, a payment advice or a customer complaint",
    description=(
        "Requires invoices.upload. The one upload entry for invoices, purchase orders, payment advices and customer complaints: you "
        "do not say which it is. Send one PDF, Word (.docx) or image file as multipart form "
        "data (field name `file`). The file is checked (not empty, at most 20 MB, supported "
        "type, content matches the extension, PDFs at most 30 pages), saved on disk under its "
        "sha256 hash and recorded in source_documents together with a queued job; a worker then "
        "parses it once and classifies it as an invoice, a purchase order, a payment advice, a customer complaint or none of them. A confident invoice, "
        "purchase order or payment advice is extracted and checked; a confident complaint raises a dispute (one per invoice it names; one that also holds a payment advice raises the dispute first, then the payment is matched); a confident 'neither' is rejected with the "
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
    file: UploadFile = File(
        ...,
        description="The invoice, purchase order, payment advice or complaint file (PDF, .docx or image)",
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
    return UploadResult(
        file_name=name, status="accepted", source_document_id=stored.source_document_id
    )


@router.post(
    "/uploads/{source_document_id}/rerun",
    response_model=RerunResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run a failed upload",
    description=(
        "Requires failed_items.rerun. For an upload whose status is Failed (the file could not be "
        "read, or the model stayed unavailable until the retries ran out). Queues it again, "
        "as the same kind of job; poll GET /documents/uploads/{source_document_id}."
    ),
    responses={
        404: {"description": "No such upload"},
        409: {"description": "Its processing has not failed"},
        422: {"description": "The file is no longer available"},
    },
)
async def rerun(
    source_document_id: uuid.UUID,
    user: User = Depends(require_permission("failed_items.rerun")),
    db: AsyncSession = Depends(get_db),
) -> RerunResult:
    return await rerun_upload(db, user, source_document_id)


@router.get(
    "/uploads/{source_document_id}/file",
    summary="View the original uploaded file",
    description=(
        "Requires invoices.view. Sends the file as uploaded, to be shown in the browser: PDFs and "
        "images open in the tab, so a reviewer can check the source without downloading it (use "
        "the browser's own save button to keep a copy). A Word file cannot be shown by a browser "
        "and downloads instead."
    ),
    response_class=FileResponse,
    responses={404: {"description": "No such upload, or its file is no longer available"}},
)
async def original_file(
    source_document_id: uuid.UUID,
    _: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    found = await find_original(db, source_document_id)
    return FileResponse(
        found.path,
        media_type=found.media_type,
        filename=found.file_name,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/uploads",
    response_model=list[DocumentUploadSummary],
    summary="List uploaded documents",
    description=(
        "Requires invoices.view. Every uploaded document, invoice, purchase order or payment advice, newest "
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
