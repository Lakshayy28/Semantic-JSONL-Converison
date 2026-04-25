# Semantic JSONL Conversion Pipeline

A local, production-grade file ingestion and semantic chunking pipeline that converts heterogeneous enterprise files (`.xlsx`, `.docx`, `.md`, `.txt`, `.json`, `.yaml`) into structured JSONL format for RAG (Retrieval-Augmented Generation) downstream processing.

The downstream ingestion API calculates **768-dimensional embeddings** exclusively on a field called `raw_context`. Before chunking, the pipeline uses **Google Gemini** to generate a 3-5 sentence global document summary (Anthropic "Contextual Chunking" pattern) that is prepended to every chunk's `raw_context` — ensuring vector retrieval always carries document-level context.

## 🏗 System Architecture

```
.
├── api.py                           # FastAPI REST backend (POST /convert, etc)
├── artifact_conversion_report.md    # Documented test report of artifact conversions
├── artifacts/                       # Source enterprise files for conversion
├── index.html                       # Frontend drag-and-drop UI
├── run_e2e.py                       # End-to-end integration test script
├── requirements.txt                 # All Python dependencies
├── .env                             # API keys and model names (not committed)
├── .env.example                     # Template for .env
├── semantic_pipeline/
│   ├── models.py                    # ChunkRecord (Pydantic) + EmitterConfig
│   ├── emitter.py                   # JSONLEmitter with 9.5MB pre-check rollover
│   ├── router.py                    # SemanticRouter — extension-based dispatcher
│   ├── context_client.py            # GeminiContextClient — global context summary
│   ├── vision_client.py             # GeminiVisionClient — image triage & transcription
│   └── parsers/
│       ├── markdown_parser.py       # Header-aware chunking with breadcrumb stitching
│       ├── word_parser.py           # DOCX → Markdown conversion + table extraction
│       ├── excel_parser.py          # Merged-cell unrolling + row-level stringification
│       └── structured_parser.py     # OpenAPI/Swagger endpoint bundling + generic fallback
└── tests/
    ├── conftest.py                  # Sets GEMINI_API_KEY=MOCK before any import
    └── test_api.py                  # 38-test consolidated suite (offline, <1s)
```

### Core Components

* **SemanticRouter** (`router.py`): The main entry point. Resolves file extensions to specialized parsers, generates a global document summary via `GeminiContextClient` and prepends it to every chunk, collects `ChunkRecord` objects, and feeds them to the `JSONLEmitter`. Supports single-file ingestion (`ingest_file`), directory-level ingestion (`ingest_directory`), and **unchunked whole-file mode** (`ingest_file_unchunked`) which emits exactly one record per file.

* **GeminiContextClient** (`context_client.py`): Implements the Anthropic "Contextual Chunking" pattern. Sends the full document text to `gemini-2.5-flash` and receives a 3-5 sentence global summary that is prepended to every chunk's `raw_context`. Configured via `GEMINI_CONTEXT_MODEL` env var. Returns `[GLOBAL_CONTEXT_FAILED]` on error and never crashes the pipeline. Uses `tenacity` exponential backoff with 3 retries.

* **GeminiVisionClient** (`vision_client.py`): Triage-and-transcribe pipeline for images extracted from DOCX files. Sends images to `gemini-2.5-pro` (configurable via `GEMINI_VISION_MODEL`). Returns `DECORATIVE_DISCARD` for logos/stock photos, or a step-by-step transcription for flowcharts and diagrams. Gracefully degrades to a `[IMAGE_TRANSCRIPTION_FAILED: ...]` prefix on error. Uses `tenacity` with 3 retries and a 60-second timeout.

* **Parsers** (`parsers/`): Format-specific semantic chunking logic:

  | Parser | Extensions | Strategy |
  |---|---|---|
  | `MarkdownParser` | `.md`, `.txt` | Header-aware splitting via LangChain's `MarkdownHeaderTextSplitter`. Stitches header breadcrumbs (e.g., `Section: H1 > H2 > H3`) into `raw_context`. Falls back to `RecursiveCharacterTextSplitter` for oversized sections with 10% overlap. |
  | `WordParser` | `.docx` | Converts DOCX heading styles (Title, Heading 1–4) to Markdown syntax. **Extracts Word tables** into Markdown table syntax (`\| Header \| ... \|` with separator row). Delegates to `MarkdownParser` for unified semantic chunking. |
  | `ExcelParser` | `.xlsx`, `.xls` | Uses `openpyxl` to unmerge all merged cells (propagating the top-left value to every spanned cell), writes to an in-memory `BytesIO` buffer, then uses `pandas` for row-level iteration. Each row becomes: `[Sheet: <name>] [<col>: <val>] ...`. Empty rows are dropped; `NaN` values are omitted. |
  | `StructuredParser` | `.json`, `.yaml`, `.yml` | Detects OpenAPI/Swagger specs via top-level keys (`openapi`, `swagger`). **Resolves all `$ref` pointers** via `jsonref` before traversal, inlining schema properties into the chunk context. For API specs, produces one chunk per endpoint bundling Method + Path + Summary + Parameters + Schema Fields + Responses. For generic JSON/YAML, falls back to `RecursiveCharacterTextSplitter`. |

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

## ⚙️ Environment Variables

Create a `.env` file in the project root (copy from `.env.example`):

```bash
GEMINI_API_KEY="your-gemini-api-key"
GEMINI_CONTEXT_MODEL="gemini-2.5-flash"   # model for contextual summarisation
GEMINI_VISION_MODEL="gemini-2.5-pro"      # model for image transcription
```

Setting `GEMINI_API_KEY=MOCK` disables all real HTTP calls and returns deterministic stubs — this is what the test suite uses automatically via `tests/conftest.py`.

## 🚀 How to Use It

### 1. Installation

Set up a Python 3.10+ virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your GEMINI_API_KEY
```

### 2. Generating Sample Data

To generate test artifacts simulating a Credit Decisioning Underwriting Microservice (Excel rulesets, Word architecture docs, Markdown guides, OpenAPI specs):

*Note: The standalone artifact generator script has been removed from the repository. Place source files manually in the `artifacts/` folder.*

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

Once running, you can access the interactive **Premium Web Portal UI** at **http://localhost:8000/** to test single and batch conversions via a drag-and-drop interface.

The interactive API Swagger docs are also available at **http://localhost:8000/docs**.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — lists supported extensions |
| `POST` | `/convert` | Upload a single file → returns JSONL bytes directly |
| `POST` | `/convert/batch` | Upload multiple files → returns a ZIP of JSONL files |

All conversion endpoints accept a `chunked` boolean query parameter (default `true`). Set `chunked=false` to emit exactly **one** JSONL record containing the entire parsed content.

**Response headers** for `/convert`:
- `Content-Type: application/x-ndjson`
- `Content-Disposition: attachment; filename="<stem>.jsonl"` (or `<stem>_unchunked.jsonl`)
- `X-Chunks-Produced: <n>`

No output files are stored on the server — all JSONL content is streamed directly back to the client.

### Examples

**Convert a single file:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-rules" \
  -F "file=@artifacts/decision_controller_rules.xlsx" -o output.jsonl
```

**Batch convert multiple files (returns a ZIP):**
```bash
curl -X POST "http://localhost:8000/convert/batch?usecase_id=credit-full&chunked=true" \
  -F "files=@artifacts/credit_decisioning_openapi.yaml" \
  -F "files=@artifacts/decision_controller_rules.xlsx" \
  -o batch_output.zip
```

**Unchunked (whole-file) conversion:**
```bash
curl -X POST "http://localhost:8000/convert?usecase_id=credit-decisioning&chunked=false" \
  -F "file=@artifacts/credit_decisioning_openapi.yaml" -o output_unchunked.jsonl
```

For a full end-to-end test report covering all 5 workspace artifacts (126 chunks across all file types), see [`artifact_conversion_report.md`](artifact_conversion_report.md).

## 🧪 Testing

The pipeline has a **38-test consolidated suite** that runs entirely offline (no real API calls, no network) in under one second.

```bash
# Run the full suite
pytest tests/ -v

# Quick smoke check
pytest tests/test_api.py -q
```

`tests/conftest.py` sets `GEMINI_API_KEY=MOCK` at module level before any import occurs. This causes both `GeminiContextClient` and `GeminiVisionClient` to return deterministic stubs without touching the network — `load_dotenv()` in `api.py` cannot override an env var already set in `os.environ`.

### What's Tested

| Class | Tests | Coverage |
|---|---|---|
| `TestHealthEndpoint` | 3 | Status 200, `status` field, supported extensions list |
| `TestConvertEndpoint` | 5 | Markdown → JSONL, xlsx → JSONL, 400 on unsupported, `X-Chunks-Produced` header, `Content-Disposition` filename |
| `TestChunkedVsUnchunked` | 5 | Unchunked = 1 record, chunked ≥ 1, filename suffix `_unchunked`, xlsx unchunked = 1 record |
| `TestBatchEndpoint` | 4 | Returns ZIP, one JSONL per file, skips unsupported, mixed formats |
| `TestVisionClient` | 7 | Payload structure, base64 image, system prompt, mock stub, `DECORATIVE_DISCARD` constant, graceful degradation, no exception raised |
| `TestContextClient` | 9 | Mock returns `MOCK_CONTEXT`, empty doc → fallback, whitespace → fallback, system prompt, user message, degradation on failure, no exception, timeout ≥ 30, retries ≥ 1 |
| `TestLLMConnectivity` | 5 | Mock key never calls httpx, real (fake) key attempts HTTP, `top_p=1` in both payloads |

## 📦 Dependencies

### Pipeline (Core)

| Package | Purpose |
|---|---|
| `pydantic>=2.0.0` | Schema validation for `ChunkRecord` and `EmitterConfig` |
| `langchain-text-splitters>=0.2.0` | `MarkdownHeaderTextSplitter` and `RecursiveCharacterTextSplitter` |
| `pandas` | Tabular data iteration for Excel parsing |
| `openpyxl` | Excel file I/O and merged cell resolution |
| `python-docx` | DOCX paragraph, heading, and table extraction |
| `PyYAML` | YAML file parsing |
| `jsonref>=1.0.0` | OpenAPI `$ref` pointer resolution |
| `httpx>=0.27.0` | HTTP client for Gemini API calls |
| `tenacity>=8.0.0` | Exponential backoff retry for Gemini requests |
| `python-dotenv>=1.0.0` | Loads `.env` into `os.environ` at startup |
| `Pillow>=10.0.0` | Image handling for vision pipeline |

### API Server

| Package | Purpose |
|---|---|
| `fastapi>=0.115.0` | REST API framework for file conversion endpoints |
| `uvicorn[standard]>=0.30.0` | ASGI server to run the FastAPI app |
| `python-multipart>=0.0.9` | Multipart form data (file uploads) support |

### Testing

| Package | Purpose |
|---|---|
| `pytest>=8.0.0` | Test framework |
