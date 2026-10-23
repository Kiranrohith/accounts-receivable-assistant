# One image for the three things the backend does: the API, the job worker (python -m jobs.runner) and the one-off
# release step (scripts/release.sh: migrations, then the first admin). The command picks which.
#
# Built in two stages: the first installs the dependencies and downloads every model the document reader needs, so
# nothing is fetched from the internet when a container starts (the reader runs offline).

FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock change (Linux gets CPU-only PyTorch)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# the models: RapidOCR's ONNX files (scripts/download_ocr_models.py) and Docling's layout and table models
COPY scripts ./scripts
RUN /app/.venv/bin/python scripts/download_ocr_models.py \
 && /app/.venv/bin/python -c "from pathlib import Path; from docling.utils.model_downloader import download_models; download_models(output_dir=Path('/opt/docling-models'), with_layout=True, with_tableformer=True, with_code_formula=False, with_picture_classifier=False, with_rapidocr=False)"


FROM python:3.12-slim

# libgomp: PyTorch and ONNX Runtime; libglib: OpenCV; tzdata: the IST business calendar
# (the package sources are switched to https: plain http is blocked on some networks)
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources  && apt-get update && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home app \
 && mkdir -p /data/raw && chown app:app /data/raw

WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/models ./models
COPY --from=build /opt/docling-models /opt/docling-models
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    HF_HUB_OFFLINE=1 \
    RAW_FILES_DIR=/data/raw

USER app
EXPOSE 8000
CMD ["uvicorn", "api.rest.app:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src"]
