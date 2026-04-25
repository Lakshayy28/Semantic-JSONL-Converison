"""
SemanticRouter — The File Dispatcher
=====================================
Routes incoming files to the correct format-specific parser based on
file extension.  Each parser produces a list of raw chunk dicts,
which are converted into ChunkRecords, enriched with global document
context (via GeminiContextClient), and then fed to the JSONLEmitter.

Supported parsers:
  • MarkdownParser   → .md, .txt
  • WordParser        → .docx
  • ExcelParser       → .xlsx, .xls
  • StructuredParser  → .json, .yaml, .yml
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .emitter import JSONLEmitter
from .models import ChunkRecord, EmitterConfig
from .context_client import GeminiContextClient, FALLBACK_STRING
from .parsers.excel_parser import ExcelParser
from .parsers.markdown_parser import MarkdownParser
from .parsers.structured_parser import StructuredParser
from .parsers.word_parser import WordParser

logger = logging.getLogger(__name__)

# ── File extension → parser mapping ────────────────────────────────

SUPPORTED_EXTENSIONS = {
    ".xlsx": "ExcelParser",
    ".xls": "ExcelParser",
    ".docx": "WordParser",
    ".md": "MarkdownParser",
    ".json": "StructuredParser",
    ".yaml": "StructuredParser",
    ".yml": "StructuredParser",
    ".txt": "MarkdownParser",  # plain text treated as headerless markdown
}


class SemanticRouter:
    """
    Orchestrates the full ingestion pipeline:
      1. Scan an input directory (or accept individual files).
      2. Route each file to its specialised parser.
      3. Generate a global document summary via GeminiContextClient.
      4. Prepend the summary to every chunk's raw_context.
      5. Feed ChunkRecords to the JSONLEmitter.
    """

    def __init__(self, config: EmitterConfig, context_client: Optional[GeminiContextClient] = None) -> None:
        self._config = config
        self._emitter = JSONLEmitter(config)
        self._skipped_files: List[str] = []
        self._processed_files: List[str] = []
        self._context_client = context_client or GeminiContextClient()

        # ── Parser instances ────────────────────────────────────
        self._md_parser = MarkdownParser()
        self._word_parser = WordParser()
        self._excel_parser = ExcelParser()
        self._structured_parser = StructuredParser()

    # ── Public API ──────────────────────────────────────────────────

    def ingest_directory(self, directory: str) -> dict:
        """
        Recursively scan a directory and ingest all supported files.

        Returns:
            Summary dict with processing stats.
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Input directory not found: {directory}")

        files = sorted(dir_path.rglob("*"))
        for file_path in files:
            if file_path.is_file():
                self.ingest_file(str(file_path))

        return self.summary()

    def ingest_file(self, file_path: str) -> List[ChunkRecord]:
        """
        Route a single file to the appropriate parser and emit its chunks.

        Returns:
            List of ChunkRecords produced (empty if the file was skipped).
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            logger.warning("Unsupported file extension '%s': %s", ext, file_path)
            self._skipped_files.append(file_path)
            return []

        parser_name = SUPPORTED_EXTENSIONS[ext]
        logger.info("Routing %s → %s", file_path, parser_name)

        # ── Step 1: Extract full text for global context ───────────
        global_context = self._generate_global_context(path)

        # ── Step 2: Dispatch to parser for chunking ────────────────
        chunks = self._dispatch(parser_name, path, global_context=global_context)

        if chunks:
            self._emitter.emit_many(chunks)
            self._processed_files.append(file_path)
            logger.info(
                "Emitted %d chunks from %s", len(chunks), path.name
            )
        else:
            logger.info("No chunks produced for %s (empty or unsupported content).", file_path)
            self._skipped_files.append(file_path)

        return chunks

    def ingest_file_unchunked(self, file_path: str) -> List[ChunkRecord]:
        """
        Bypass chunking: extract clean text via the parser, then emit
        exactly **one** ChunkRecord containing the entire document.

        The parsers still run (e.g., DOCX → Markdown, Excel unmerge,
        $ref resolution) but instead of splitting into multiple chunks,
        all parsed output is aggregated into a single `raw_context`.

        The chunk_id is set to ``{document_id}-full`` for traceability.

        Returns:
            List with exactly 1 ChunkRecord (or empty if nothing parsed).
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            logger.warning("Unsupported file extension '%s': %s", ext, file_path)
            self._skipped_files.append(file_path)
            return []

        parser_name = SUPPORTED_EXTENSIONS[ext]
        logger.info("Unchunked routing %s → %s", file_path, parser_name)

        # ── Run the parser to get clean extracted text fragments ────
        parser_map = {
            "MarkdownParser": lambda: self._md_parser.parse_file(str(path)),
            "WordParser": lambda: self._word_parser.parse_file(str(path)),
            "ExcelParser": lambda: self._excel_parser.parse_file(str(path)),
            "StructuredParser": lambda: self._structured_parser.parse_file(str(path)),
        }

        parser_fn = parser_map.get(parser_name)
        if parser_fn is None:
            logger.error("Unknown parser '%s' for file: %s", parser_name, file_path)
            return []

        raw_chunks = parser_fn()

        if not raw_chunks:
            logger.info("No content parsed for %s — skipping.", file_path)
            self._skipped_files.append(file_path)
            return []

        # ── Aggregate all chunk texts into one raw_context ─────────
        combined = "\n\n".join(
            chunk.get("raw_context", "").strip()
            for chunk in raw_chunks
            if chunk.get("raw_context", "").strip()
        )

        if not combined:
            self._skipped_files.append(file_path)
            return []

        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        record = ChunkRecord(
            usecase_id=self._config.usecase_id,
            document_id=doc_id,
            chunk_id=f"{doc_id}-full",
            raw_context=combined,
            file_name=path.name,
            data_classification=self._config.data_classification,
            identifier=self._config.identifier,
        )

        self._emitter.emit(record)
        self._processed_files.append(file_path)
        logger.info(
            "Emitted 1 unchunked record (%d chars) from %s",
            len(combined),
            path.name,
        )

        return [record]

    def close(self) -> None:
        """Finalize the emitter and flush all output."""
        self._emitter.close()

    def summary(self) -> dict:
        """Return a summary of the full pipeline run."""
        return {
            "processed_files": list(self._processed_files),
            "skipped_files": list(self._skipped_files),
            "emitter_stats": self._emitter.stats,
        }

    # ── Internal Dispatch ───────────────────────────────────────────

    def _dispatch(self, parser_name: str, file_path: Path, global_context: Optional[str] = None) -> List[ChunkRecord]:
        """
        Dispatch to the correct parser and convert results to ChunkRecords.
        All file types are fully supported.
        """
        parser_map = {
            "MarkdownParser": lambda: self._md_parser.parse_file(str(file_path)),
            "WordParser": lambda: self._word_parser.parse_file(str(file_path)),
            "ExcelParser": lambda: self._excel_parser.parse_file(str(file_path)),
            "StructuredParser": lambda: self._structured_parser.parse_file(str(file_path)),
        }

        parser_fn = parser_map.get(parser_name)
        if parser_fn is None:
            logger.error(
                "Unknown parser '%s' for file: %s",
                parser_name,
                file_path,
            )
            return []

        raw_chunks = parser_fn()

        # ── Convert parser dicts → ChunkRecords ────────────────────
        return self._to_chunk_records(raw_chunks, file_path, global_context=global_context)

    def _to_chunk_records(
        self,
        raw_chunks: List[Dict[str, str]],
        file_path: Path,
        global_context: Optional[str] = None,
    ) -> List[ChunkRecord]:
        """
        Convert parser output dicts into fully stamped ChunkRecords
        using the pipeline config defaults.

        If ``global_context`` is provided it is prepended to every
        chunk's ``raw_context`` using the tag:
        ``[Global Document Context: <summary>]``
        """
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        records: List[ChunkRecord] = []

        for chunk_dict in raw_chunks:
            raw_context = chunk_dict.get("raw_context", "").strip()
            if not raw_context:
                continue

            # ── Prepend global context if available ─────────────────
            if global_context:
                raw_context = (
                    f"[Global Document Context: {global_context}]\n\n"
                    + raw_context
                )

            record = ChunkRecord(
                usecase_id=self._config.usecase_id,
                document_id=doc_id,
                raw_context=raw_context,
                file_name=file_path.name,
                data_classification=self._config.data_classification,
                identifier=self._config.identifier,
            )
            records.append(record)

        return records

    # ── Global Context Helper ───────────────────────────────────────

    def _generate_global_context(self, file_path: Path) -> Optional[str]:
        """
        Extract full document text (unchunked), pass to the context
        client, and return the summary string.

        Returns None if context generation fails or is empty.
        """
        ext = file_path.suffix.lower()
        parser_name = SUPPORTED_EXTENSIONS.get(ext)
        if not parser_name:
            return None

        parser_map = {
            "MarkdownParser": lambda: self._md_parser.parse_file(str(file_path)),
            "WordParser": lambda: self._word_parser.parse_file(str(file_path)),
            "ExcelParser": lambda: self._excel_parser.parse_file(str(file_path)),
            "StructuredParser": lambda: self._structured_parser.parse_file(str(file_path)),
        }

        parser_fn = parser_map.get(parser_name)
        if not parser_fn:
            return None

        try:
            raw_chunks = parser_fn()
            if not raw_chunks:
                return None

            full_text = "\n\n".join(
                c.get("raw_context", "").strip()
                for c in raw_chunks
                if c.get("raw_context", "").strip()
            )

            if not full_text:
                return None

            summary = self._context_client.generate_global_context(full_text)
            if summary == FALLBACK_STRING:
                logger.warning("Global context generation failed for %s.", file_path.name)
                return None

            logger.info(
                "Generated global context (%d chars) for %s.",
                len(summary),
                file_path.name,
            )
            return summary

        except Exception as e:
            logger.warning("Error generating global context for %s: %s", file_path.name, e)
            return None

    # ── Context Manager ─────────────────────────────────────────────

    def __enter__(self) -> "SemanticRouter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
