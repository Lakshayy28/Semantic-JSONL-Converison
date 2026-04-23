# Artifact Conversion Test Report

**Date:** April 23, 2026  
**API Endpoint:** `POST http://localhost:8000/convert`  
**Output Directory:** `jsonl/`

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
  -F "file=@artifacts/high_level_design.md"
```

**Response:**
```json
{
    "status": "success",
    "source_file": "high_level_design.md",
    "chunks_produced": 6,
    "jsonl_files": ["high_level_design_001.jsonl"],
    "processing_time_ms": 0.43
}
```

**Parser Used:** `MarkdownParser`  
**Chunking Strategy:** Header-aware splitting via `MarkdownHeaderTextSplitter` with breadcrumb stitching (e.g., `Section: HLD > 1. System Overview`).  
**Source Size:** 1,448 bytes  
**Output File:** `jsonl/high_level_design_001.jsonl` (3.5 KB, 6 chunks)

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
  -F "file=@artifacts/business_architecture.docx"
```

**Response:**
```json
{
    "status": "success",
    "source_file": "business_architecture.docx",
    "chunks_produced": 6,
    "jsonl_files": ["business_architecture_001.jsonl", "high_level_design_001.jsonl"],
    "processing_time_ms": 14.29
}
```

**Parser Used:** `WordParser` → `MarkdownParser`  
**Chunking Strategy:** DOCX heading styles (Heading 1-4) are converted to Markdown syntax, then processed by `MarkdownParser` with breadcrumb stitching.  
**Source Size:** 37,703 bytes  
**Output File:** `jsonl/business_architecture_001.jsonl` (3.8 KB, 6 chunks)

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
  -F "file=@artifacts/decision_controller_rules.xlsx"
```

**Response:**
```json
{
    "status": "success",
    "source_file": "decision_controller_rules.xlsx",
    "chunks_produced": 50,
    "jsonl_files": ["business_architecture_001.jsonl", "high_level_design_001.jsonl", "decision_controller_rules_001.jsonl"],
    "processing_time_ms": 37.5
}
```

**Parser Used:** `ExcelParser`  
**Chunking Strategy:** Merged cell unrolling via `openpyxl`, then `pandas` row-level stringification. Each row becomes `[Sheet: <name>] [<col>: <val>] ...`. Empty rows dropped, NaN values omitted.  
**Source Size:** 117,616 bytes  
**Output File:** `jsonl/decision_controller_rules_001.jsonl` (19.3 KB, 50 chunks)

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
  -F "file=@artifacts/credit_decisioning_openapi.yaml"
```

**Response:**
```json
{
    "status": "success",
    "source_file": "credit_decisioning_openapi.yaml",
    "chunks_produced": 32,
    "jsonl_files": ["business_architecture_001.jsonl", "credit_decisioning_openapi_001.jsonl", "high_level_design_001.jsonl", "decision_controller_rules_001.jsonl"],
    "processing_time_ms": 16.18
}
```

**Parser Used:** `StructuredParser` (API spec mode)  
**Chunking Strategy:** Detected `openapi: 3.0.3` top-level key → endpoint-level bundling. Each chunk contains: API title/version, Method + Path, Summary, Tags, Parameters, and Responses bundled together.  
**Source Size:** 6,378 bytes  
**Output File:** `jsonl/credit_decisioning_openapi_001.jsonl` (13.9 KB, 32 chunks)

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
  -F "file=@artifacts/credit_decisioning_swagger.json"
```

**Response:**
```json
{
    "status": "success",
    "source_file": "credit_decisioning_swagger.json",
    "chunks_produced": 32,
    "jsonl_files": ["credit_decisioning_swagger_001.jsonl", "business_architecture_001.jsonl", "credit_decisioning_openapi_001.jsonl", "high_level_design_001.jsonl", "decision_controller_rules_001.jsonl"],
    "processing_time_ms": 0.81
}
```

**Parser Used:** `StructuredParser` (API spec mode)  
**Chunking Strategy:** Detected `swagger: "2.0"` top-level key → same endpoint-level bundling as OpenAPI. 32 endpoints bundled identically to the YAML spec.  
**Source Size:** 9,548 bytes  
**Output File:** `jsonl/credit_decisioning_swagger_001.jsonl` (13.9 KB, 32 chunks)

**Sample Chunk:**
```json
{
    "usecase_id": "credit-decisioning",
    "document_id": "doc-d45e4bdf",
    "chunk_id": "chunk-5240d398da0b",
    "raw_context": "API: Credit Decisioning Microservice API (v2.5.0). API Endpoint: POST /applications. Summary: Submit a new application. Tags: Application. Responses: 201 (Application created).",
    "file_name": "credit_decisioning_swagger.json",
    "data_classification": "confidential",
    "sor_last_modified": "04/23/2026",
    "identifier": "underwriting-team"
}
```

---

## Summary

| # | Artifact | Type | Parser | Chunks | Output File | Size |
|---|---|---|---|---|---|---|
| 1 | `high_level_design.md` | Markdown | MarkdownParser | 6 | `high_level_design_001.jsonl` | 3.5 KB |
| 2 | `business_architecture.docx` | Word | WordParser → MarkdownParser | 6 | `business_architecture_001.jsonl` | 3.8 KB |
| 3 | `decision_controller_rules.xlsx` | Excel | ExcelParser | 50 | `decision_controller_rules_001.jsonl` | 19.3 KB |
| 4 | `credit_decisioning_openapi.yaml` | OpenAPI 3.0 | StructuredParser | 32 | `credit_decisioning_openapi_001.jsonl` | 13.9 KB |
| 5 | `credit_decisioning_swagger.json` | Swagger 2.0 | StructuredParser | 32 | `credit_decisioning_swagger_001.jsonl` | 13.9 KB |
| | **TOTAL** | | | **126** | **5 files** | **54.4 KB** |

All output JSONL files are stored in the `jsonl/` directory and are **not deleted** — they persist for downstream consumption.
