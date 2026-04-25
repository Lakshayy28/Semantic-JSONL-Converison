"""
Phase 1 Tests — Data Models, JSONLEmitter Rollover, SemanticRouter Core
========================================================================
These tests verify:
  1. ChunkRecord schema validation and serialization.
  2. JSONLEmitter 10MB file rotation under realistic payloads.
  3. SemanticRouter core dispatch behaviour.
"""

from __future__ import annotations
import os

import json
from pathlib import Path

import pytest

from semantic_pipeline.models import ChunkRecord, EmitterConfig
from semantic_pipeline.emitter import JSONLEmitter
from semantic_pipeline.router import SemanticRouter, SUPPORTED_EXTENSIONS


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary output directory."""
    out = tmp_path / "jsonl_output"
    out.mkdir()
    return out


@pytest.fixture
def sample_chunk() -> ChunkRecord:
    """A minimal valid ChunkRecord."""
    return ChunkRecord(
        usecase_id="credit-decisioning",
        document_id="doc-001",
        raw_context="This is a sample raw context for embedding.",
        file_name="sample.md",
        data_classification="internal",
        sor_last_modified="04/23/2026",
        identifier="underwriting-team",
    )


@pytest.fixture
def make_chunk():
    """Factory fixture: create chunks with custom raw_context size."""

    def _make(
        raw_context: str = "default context",
        usecase_id: str = "uc-001",
        document_id: str = "doc-001",
        file_name: str = "test.md",
    ) -> ChunkRecord:
        return ChunkRecord(
            usecase_id=usecase_id,
            document_id=document_id,
            raw_context=raw_context,
            file_name=file_name,
            sor_last_modified="01/01/2026",
            identifier="test",
        )

    return _make


# ═══════════════════════════════════════════════════════════════════
# 1. ChunkRecord Model Tests
# ═══════════════════════════════════════════════════════════════════


class TestChunkRecord:
    """Validate the Pydantic data model for JSONL chunks."""

    def test_valid_chunk_creation(self, sample_chunk: ChunkRecord):
        """A well-formed chunk should instantiate without errors."""
        assert sample_chunk.usecase_id == "credit-decisioning"
        assert sample_chunk.document_id == "doc-001"
        assert sample_chunk.raw_context.startswith("This is a sample")
        assert sample_chunk.chunk_id.startswith("chunk-")
        assert len(sample_chunk.chunk_id) == 18  # "chunk-" + 12 hex chars

    def test_auto_generated_chunk_id(self, make_chunk):
        """Each chunk should receive a unique auto-generated ID."""
        c1 = make_chunk(raw_context="first")
        c2 = make_chunk(raw_context="second")
        assert c1.chunk_id != c2.chunk_id

    def test_serialization_roundtrip(self, sample_chunk: ChunkRecord):
        """Serialize to JSON line and parse back; all fields must survive."""
        line = sample_chunk.to_jsonl_line()
        parsed = json.loads(line)
        assert parsed["usecase_id"] == "credit-decisioning"
        assert parsed["raw_context"] == sample_chunk.raw_context
        assert parsed["file_name"] == "sample.md"
        assert parsed["sor_last_modified"] == "04/23/2026"

    def test_byte_size_includes_newline(self, sample_chunk: ChunkRecord):
        """byte_size() must account for the trailing newline character."""
        expected = len(sample_chunk.to_jsonl_line().encode("utf-8")) + 1
        assert sample_chunk.byte_size() == expected

    def test_invalid_date_format_rejected(self):
        """Dates not in MM/DD/YYYY format must raise ValidationError."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            ChunkRecord(
                usecase_id="uc",
                document_id="doc",
                raw_context="text",
                file_name="f.txt",
                sor_last_modified="2026-04-23",  # wrong format
            )

    def test_empty_raw_context_rejected(self):
        """raw_context must not be empty — it drives embedding quality."""
        with pytest.raises(Exception):
            ChunkRecord(
                usecase_id="uc",
                document_id="doc",
                raw_context="",
                file_name="f.txt",
            )

    def test_empty_usecase_id_rejected(self):
        """usecase_id is mandatory and must not be empty."""
        with pytest.raises(Exception):
            ChunkRecord(
                usecase_id="",
                document_id="doc",
                raw_context="text",
                file_name="f.txt",
            )


# ═══════════════════════════════════════════════════════════════════
# 2. JSONLEmitter Tests
# ═══════════════════════════════════════════════════════════════════


class TestJSONLEmitter:
    """Validate JSONL writing and the critical 10 MB rollover logic."""

    def test_single_chunk_write(self, tmp_output_dir: Path, sample_chunk: ChunkRecord):
        """Writing one chunk should create exactly one file with one line."""
        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with JSONLEmitter(config) as emitter:
            emitter.emit(sample_chunk)

        files = list(tmp_output_dir.glob("*.jsonl"))
        assert len(files) == 1

        lines = files[0].read_text().strip().splitlines()
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["usecase_id"] == sample_chunk.usecase_id

    def test_multiple_chunks_single_file(
        self, tmp_output_dir: Path, make_chunk
    ):
        """Multiple small chunks should fit in a single file."""
        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with JSONLEmitter(config) as emitter:
            for i in range(100):
                emitter.emit(make_chunk(raw_context=f"Chunk number {i}"))

        files = list(tmp_output_dir.glob("*.jsonl"))
        assert len(files) == 1
        assert emitter.stats["total_chunks_written"] == 100

    def test_file_rotation_at_byte_limit(
        self, tmp_output_dir: Path, make_chunk
    ):
        """
        CRITICAL TEST: File must rotate BEFORE exceeding the byte cap.

        Strategy:
          - Set a small cap (5000 bytes) so rotation triggers quickly.
          - Write chunks until we've produced multiple files.
          - Verify every file is under the cap.
        """
        cap = 5_000  # 5 KB cap for fast test
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            max_file_bytes=cap,
        )

        with JSONLEmitter(config) as emitter:
            for i in range(200):
                emitter.emit(
                    make_chunk(raw_context=f"Row {i}: " + "x" * 80)
                )

        files = sorted(tmp_output_dir.glob("*.jsonl"))
        assert len(files) > 1, "Expected multiple files due to rotation."

        for f in files:
            size = f.stat().st_size
            assert size <= cap, (
                f"File {f.name} is {size} bytes, exceeding the {cap} byte cap."
            )

    def test_file_rotation_naming_sequence(
        self, tmp_output_dir: Path, make_chunk
    ):
        """Output files should be numbered sequentially: _001, _002, …"""
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            base_filename="out",
            max_file_bytes=1_000,
        )

        with JSONLEmitter(config) as emitter:
            for i in range(100):
                emitter.emit(make_chunk(raw_context="x" * 100))

        files = sorted(tmp_output_dir.glob("*.jsonl"))
        for idx, f in enumerate(files, start=1):
            assert f.name == f"out_{idx:03d}.jsonl", (
                f"Expected out_{idx:03d}.jsonl, got {f.name}"
            )

    def test_stats_tracking(self, tmp_output_dir: Path, make_chunk):
        """The stats dict should accurately reflect emitter activity."""
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            max_file_bytes=2_000,
        )

        with JSONLEmitter(config) as emitter:
            for i in range(50):
                emitter.emit(make_chunk(raw_context=f"Data point {i}"))

            stats = emitter.stats
            assert stats["total_chunks_written"] == 50
            assert stats["files_count"] >= 1
            assert isinstance(stats["files_created"], list)

    def test_emit_many_convenience(
        self, tmp_output_dir: Path, make_chunk
    ):
        """emit_many should behave identically to sequential emit calls."""
        config = EmitterConfig(output_dir=str(tmp_output_dir))
        chunks = [make_chunk(raw_context=f"Batch chunk {i}") for i in range(10)]

        with JSONLEmitter(config) as emitter:
            emitter.emit_many(chunks)
            assert emitter.stats["total_chunks_written"] == 10

    def test_oversized_single_chunk_raises(
        self, tmp_output_dir: Path, make_chunk
    ):
        """A single chunk larger than max_file_bytes must raise ValueError."""
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            max_file_bytes=100,  # tiny cap
        )

        with JSONLEmitter(config) as emitter:
            huge_chunk = make_chunk(raw_context="A" * 500)
            with pytest.raises(ValueError, match="exceeds the max file size"):
                emitter.emit(huge_chunk)

    def test_realistic_10mb_rotation(self, tmp_output_dir: Path, make_chunk):
        """
        Stress test: Simulate realistic 9.5 MB cap with ~1 KB chunks.
        Verify all output files respect the limit.
        """
        cap = 9_500_000  # 9.5 MB — the production default
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            max_file_bytes=cap,
        )

        # ~1 KB per chunk × 20,000 chunks ≈ 20 MB → expect ≥ 3 files
        chunk_text = "A" * 900

        with JSONLEmitter(config) as emitter:
            for i in range(20_000):
                emitter.emit(make_chunk(raw_context=f"[{i:05d}] {chunk_text}"))

        files = sorted(tmp_output_dir.glob("*.jsonl"))
        assert len(files) >= 2, (
            f"Expected at least 2 files for 20 MB of data, got {len(files)}."
        )

        for f in files:
            size = f.stat().st_size
            assert size <= cap, (
                f"CRITICAL: {f.name} is {size} bytes, exceeding {cap} byte cap."
            )

    def test_output_is_valid_jsonl(self, tmp_output_dir: Path, make_chunk):
        """Every line in every output file must parse as valid JSON."""
        config = EmitterConfig(
            output_dir=str(tmp_output_dir),
            max_file_bytes=3_000,
        )

        with JSONLEmitter(config) as emitter:
            for i in range(50):
                emitter.emit(make_chunk(raw_context=f"Validate JSON line {i}"))

        for f in sorted(tmp_output_dir.glob("*.jsonl")):
            for line_num, line in enumerate(f.read_text().splitlines(), 1):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    pytest.fail(
                        f"Invalid JSON at {f.name}:{line_num}: {line[:80]}…"
                    )
                # Also verify schema fields exist
                assert "raw_context" in obj
                assert "chunk_id" in obj
                assert "usecase_id" in obj


# ═══════════════════════════════════════════════════════════════════
# 3. SemanticRouter Core Tests
# ═══════════════════════════════════════════════════════════════════


class TestSemanticRouter:
    """Verify the router dispatches correctly and handles edge cases."""

    def test_supported_extensions_defined(self):
        """All target file types must be registered."""
        for ext in [".xlsx", ".docx", ".md", ".json", ".yaml", ".yml", ".txt"]:
            assert ext in SUPPORTED_EXTENSIONS, f"{ext} not in SUPPORTED_EXTENSIONS"

    def test_ingest_unsupported_file_skips(self, tmp_output_dir: Path, tmp_path: Path):
        """An unsupported file type should be skipped, not crash."""
        # Create a dummy unsupported file
        bad_file = tmp_path / "data.csv"
        bad_file.write_text("a,b,c\n1,2,3\n")

        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(bad_file))

        assert chunks == []

    def test_all_parsers_operational(
        self, tmp_output_dir: Path, tmp_path: Path
    ):
        """All registered extensions now have live parsers — verify .json works."""
        json_file = tmp_path / "config.json"
        json_file.write_text('{"key": "value", "num": 42}')

        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(json_file))

        # Generic JSON should produce at least 1 chunk via fallback
        assert len(chunks) >= 1

    def test_ingest_directory_missing_raises(self, tmp_output_dir: Path):
        """Pointing at a non-existent directory should raise FileNotFoundError."""
        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with SemanticRouter(config) as router:
            with pytest.raises(FileNotFoundError):
                router.ingest_directory("/nonexistent/path/xyz")

    def test_summary_structure(self, tmp_output_dir: Path):
        """The summary dict should have the expected keys."""
        config = EmitterConfig(output_dir=str(tmp_output_dir))
        with SemanticRouter(config) as router:
            summary = router.summary()

        assert "processed_files" in summary
        assert "skipped_files" in summary
        assert "emitter_stats" in summary
