# Artifact Conversion Test Report

**Date:** April 23, 2026  
**API Endpoint:** `POST http://localhost:8000/convert`  
**Output:** JSONL bytes returned directly in the response body (no server-side storage)

---

## API Response Format

The `/convert` endpoint returns JSONL content directly as `application/x-ndjson` bytes — not a JSON envelope. Conversion metadata is carried in response headers:

| Header | Example |
|---|---|
| `Content-Type` | `application/x-ndjson` |
| `Content-Disposition` | `attachment; filename="high_level_design.jsonl"` |
| `X-Chunks-Produced` | `6` |

For unchunked mode (`chunked=false`) the filename becomes `<stem>_unchunked.jsonl`.

---

## Common Request Parameters

All conversions were executed with the following shared query parameters:

| Parameter | Value |
|---|---|
| `usecase_id` | `credit-decisioning` |
| `identifier` | `underwriting-team` |
| `data_classification` | `confidential` |

---

## Conversion Results

### 1. `high_level_design.md` (Markdown)

**Request:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&identifier=underwriting-team&data_classification=confidential" \
  -F "file=@artifacts/high_level_design.md" -o high_level_design.jsonl
```

**Response Headers:**
```
Content-Type: application/x-ndjson
Content-Disposition: attachment; filename="high_level_design.jsonl"
X-Chunks-Produced: 6
```

**Parser Used:** `MarkdownParser`  
**Chunking Strategy:** Header-aware splitting via `MarkdownHeaderTextSplitter` with breadcrumb stitching (e.g., `Section: HLD > 1. System Overview`). Global document summary prepended to each chunk's `raw_context` via `GeminiContextClient`.  
**Source Size:** 1,448 bytes  
**Chunks:** 6

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-2d27901d",
    "chunk_id": "chunk-341efc0350a9",
    "raw_context": "Section: High-Level Design (HLD): Credit Decisioning Microservice > 1. System Overview. Content: # High-Level Design (HLD)...",
    "file_name": "high_level_design.md",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

### 2. `business_architecture.docx` (Word Document)

**Request:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&identifier=underwriting-team&data_classification=confidential" \
  -F "file=@artifacts/business_architecture.docx" -o business_architecture.jsonl
```

**Response Headers:**
```
Content-Type: application/x-ndjson
Content-Disposition: attachment; filename="business_architecture.jsonl"
X-Chunks-Produced: 6
```

**Parser Used:** `WordParser` → `MarkdownParser`  
**Chunking Strategy:** DOCX heading styles (Heading 1-4) are converted to Markdown syntax, Word tables extracted to Markdown table format, then processed by `MarkdownParser` with breadcrumb stitching. Global document summary prepended to each chunk via `GeminiContextClient`.  
**Source Size:** 37,703 bytes  
**Chunks:** 6

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-7997a60a",
    "chunk_id": "chunk-aa657fb217cf",
    "raw_context": "Section: Business Architecture: Credit Decisioning Microservice. Content: # Business Architecture: Credit Decisioning Microservice",
    "file_name": "business_architecture.docx",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

### 3. `decision_controller_rules.xlsx` (Excel Ruleset)

**Request:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&identifier=underwriting-team&data_classification=confidential" \
  -F "file=@artifacts/decision_controller_rules.xlsx" -o decision_controller_rules.jsonl
```

**Response Headers:**
```
Content-Type: application/x-ndjson
Content-Disposition: attachment; filename="decision_controller_rules.jsonl"
X-Chunks-Produced: 50
```

**Parser Used:** `ExcelParser`  
**Chunking Strategy:** Merged cell unrolling via `openpyxl`, then `pandas` row-level stringification. Each row becomes `[Sheet: <name>] [<col>: <val>] ...`. Empty rows dropped, NaN values omitted. Global document summary prepended to each chunk via `GeminiContextClient`.  
**Source Size:** 117,616 bytes  
**Chunks:** 50

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-0ddc0dcd",
    "chunk_id": "chunk-f6b02b989117",
    "raw_context": "[Sheet: Evaluate App] [Endpoint Name:: Method & Path:] [Evaluate App: [POST] /decisions/evaluate]",
    "file_name": "decision_controller_rules.xlsx",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

### 4. `credit_decisioning_openapi.yaml` (OpenAPI 3.0 Spec)

**Request:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&identifier=underwriting-team&data_classification=confidential" \
  -F "file=@artifacts/credit_decisioning_openapi.yaml" -o credit_decisioning_openapi.jsonl
```

**Response Headers:**
```
Content-Type: application/x-ndjson
Content-Disposition: attachment; filename="credit_decisioning_openapi.jsonl"
X-Chunks-Produced: 32
```

**Parser Used:** `StructuredParser` (API spec mode)  
**Chunking Strategy:** Detected `openapi: 3.0.3` top-level key → endpoint-level bundling. Each chunk contains: API title/version, Method + Path, Summary, Tags, Parameters, and Responses. All `$ref` pointers resolved before traversal. Global document summary prepended via `GeminiContextClient`.  
**Source Size:** 6,378 bytes  
**Chunks:** 32

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-486d8b1f",
    "chunk_id": "chunk-92de8c6746b6",
    "raw_context": "API: Credit Decisioning Microservice API (v2.5.0). API Endpoint: POST /applications. Summary: Submit a new application. Tags: Application. Responses: 201 (Application created).",
    "file_name": "credit_decisioning_openapi.yaml",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

### 5. `credit_decisioning_swagger.json` (Swagger 2.0 Spec)

**Request:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&identifier=underwriting-team&data_classification=confidential" \
  -F "file=@artifacts/credit_decisioning_swagger.json" -o credit_decisioning_swagger.jsonl
```

**Response Headers:**
```
Content-Type: application/x-ndjson
Content-Disposition: attachment; filename="credit_decisioning_swagger.jsonl"
X-Chunks-Produced: 32
```

**Parser Used:** `StructuredParser` (API spec mode)  
**Chunking Strategy:** Detected `swagger: "2.0"` top-level key → same endpoint-level bundling as OpenAPI. 32 endpoints bundled identically to the YAML spec. Global document summary prepended via `GeminiContextClient`.  
**Source Size:** 9,548 bytes  
**Chunks:** 32

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-d45e4bdf",
    "chunk_id": "chunk-5240d398da0b",
    "raw_context": "[Global context: Credit Decisioning Microservice API v2.5.0 provides endpoints for submitting and managing loan applications, performing credit checks, and retrieving decision results.] API: Credit Decisioning Microservice API (v2.5.0). API Endpoint: POST /applications. Summary: Submit a new application. Tags: Application. Responses: 201 (Application created).",
    "file_name": "credit_decisioning_swagger.json",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

## Summary

| # | Artifact | Type | Parser | Chunks |
|---|---|---|---|---|
| 1 | `high_level_design.md` | Markdown | MarkdownParser | 6 |
| 2 | `business_architecture.docx` | Word | WordParser → MarkdownParser | 6 |
| 3 | `decision_controller_rules.xlsx` | Excel | ExcelParser | 50 |
| 4 | `credit_decisioning_openapi.yaml` | OpenAPI 3.0 | StructuredParser | 32 |
| 5 | `credit_decisioning_swagger.json` | Swagger 2.0 | StructuredParser | 32 |
| | **TOTAL** | | | **126** |

All output is returned as JSONL bytes in the HTTP response body. No files are stored on the server.

All output JSONL files are stored in the `jsonl/` directory and are **not deleted** — they persist for downstream consumption.
