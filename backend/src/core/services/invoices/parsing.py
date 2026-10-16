"""Docling document parsing for the invoice module (PDF, Word, images)."""

import asyncio
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)

logger = logging.getLogger(__name__)

_OCR_MODELS = Path(__file__).resolve().parents[4] / "models" / "rapidocr"

_MARKER = "<!-- image -->"
_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
_MC_FALLBACK = (
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
)
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_R_EMBED = _R + "embed"
_R_ID = _R + "id"

SUPPORTED_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
)


class InvoiceParseError(Exception):
    """The file could not be read by Docling."""


@dataclass(frozen=True)
class ParsedDocument:
    file_name: str
    page_count: int
    markdown: str


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    # Building the converter loads the layout/OCR models; do it once per process.
    # RapidOCR is pinned to local ONNX files (scripts/download_ocr_models.py) because
    # its default model host, modelscope.cn, is unreachable from some networks.
    ocr = RapidOcrOptions(
        backend="onnxruntime",
        det_model_path=str(_OCR_MODELS / "PP-OCRv4" / "ch_PP-OCRv4_det_infer.onnx"),
        rec_model_path=str(_OCR_MODELS / "PP-OCRv4" / "ch_PP-OCRv4_rec_infer.onnx"),
        cls_model_path=str(
            _OCR_MODELS / "PP-OCRv1" / "ch_ppocr_mobile_v2.0_cls_infer.onnx"
        ),
    )
    pipeline = PdfPipelineOptions(ocr_options=ocr)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline),
        }
    )


def _docx_image_names_in_order(zf: zipfile.ZipFile) -> list[str]:
    """Names of embedded images in word/document.xml, in reading order.

    Docling reads Word text/tables itself but never OCRs pictures, so we walk the
    XML (which is already stored in reading order) to line images up with the
    `<!-- image -->` markers Docling leaves behind.
    """
    rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}

    names: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == _MC_FALLBACK:  # legacy duplicate of the DrawingML image
            return
        rid = None
        if node.tag == _A_BLIP:
            rid = node.get(_R_EMBED)
        elif node.tag == _V_IMAGEDATA:
            rid = node.get(_R_ID)
        if rid and (target := targets.get(rid)):
            names.append("word/" + target.lstrip("/").removeprefix("word/"))
        for child in node:
            walk(child)

    walk(ET.fromstring(zf.read("word/document.xml")))
    return names


def _ocr_image_bytes(name: str, data: bytes) -> str:
    result = _converter().convert(DocumentStream(name=name, stream=BytesIO(data)))
    return result.document.export_to_markdown().strip()


def _add_docx_image_text(path: Path, markdown: str) -> str:
    with zipfile.ZipFile(path) as zf:
        names = _docx_image_names_in_order(zf)
        texts: list[str] = []
        cache: dict[str, str] = {}
        for name in names:
            if name not in cache:
                try:
                    cache[name] = _ocr_image_bytes(Path(name).name, zf.read(name))
                except Exception:
                    logger.warning("OCR failed for %s in %s", name, path.name)
                    cache[name] = ""
            texts.append(cache[name])

    if not any(texts):
        return markdown

    def wrap(text: str) -> str:
        return f"[embedded image]\n{text}\n[/embedded image]" if text else _MARKER

    if markdown.count(_MARKER) == len(texts):
        parts = markdown.split(_MARKER)
        merged = parts[0]
        for text, tail in zip(texts, parts[1:], strict=True):
            merged += wrap(text) + tail
        return merged

    # Markers and images disagree (floating images, headers, ...): don't guess a position.
    logger.warning(
        "%s: %d image markers vs %d images; appending image text at the end",
        path.name,
        markdown.count(_MARKER),
        len(texts),
    )
    return markdown + "".join(f"\n\n{wrap(t)}" for t in texts if t)


def _parse_blocking(path: Path) -> ParsedDocument:
    try:
        result = _converter().convert(path)
        doc = result.document
        markdown = doc.export_to_markdown()
        if path.suffix.lower() == ".docx":
            markdown = _add_docx_image_text(path, markdown)
    except Exception as exc:
        raise InvoiceParseError(f"Docling failed on {path.name}: {exc}") from exc

    return ParsedDocument(
        file_name=path.name,
        page_count=doc.num_pages(),
        markdown=markdown,
    )


async def parse_document(file_path: str | Path) -> ParsedDocument:
    path = Path(file_path)
    if not path.is_file():
        raise InvoiceParseError(f"File not found: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvoiceParseError(
            f"Unsupported file type '{path.suffix}' for {path.name}; "
            f"supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # Docling is CPU-bound and synchronous; keep it off the event loop.
    return await asyncio.to_thread(_parse_blocking, path)
