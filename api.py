"""
FastAPI Backend — Semantic JSONL Conversion API
=================================================
Exposes REST endpoints to upload enterprise files and convert them
into semantically chunked JSONL.  Every POST endpoint returns the
converted JSONL content directly — nothing is stored on disk.

Run:
    uvicorn api:app --reload --port 8000

Endpoints:
    GET  /health                    — Health check
    POST /convert                   — Single file → JSONL download
    POST /convert/batch             — Multiple files → ZIP of JSONLs
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

from semantic_pipeline.models import EmitterConfig, ChunkRecord
from semantic_pipeline.router import SemanticRouter

# ── Configuration ───────────────────────────────────────────────
UPLOAD_TEMP_DIR = Path(__file__).parent / ".uploads"
UPLOAD_TEMP_DIR.mkdir(exist_ok=True)


# ── Response Models ─────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    pipeline_version: str = "1.0.0"
    supported_extensions: List[str]


# ── FastAPI App ─────────────────────────────────────────────────

app = FastAPI(
    title="Semantic JSONL Conversion API",
    description=(
        "Upload enterprise files (.xlsx, .docx, .md, .txt, .json, .yaml) "
        "and convert them into semantically chunked JSONL format for RAG pipelines. "
        "Every conversion endpoint returns the JSONL file directly — "
        "no server-side storage.\n\n"
        "**Batch endpoints** return a ZIP archive containing one JSONL per input file."
    ),
    version="2.0.0",
)

SUPPORTED = [".xlsx", ".xls", ".docx", ".md", ".json", ".yaml", ".yml", ".txt"]


# ── Helpers ─────────────────────────────────────────────────────

def _save_upload(upload: UploadFile) -> Path:
    """Save an uploaded file to the temp directory and return the path."""
    safe_name = Path(upload.filename).name
    dest = UPLOAD_TEMP_DIR / safe_name
    with open(dest, "wb") as f:
        f.write(upload.file.read())
    return dest


def _chunks_to_jsonl_bytes(chunks: List[ChunkRecord]) -> bytes:
    """Serialize a list of ChunkRecords into a JSONL byte string."""
    lines = [chunk.to_jsonl_line() for chunk in chunks]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _run_chunked(file_path: str, usecase_id: str, identifier: str,
                 data_classification: str) -> List[ChunkRecord]:
    """Run the chunked pipeline in-memory (no disk output)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmitterConfig(
            output_dir=tmpdir,
            base_filename="mem",
            usecase_id=usecase_id,
            identifier=identifier,
            data_classification=data_classification,
        )
        with SemanticRouter(config) as router:
            return router.ingest_file(file_path)


def _run_unchunked(file_path: str, usecase_id: str, identifier: str,
                   data_classification: str) -> List[ChunkRecord]:
    """Run the unchunked pipeline in-memory (no disk output)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = EmitterConfig(
            output_dir=tmpdir,
            base_filename="mem",
            usecase_id=usecase_id,
            identifier=identifier,
            data_classification=data_classification,
        )
        with SemanticRouter(config) as router:
            return router.ingest_file_unchunked(file_path)


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
    return HealthResponse(supported_extensions=SUPPORTED)


@app.post("/convert", tags=["Conversion"])
async def convert_file(
    file: UploadFile = File(..., description="File to convert to JSONL"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
    chunked: bool = Query(True, description="Whether to chunk the output or return a single record"),
):
    """
    Upload a single file → returns the converted JSONL file directly.

    Supported formats: .xlsx, .xls, .docx, .md, .txt, .json, .yaml, .yml
    """
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: {SUPPORTED}",
        )

    temp_path = _save_upload(file)
    try:
        if chunked:
            chunks = _run_chunked(str(temp_path), usecase_id, identifier,
                                  data_classification)
            out_name = f"{Path(file.filename).stem}.jsonl"
        else:
            chunks = _run_unchunked(str(temp_path), usecase_id, identifier,
                                    data_classification)
            out_name = f"{Path(file.filename).stem}_unchunked.jsonl"
            
        jsonl_bytes = _chunks_to_jsonl_bytes(chunks)

        return Response(
            content=jsonl_bytes,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{out_name}"',
                "X-Chunks-Produced": str(len(chunks)),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/convert/batch", tags=["Conversion"])
async def convert_batch(
    files: List[UploadFile] = File(..., description="Files to convert"),
    usecase_id: str = Query("default", description="Business use-case identifier"),
    identifier: str = Query("api-upload", description="Team or system identifier"),
    data_classification: str = Query("internal", description="Data sensitivity label"),
    chunked: bool = Query(True, description="Whether to chunk the output or return a single record per file"),
):
    """
    Upload multiple files → returns a ZIP archive of individual JSONL files.

    Each input file produces its own `.jsonl` inside the ZIP, in upload order.
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in SUPPORTED:
                continue

            temp_path = _save_upload(upload)
            try:
                if chunked:
                    chunks = _run_chunked(str(temp_path), usecase_id, identifier,
                                          data_classification)
                    out_name = f"{Path(upload.filename).stem}.jsonl"
                else:
                    chunks = _run_unchunked(str(temp_path), usecase_id, identifier,
                                            data_classification)
                    out_name = f"{Path(upload.filename).stem}_unchunked.jsonl"
                    
                jsonl_bytes = _chunks_to_jsonl_bytes(chunks)
                zf.writestr(out_name, jsonl_bytes)
            except Exception:
                # Skip failed files, don't crash the batch
                pass
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    zip_buffer.seek(0)
    
    zip_name = "batch_converted.zip" if chunked else "batch_unchunked.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"',
        },
    )


# ── OpenAPI Spec Patch ──────────────────────────────────────────

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
            if (
                prop.get("type") == "string"
                and prop.get("contentMediaType") == "application/octet-stream"
            ):
                prop.pop("contentMediaType", None)
                prop["format"] = "binary"
            elif prop.get("type") == "array":
                items = prop.get("items", {})
                if items.get("contentMediaType") == "application/octet-stream":
                    prop["items"] = {"type": "string", "format": "binary"}

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
