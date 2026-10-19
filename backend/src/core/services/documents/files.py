"""Locate the original file of an uploaded document, for viewing in the browser."""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from core.exceptions.errors import NotFoundError
from data.repositories.invoices import documents_repo

# What the browser should be told. PDFs and images open in the tab; a Word file is sent with the
# same "inline" disposition but a browser cannot show it, so it ends up downloading.
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class OriginalFile:
    path: Path
    media_type: str
    file_name: str


async def find_original(db: AsyncSession, document_id: uuid.UUID) -> OriginalFile:
    document = await documents_repo.get_document(db, document_id)
    if document is None or not document.file_path:
        raise NotFoundError("The original file is not available")
    path = Path(document.file_path).resolve()
    root = Path(get_settings().raw_files_dir).resolve()
    # The path comes from our own table, but never serve anything outside the upload folder.
    if not path.is_relative_to(root) or not path.is_file():
        raise NotFoundError("The original file is not available")
    return OriginalFile(
        path=path,
        media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        file_name=document.file_name or path.name,
    )
