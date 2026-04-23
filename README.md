# Semantic JSONL Conversion Pipeline

A local, production-grade file ingestion and semantic chunking pipeline that converts heterogeneous enterprise files (`.xlsx`, `.docx`, `.md`, `.txt`, `.json`, `.yaml`) into structured JSONL format for RAG (Retrieval-Augmented Generation) downstream processing.

The downstream ingestion API calculates **768-dimensional embeddings** exclusively on a field called `raw_context`. The pipeline's semantic chunking strategies are designed to preserve hierarchical and tabular context boundaries — never using blind character splitting that would destroy document structure.

## 🏗 System Architecture

```
semantic_pipeline/
├── models.py                    # ChunkRecord (Pydantic) + EmitterConfig
├── emitter.py                   # JSONLEmitter with 9.5MB pre-check rollover
├── router.py                    # SemanticRouter — extension-based dispatcher
└── parsers/
    ├── markdown_parser.py       # Header-aware chunking with breadcrumb stitching
    ├── word_parser.py           # DOCX → Markdown conversion, then header chunking
    ├── excel_parser.py          # Merged-cell unrolling + row-level stringification
    └── structured_parser.py     # OpenAPI/Swagger endpoint bundling + generic fallback
```

### Core Components

* **SemanticRouter** (`router.py`): The main entry point. Resolves file extensions to specialized parsers, collects `ChunkRecord` objects, and feeds them to the `JSONLEmitter`. Supports both single-file and directory-level ingestion.

* **Parsers** (`parsers/`): Format-specific semantic chunking logic:

  | Parser | Extensions | Strategy |
  |---|---|---|
  | `MarkdownParser` | `.md`, `.txt` | Header-aware splitting via LangChain's `MarkdownHeaderTextSplitter`. Stitches header breadcrumbs (e.g., `Section: H1 > H2 > H3`) into `raw_context`. Falls back to `RecursiveCharacterTextSplitter` for oversized sections with 10% overlap. |
  | `WordParser` | `.docx` | Converts DOCX heading styles (Title, Heading 1–4) to Markdown syntax, then delegates to `MarkdownParser` for unified semantic chunking. |
  | `ExcelParser` | `.xlsx`, `.xls` | Uses `openpyxl` to unmerge all merged cells (propagating the top-left value to every spanned cell), writes to an in-memory `BytesIO` buffer, then uses `pandas` for row-level iteration. Each row becomes: `[Sheet: <name>] [<col>: <val>] ...`. Empty rows are dropped; `NaN` values are omitted. |
  | `StructuredParser` | `.json`, `.yaml`, `.yml` | Detects OpenAPI/Swagger specs via top-level keys (`openapi`, `swagger`). For API specs, produces one chunk per endpoint bundling Method + Path + Summary + Parameters + Request Body + Responses. For generic JSON/YAML, falls back to `RecursiveCharacterTextSplitter`. |

* **ChunkRecord** (`models.py`): Pydantic model enforcing the strict JSONL schema — validates non-empty fields, MM/DD/YYYY date format, and auto-generates unique `chunk_id` values.

* **JSONLEmitter** (`emitter.py`): Serializes `ChunkRecord` objects to `.jsonl` files with **pre-check rollover logic** — measures chunk byte size *before* writing to ensure no file ever exceeds the 9.5 MB threshold (500 KB safety margin against the 10 MB API limit). Raises `ValueError` if a single chunk exceeds the limit.

### JSONL Output Schema

Each line in the output `.jsonl` files conforms to:

```json
{
  "usecase_id": "string",
  "document_id": "string",
  "chunk_id": "chunk-<12-hex-chars>",
  "raw_context": "string (embedding target)",
  "file_name": "string",
  "data_classification": "string",
  "sor_last_modified": "MM/DD/YYYY",
  "identifier": "string"
}
```

## 🚀 How to Use It

### 1. Installation

Set up a Python 3.10+ virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Generating Sample Data

To generate test artifacts simulating a Credit Decisioning Underwriting Microservice (Excel rulesets, Word architecture docs, Markdown guides, OpenAPI specs):

```bash
python generate_credit_artifacts.py
```

### 3. Running the Pipeline

```python
from semantic_pipeline.models import EmitterConfig
from semantic_pipeline.router import SemanticRouter

config = EmitterConfig(
    output_dir="./output",
    usecase_id="credit-decisioning",
    identifier="underwriting-team",
)

# Process a single file
with SemanticRouter(config) as router:
    chunks = router.ingest_file("artifacts/credit_decisioning_openapi.yaml")
    print(f"Produced {len(chunks)} chunks")

# Or process an entire directory
with SemanticRouter(config) as router:
    summary = router.ingest_directory("./artifacts")
    print(summary)
```

The `EmitterConfig` supports customization:

```python
config = EmitterConfig(
    output_dir="./output",          # Where .jsonl files are written
    usecase_id="my-usecase",        # Required: business use-case ID
    base_filename="chunks",         # Output file prefix (default: "chunks")
    max_file_bytes=9_500_000,       # Rollover threshold (default: 9.5 MB)
    data_classification="internal", # Data sensitivity label
    identifier="team-name",        # Team or system identifier
)
```

## 🌐 REST API (FastAPI)

The pipeline ships with a fully functional FastAPI backend for file conversion via HTTP.

### Starting the Server

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

The interactive Swagger docs are available at **http://localhost:8000/docs**.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — lists supported extensions |
| `POST` | `/convert` | Upload a single file → JSONL conversion |
| `POST` | `/convert/batch` | Upload multiple files → batch JSONL conversion |
| `GET` | `/files` | List all generated JSONL output files |
| `GET` | `/files/{filename}` | Download a specific JSONL file |
| `DELETE` | `/files` | Clear all generated JSONL files |

All output `.jsonl` files are written to the `jsonl/` directory.

### Examples

**Convert a single file:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-rules" \
  -F "file=@artifacts/decision_controller_rules.xlsx"
```

**Batch convert multiple files:**
```bash
curl -X POST "http://localhost:8000/convert/batch?usecase_id=credit-full" \
  -F "files=@artifacts/credit_decisioning_openapi.yaml" \
  -F "files=@artifacts/decision_controller_rules.xlsx" \
  -F "files=@artifacts/credit_decisioning_swagger.json"
```

**List output files:**
```bash
curl http://localhost:8000/files
```

**Download a JSONL file:**
```bash
curl -O http://localhost:8000/files/chunks_001.jsonl
```

## 🧪 Testing

The pipeline has a comprehensive **106-test suite** organized by phase:

```bash
# Run the full suite
pytest tests/ -v

# Run a specific phase
pytest tests/test_phase1.py -v   # 21 tests — Models, Emitter, Router foundation
pytest tests/test_phase2.py -v   # 26 tests — Markdown, DOCX, header stitching
pytest tests/test_phase3.py -v   # 19 tests — Excel, merged cells, NaN handling
pytest tests/test_phase4.py -v   # 37 tests — OpenAPI/Swagger, generic fallback
```

### What's Tested

| Area | Coverage |
|---|---|
| Schema validation | Non-empty fields, date format, chunk ID uniqueness |
| JSONL rollover | Pre-check byte measurement, 9.5 MB cap enforcement, sequential file naming |
| Header stitching | Breadcrumb paths, recursive fallback overlap verification |
| DOCX conversion | Heading style → Markdown mapping, round-trip through MarkdownParser |
| Merged cells | Single-column merges, multi-dimensional block merges, value propagation |
| Data cleaning | Empty row removal, NaN omission, column-header context injection |
| API spec detection | OpenAPI 3.x, Swagger 2.0, generic fallback, invalid input handling |
| Endpoint bundling | Method, Path, Summary, Parameters, Request Body, Responses extraction |
| Real artifacts | Tests against actual workspace files (skipped if unavailable) |

## 📦 Dependencies

### Pipeline (Core)

| Package | Purpose |
|---|---|
| `pydantic>=2.0.0` | Schema validation for `ChunkRecord` and `EmitterConfig` |
| `langchain-text-splitters>=0.2.0` | `MarkdownHeaderTextSplitter` and `RecursiveCharacterTextSplitter` |
| `pandas` | Tabular data iteration for Excel parsing |
| `openpyxl` | Excel file I/O and merged cell resolution |
| `python-docx` | DOCX paragraph and heading extraction |
| `PyYAML` | YAML file parsing |

### Testing

| Package | Purpose |
|---|---|
| `pytest>=8.0.0` | Test framework |

### Artifact Generation (`generate_credit_artifacts.py`)

| Package | Purpose |
|---|---|
| `matplotlib` | Chart generation for sample Excel artifacts |
| `Pillow` | Image handling dependency for matplotlib |
| `XlsxWriter` | Excel file creation for sample artifacts |
