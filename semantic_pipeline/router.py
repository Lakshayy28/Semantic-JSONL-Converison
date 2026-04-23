"""
SemanticRouter — The File Dispatcher
=====================================
Routes incoming files to the correct format-specific parser based on
file extension.  Each parser is responsible for producing a list of
ChunkRecord objects, which are then fed to the JSONLEmitter.

Phase 2: MarkdownParser and WordParser are now wired.
         ExcelParser and OpenAPIParser remain stubbed for Phases 3–4.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .emitter import JSONLEmitter
from .models import ChunkRecord, EmitterConfig
from .parsers.markdown_parser import MarkdownParser
from .parsers.word_parser import WordParser

logger = logging.getLogger(__name__)

# ── File extension → parser mapping ────────────────────────────────

SUPPORTED_EXTENSIONS = {
    ".xlsx": "ExcelParser",
    ".docx": "WordParser",
    ".md": "MarkdownParser",
    ".json": "OpenAPIParser",
    ".yaml": "OpenAPIParser",
    ".yml": "OpenAPIParser",
    ".txt": "MarkdownParser",  # plain text treated as headerless markdown
}


class SemanticRouter:
    """
    Orchestrates the full ingestion pipeline:
      1. Scan an input directory (or accept individual files).
      2. Route each file to its specialised parser.
      3. Collect ChunkRecords from the parser.
      4. Feed them to the JSONLEmitter.

    Phase 2: MarkdownParser and WordParser are live.
             ExcelParser and OpenAPIParser return empty (stubs).
    """

    def __init__(self, config: EmitterConfig) -> None:
        self._config = config
        self._emitter = JSONLEmitter(config)
        self._skipped_files: List[str] = []
        self._processed_files: List[str] = []

        # ── Lazy-init parser instances ──────────────────────────────
        self._md_parser = MarkdownParser()
        self._word_parser = WordParser()

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

        # ── Dispatch to parser ──────────────────────────────────────
        chunks = self._dispatch(parser_name, path)

        if chunks:
            self._emitter.emit_many(chunks)
            self._processed_files.append(file_path)
            logger.info(
                "Emitted %d chunks from %s", len(chunks), path.name
            )
        else:
            logger.info("No chunks produced for %s (parser may be stubbed).", file_path)
            self._skipped_files.append(file_path)

        return chunks

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

    def _dispatch(self, parser_name: str, file_path: Path) -> List[ChunkRecord]:
        """
        Dispatch to the correct parser and convert results to ChunkRecords.

        Phase 2: MarkdownParser and WordParser are wired.
        Phase 3–4: ExcelParser and OpenAPIParser remain stubbed.
        """
        raw_chunks: List[Dict[str, str]] = []

        if parser_name == "MarkdownParser":
            raw_chunks = self._md_parser.parse_file(str(file_path))
        elif parser_name == "WordParser":
            raw_chunks = self._word_parser.parse_file(str(file_path))
        else:
            # TODO Phase 3: Wire ExcelParser
            # TODO Phase 4: Wire OpenAPIParser
            logger.warning(
                "Parser '%s' is not yet implemented (stub). "
                "File skipped: %s",
                parser_name,
                file_path,
            )
            return []

        # ── Convert parser dicts → ChunkRecords ────────────────────
        return self._to_chunk_records(raw_chunks, file_path)

    def _to_chunk_records(
        self,
        raw_chunks: List[Dict[str, str]],
        file_path: Path,
    ) -> List[ChunkRecord]:
        """
        Convert parser output dicts into fully stamped ChunkRecords
        using the pipeline config defaults.
        """
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        records: List[ChunkRecord] = []

        for chunk_dict in raw_chunks:
            raw_context = chunk_dict.get("raw_context", "").strip()
            if not raw_context:
                continue

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

    # ── Context Manager ─────────────────────────────────────────────

    def __enter__(self) -> "SemanticRouter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
