"""
JSONLEmitter — File Writer with Strict 10 MB Rollover
======================================================
Accepts ChunkRecord instances and writes them as newline-delimited JSON
to output files, automatically rotating to a new file the moment the
current file approaches the configured byte ceiling (default 9.5 MB).

Architecture notes:
  • The emitter is file-format agnostic — it only cares about ChunkRecords.
  • Parsers upstream produce ChunkRecords; the emitter consumes them.
  • File rotation is **pre-checked**: we measure the *would-be* size
    before writing.  If it would exceed the cap, we rotate first.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from .models import ChunkRecord, EmitterConfig

logger = logging.getLogger(__name__)


class JSONLEmitter:
    """
    Writes ChunkRecord objects to size-capped JSONL files.

    Usage:
        config = EmitterConfig(output_dir="output", base_filename="chunks")
        emitter = JSONLEmitter(config)
        emitter.emit(chunk_record)
        ...
        emitter.close()  # flush and finalize
    """

    def __init__(self, config: EmitterConfig) -> None:
        self._config = config
        self._output_dir = Path(config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._file_index: int = 1
        self._current_file: Optional[object] = None
        self._current_file_path: Optional[Path] = None
        self._current_bytes: int = 0
        self._total_chunks_written: int = 0
        self._files_created: List[str] = []

        # Open the first file immediately
        self._rotate_file()

    # ── Public API ──────────────────────────────────────────────────

    def emit(self, chunk: ChunkRecord) -> None:
        """
        Write a single ChunkRecord to the current output file.
        Automatically rotates to a new file if the write would
        exceed the byte ceiling.
        """
        line = chunk.to_jsonl_line() + "\n"
        line_bytes = len(line.encode("utf-8"))

        # Guard: a single chunk larger than max_file_bytes is an error
        if line_bytes > self._config.max_file_bytes:
            logger.error(
                "Single chunk exceeds max file size (%d > %d bytes). "
                "chunk_id=%s, file_name=%s. Skipping.",
                line_bytes,
                self._config.max_file_bytes,
                chunk.chunk_id,
                chunk.file_name,
            )
            raise ValueError(
                f"Single chunk ({line_bytes} bytes) exceeds the "
                f"max file size ({self._config.max_file_bytes} bytes). "
                f"chunk_id={chunk.chunk_id}"
            )

        # Pre-check: would this write push us over the limit?
        if self._current_bytes + line_bytes > self._config.max_file_bytes:
            logger.info(
                "File %s reached %d bytes; rotating before adding %d more.",
                self._current_file_path,
                self._current_bytes,
                line_bytes,
            )
            self._rotate_file()

        self._current_file.write(line)
        self._current_bytes += line_bytes
        self._total_chunks_written += 1

    def emit_many(self, chunks: List[ChunkRecord]) -> None:
        """Convenience: emit a list of chunks sequentially."""
        for chunk in chunks:
            self.emit(chunk)

    def close(self) -> None:
        """Flush and close the current file handle."""
        if self._current_file and not self._current_file.closed:
            self._current_file.flush()
            self._current_file.close()
            logger.info(
                "Closed %s (%d bytes).",
                self._current_file_path,
                self._current_bytes,
            )

    @property
    def stats(self) -> dict:
        """Return a summary of the emitter's activity."""
        return {
            "total_chunks_written": self._total_chunks_written,
            "files_created": list(self._files_created),
            "files_count": len(self._files_created),
            "current_file": str(self._current_file_path),
            "current_file_bytes": self._current_bytes,
        }

    # ── Internal ────────────────────────────────────────────────────

    def _rotate_file(self) -> None:
        """Close the current file (if open) and open the next numbered one."""
        # Close existing handle
        if self._current_file and not self._current_file.closed:
            self._current_file.flush()
            self._current_file.close()
            logger.info(
                "Finalized %s (%d bytes).",
                self._current_file_path,
                self._current_bytes,
            )

        # Build new filename: chunks_001.jsonl, chunks_002.jsonl, …
        filename = f"{self._config.base_filename}_{self._file_index:03d}.jsonl"
        self._current_file_path = self._output_dir / filename
        self._current_file = open(self._current_file_path, "w", encoding="utf-8")
        self._current_bytes = 0
        self._files_created.append(str(self._current_file_path))
        self._file_index += 1

        logger.info("Opened new output file: %s", self._current_file_path)

    # ── Context Manager ─────────────────────────────────────────────

    def __enter__(self) -> "JSONLEmitter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
