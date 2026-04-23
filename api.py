"""
FastAPI Backend — Semantic JSONL Conversion API
=================================================
Exposes REST endpoints to upload enterprise files and convert them
into semantically chunked JSONL via the SemanticRouter pipeline.

Run:
    uvicorn api:app --reload --port 8000

Endpoints:
    POST /convert          — Upload a single file → JSONL conversion
    POST /convert/batch    — Upload multiple files → JSONL conversion
    GET  /files            — List generated JSONL output files
    GET  /files/{filename} — Download a specific JSONL file
    GET  /health           — Health check
    DELETE /files          — Clear all generated JSONL files
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

from semantic_pipeline.models import EmitterConfig
from semantic_pipeline.router import SemanticRouter

# ── Configuration ───────────────────────────────────────────────
JSONL_OUTPUT_DIR = Path(__file__).parent / "jsonl"
UPLOAD_TEMP_DIR = Path(__file__).parent / ".uploads"

# Ensure directories exist
JSONL_OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_TEMP_DIR.mkdir(exist_ok=True)


# ── Response Models ─────────────────────────────────────────────

class ConvertResponse(BaseModel):
    """Response from a successful file conversion."""
    status: str = "success"
    source_file: str
    chunks_produced: int
    jsonl_files: List[str]
    processing_time_ms: float


class BatchConvertResponse(BaseModel):
    """Response from a batch file conversion."""
    status: str = "success"
    files_processed: int
    files_skipped: int
    total_chunks: int
    jsonl_files: List[str]
    processing_time_ms: float
    details: List[dict]


class FileListResponse(BaseModel):
    """Response listing available JSONL files."""
    output_directory: str
    files: List[dict]
    total_files: int
    total_size_bytes: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    pipeline_version: str = "1.0.0"
    supported_extensions: List[str]
    output_directory: str


# ── FastAPI App ─────────────────────────────────────────────────

app = FastAPI(
    title="Semantic JSONL Conversion API",
    description=(
        "Upload enterprise files (.xlsx, .docx, .md, .txt, .json, .yaml) "
        "and convert them into semantically chunked JSONL format for RAG pipelines. "
        "Output files are written to the `jsonl/` directory.\n\n"
        "**NOTE:** For batch file uploading, use the Custom Web Portal at the root (`/`) "
        "instead of Swagger UI, as Swagger does not natively support multi-file selection."
    ),
    version="1.0.0",
)

SUPPORTED = [".xlsx", ".xls", ".docx", ".md", ".json", ".yaml", ".yml", ".txt"]


# ── Helper ──────────────────────────────────────────────────────

def _save_upload(upload: UploadFile) -> Path:
    """Save an uploaded file to the temp directory and return the path."""
    safe_name = Path(upload.filename).name  # strip directory components
    dest = UPLOAD_TEMP_DIR / safe_name

    with open(dest, "wb") as f:
        content = upload.file.read()
        f.write(content)

    return dest


def _get_jsonl_files() -> List[dict]:
    """List all .jsonl files in the output directory."""
    files = []
    for f in sorted(JSONL_OUTPUT_DIR.glob("*.jsonl")):
        files.append({
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "size_human": _human_size(f.stat().st_size),
        })
    return files


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ── Endpoints ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["UI"], include_in_schema=False)
async def serve_ui():
    """Serve the premium drag-and-drop web portal."""
    html_path = Path(__file__).parent / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Web UI not found.")
    return html_path.read_text(encoding="utf-8")


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Check API health and list supported file types."""
    return HealthResponse(
        supported_extensions=SUPPORTED,
        output_directory=str(JSONL_OUTPUT_DIR.resolve()),
    )


@app.post("/convert", response_model=ConvertResponse, tags=["Conversion"])
async def convert_file(
    file: UploadFile = File(..., description="File to convert to JSONL"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
):
    """
    Upload a single file and convert it to semantically chunked JSONL.

    The output `.jsonl` files are written to the `jsonl/` directory.
    Supported formats: .xlsx, .xls, .docx, .md, .txt, .json, .yaml, .yml
    """
    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: {SUPPORTED}",
        )

    # Save to temp
    temp_path = _save_upload(file)

    try:
        start = time.perf_counter()

        # Use source filename as base so each file gets its own JSONL
        stem = Path(file.filename).stem
        config = EmitterConfig(
            output_dir=str(JSONL_OUTPUT_DIR),
            base_filename=stem,
            usecase_id=usecase_id,
            identifier=identifier,
            data_classification=data_classification,
        )

        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(temp_path))

        elapsed_ms = (time.perf_counter() - start) * 1000

        return ConvertResponse(
            source_file=file.filename,
            chunks_produced=len(chunks),
            jsonl_files=[f.name for f in JSONL_OUTPUT_DIR.glob("*.jsonl")],
            processing_time_ms=round(elapsed_ms, 2),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
    finally:
        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()


@app.post("/convert/unchunked", response_model=ConvertResponse, tags=["Conversion"])
async def convert_file_unchunked(
    file: UploadFile = File(..., description="File to convert (whole-file mode)"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
):
    """
    Upload a file and emit exactly **one** JSONL record containing the entire
    parsed content (no chunking). Useful when the downstream model can handle
    full-document context windows.

    The parsers still clean the data (DOCX→Markdown, Excel unmerge, $ref resolve)
    but all output is aggregated into a single `raw_context`.
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: {SUPPORTED}",
        )

    temp_path = _save_upload(file)

    try:
        start = time.perf_counter()

        stem = Path(file.filename).stem
        config = EmitterConfig(
            output_dir=str(JSONL_OUTPUT_DIR),
            base_filename=f"{stem}_unchunked",
            usecase_id=usecase_id,
            identifier=identifier,
            data_classification=data_classification,
        )

        with SemanticRouter(config) as router:
            chunks = router.ingest_file_unchunked(str(temp_path))

        elapsed_ms = (time.perf_counter() - start) * 1000

        return ConvertResponse(
            source_file=file.filename,
            chunks_produced=len(chunks),
            jsonl_files=[f.name for f in JSONL_OUTPUT_DIR.glob("*.jsonl")],
            processing_time_ms=round(elapsed_ms, 2),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/convert/batch", response_model=BatchConvertResponse, tags=["Conversion"])
async def convert_batch(
    files: List[UploadFile] = File(..., description="Files to convert"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
):
    """
    Upload multiple files and convert them all to JSONL in a single batch.

    All output is written to the shared `jsonl/` directory.
    """
    start = time.perf_counter()
    details = []
    total_chunks = 0
    files_skipped = 0

    for upload in files:
        ext = Path(upload.filename).suffix.lower()

        if ext not in SUPPORTED:
            details.append({
                "file": upload.filename,
                "status": "skipped",
                "reason": f"Unsupported type: {ext}",
                "chunks": 0,
            })
            files_skipped += 1
            continue

        temp_path = _save_upload(upload)
        try:
            stem = Path(upload.filename).stem
            config = EmitterConfig(
                output_dir=str(JSONL_OUTPUT_DIR),
                base_filename=stem,
                usecase_id=usecase_id,
                identifier=identifier,
                data_classification=data_classification,
            )
            with SemanticRouter(config) as router:
                chunks = router.ingest_file(str(temp_path))
            total_chunks += len(chunks)
            details.append({
                "file": upload.filename,
                "status": "success",
                "chunks": len(chunks),
            })
        except Exception as e:
            details.append({
                "file": upload.filename,
                "status": "error",
                "reason": str(e),
                "chunks": 0,
            })
            files_skipped += 1
        finally:
            if temp_path.exists():
                temp_path.unlink()

    elapsed_ms = (time.perf_counter() - start) * 1000

    return BatchConvertResponse(
        files_processed=len(files) - files_skipped,
        files_skipped=files_skipped,
        total_chunks=total_chunks,
        jsonl_files=[f.name for f in JSONL_OUTPUT_DIR.glob("*.jsonl")],
        processing_time_ms=round(elapsed_ms, 2),
        details=details,
    )


@app.post("/convert/batch/unchunked", response_model=BatchConvertResponse, tags=["Conversion"])
async def convert_batch_unchunked(
    files: List[UploadFile] = File(..., description="Files to convert in unchunked mode"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
):
    """
    Upload multiple files and convert them all to JSONL in unchunked mode.
    
    Each file produces exactly one JSONL record containing its entire contents.
    All output is written to the shared `jsonl/` directory.
    """
    start = time.perf_counter()
    details = []
    total_chunks = 0
    files_skipped = 0

    for upload in files:
        ext = Path(upload.filename).suffix.lower()

        if ext not in SUPPORTED:
            details.append({
                "file": upload.filename,
                "status": "skipped",
                "reason": f"Unsupported type: {ext}",
                "chunks": 0,
            })
            files_skipped += 1
            continue

        temp_path = _save_upload(upload)
        try:
            stem = Path(upload.filename).stem
            config = EmitterConfig(
                output_dir=str(JSONL_OUTPUT_DIR),
                base_filename=f"{stem}_unchunked",
                usecase_id=usecase_id,
                identifier=identifier,
                data_classification=data_classification,
            )
            with SemanticRouter(config) as router:
                chunks = router.ingest_file_unchunked(str(temp_path))
            total_chunks += len(chunks)
            details.append({
                "file": upload.filename,
                "status": "success",
                "chunks": len(chunks),
            })
        except Exception as e:
            details.append({
                "file": upload.filename,
                "status": "error",
                "reason": str(e),
                "chunks": 0,
            })
            files_skipped += 1
        finally:
            if temp_path.exists():
                temp_path.unlink()

    elapsed_ms = (time.perf_counter() - start) * 1000

    return BatchConvertResponse(
        files_processed=len(files) - files_skipped,
        files_skipped=files_skipped,
        total_chunks=total_chunks,
        jsonl_files=[f.name for f in JSONL_OUTPUT_DIR.glob("*.jsonl")],
        processing_time_ms=round(elapsed_ms, 2),
        details=details,
    )


@app.get("/files", response_model=FileListResponse, tags=["Output Files"])
async def list_output_files():
    """List all generated JSONL files in the output directory."""
    files = _get_jsonl_files()
    total_size = sum(f["size_bytes"] for f in files)

    return FileListResponse(
        output_directory=str(JSONL_OUTPUT_DIR.resolve()),
        files=files,
        total_files=len(files),
        total_size_bytes=total_size,
    )


@app.get("/files/{filename}", tags=["Output Files"])
async def download_file(filename: str):
    """Download a specific JSONL output file."""
    file_path = JSONL_OUTPUT_DIR / filename

    if not file_path.exists() or not file_path.suffix == ".jsonl":
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/x-ndjson",
    )


@app.delete("/files", tags=["Output Files"])
async def clear_output_files():
    """Delete all JSONL files from the output directory."""
    files = list(JSONL_OUTPUT_DIR.glob("*.jsonl"))
    count = len(files)

    for f in files:
        f.unlink()

    return {"status": "cleared", "files_deleted": count}


# ── OpenAPI Spec Patch ──────────────────────────────────────────
# Swagger UI (as bundled in FastAPI) does not correctly render
# multi-file upload widgets when `contentMediaType` is present
# alongside `type: array`. We strip it and force `format: binary`
# on all file-upload fields so the UI shows proper file pickers.

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schemas = openapi_schema.get("components", {}).get("schemas", {})
    for schema in schemas.values():
        for prop_name, prop in schema.get("properties", {}).items():
            # Single file: type=string, contentMediaType=...
            if (
                prop.get("type") == "string"
                and prop.get("contentMediaType") == "application/octet-stream"
            ):
                prop.pop("contentMediaType", None)
                prop["format"] = "binary"

            # Multiple files: type=array, items.contentMediaType=...
            elif prop.get("type") == "array":
                items = prop.get("items", {})
                if items.get("contentMediaType") == "application/octet-stream":
                    # Replace items entirely so Swagger UI renders multi-file
                    prop["items"] = {"type": "string", "format": "binary"}

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

