"""
Semantic JSONL Conversion Pipeline
===================================
Enterprise file ingestion pipeline that converts heterogeneous files
(.xlsx, .docx, .md, .json, .yaml) into semantically chunked JSONL
ready for RAG embedding ingestion (768-dim on `raw_context`).

Strict 10MB per-file JSONL output with automatic rollover.
"""

__version__ = "0.1.0"
