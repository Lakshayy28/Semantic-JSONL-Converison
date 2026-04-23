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
from fastapi.responses import FileResponse, JSONResponse
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
        "Output files are written to the `jsonl/` directory."
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

    config = EmitterConfig(
        output_dir=str(JSONL_OUTPUT_DIR),
        base_filename="batch",
        usecase_id=usecase_id,
        identifier=identifier,
        data_classification=data_classification,
    )

    with SemanticRouter(config) as router:
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
