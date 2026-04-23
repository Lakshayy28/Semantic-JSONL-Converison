# Semantic JSONL Conversion Pipeline

This project features an automated pipeline built to parse enterprise-grade artifacts (e.g., Markdown, Word Documents, Excel rulesets, YAML/JSON API specifications) and systematically convert them into structured JSONL format intended for LLM semantic search, fine-tuning, or RAG downstream processing. 

Included in the latest updates is a suite of scripts that automatically scaffold complex test environments mimicking real-world structures. Specifically, it simulates a **Credit Decisioning Underwriting Microservice**.

## 🏗 System Architecture

The pipeline consists of the following core components:

* **Router (`semantic_pipeline/router.py`)**: Resolves file extensions to specific specialized parsers. This is the main entry point to process a file or a batch of files.
* **Parsers (`semantic_pipeline/parsers/`)**: Contains logic tailored to exact document formats:
  * `word_parser.py`: Extracts docx structured headings, paragraphs, and embedded tables.
  * `excel_parser.py`: Iterates complex multi-sheet Excel files mapping cells to respective endpoint definitions. Note: Images are currently ignored.
  * `markdown_parser.py`: Uses `langchain_text_splitters` to securely segment Markdown text based on structural headings and paragraphs without losing context.
  * `structured_parser.py`: Maps OpenAPI JSON/YAML to endpoint definitions recursively.
* **Models (`semantic_pipeline/models.py`)**: Defines strict `Pydantic` validation schemas. Guarantees consistency of extracted chunks.
* **Emitter (`semantic_pipeline/emitter.py`)**: Serializes everything into validated `JSONL` strings, generating an easily ingestible data artifact.

## 🚀 How to Use It?

### 1. Installation

Set up your Python virtual environment and run:
```bash
pip install -r requirements.txt
```

### 2. Generating Sample Credit Microservice Data

If you need data to test with, you can run the artifact generator. This constructs multiple Excel, Word, Markdown, and YAML spec files for an imaginary Credit Underwriting Engine:
```bash
python generate_credit_artifacts.py
```

### 3. Converting Files via the Pipeline

The conversion functions act locally on individual files or directories without requiring an external HTTP server, though they can easily be wrapped inside FastAPI or Flask if API endpoints are desired.

To convert a file, you typically initialize the router, process the document, and consume the emitter in Python. Here exists a hypothetical script utilizing the `semantic_pipeline`:

```python
from semantic_pipeline.router import DocumentRouter
from pathlib import Path

# Initialize pipeline
router = DocumentRouter()
input_file = Path("sample_complex.docx")

# Parse document into Pydantic SemanticChunks
extracted_chunks = router.process_file(input_file)

# Emit to JSONL
from semantic_pipeline.emitter import JSONLEmitter
emitter = JSONLEmitter()
emitter.write_to_file(extracted_chunks, "output_chunks.jsonl")
```

## 🌐 Are there any HTTP Endpoints?

**No** — the current architecture is built as an underlying **SDK/Library**, intended to be executed from a CLI script, a Cron job, or embedded directly inside another service. 

If you require REST/HTTP endpoints, you can wrap `semantic_pipeline.router` inside a FastAPI application by creating a simple POST `/convert` endpoint that accepts a file upload, processes it using `DocumentRouter.process_file(uploaded_file)`, and returns the raw JSONL via a streaming response or text blob.

## 🧪 Testing

To verify the components, you can trigger the suite of pytests checking each parsing phase:
```bash
pytest tests/
```
