"""
Data Models for the Semantic Chunking Pipeline.

Defines the strict JSONL schema required by the downstream ingestion API.
All chunks emitted by any parser MUST conform to this schema before being
written to disk by the JSONLEmitter.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _generate_chunk_id() -> str:
    """Generate a deterministic-format unique chunk ID."""
    return f"chunk-{uuid.uuid4().hex[:12]}"


class ChunkRecord(BaseModel):
    """
    Canonical schema for a single JSONL record destined for the
    RAG ingestion API.

    The downstream system calculates 768-dimensional embeddings
    exclusively on `raw_context`, so this field must contain
    semantically pure, self-contained text.
    """

    usecase_id: str = Field(
        ...,
        min_length=1,
        description="Identifier for the business use-case this chunk belongs to.",
    )
    document_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for the source document.",
    )
    chunk_id: str = Field(
        default_factory=_generate_chunk_id,
        description="Unique identifier for this individual chunk.",
    )
    raw_context: str = Field(
        ...,
        min_length=1,
        description=(
            "The semantically pure text chunk. This is the ONLY field "
            "used for embedding generation."
        ),
    )
    file_name: str = Field(
        ...,
        min_length=1,
        description="Original source file name (with extension).",
    )
    data_classification: str = Field(
        default="internal",
        description="Data classification label (e.g. internal, confidential, public).",
    )
    sor_last_modified: str = Field(
        default_factory=lambda: datetime.now().strftime("%m/%d/%Y"),
        description="System-of-record last modified date in MM/DD/YYYY format.",
    )
    identifier: str = Field(
        default="",
        description="Custom field for team/use-case filtering.",
    )

    # ── Validators ──────────────────────────────────────────────────

    @field_validator("sor_last_modified")
    @classmethod
    def _validate_date_format(cls, v: str) -> str:
        """Enforce MM/DD/YYYY date format."""
        try:
            datetime.strptime(v, "%m/%d/%Y")
        except ValueError:
            raise ValueError(
                f"sor_last_modified must be in MM/DD/YYYY format, got: '{v}'"
            )
        return v

    # ── Helpers ─────────────────────────────────────────────────────

    def to_jsonl_line(self) -> str:
        """Serialize to a single compact JSON line (no trailing newline)."""
        return self.model_dump_json()

    def byte_size(self) -> int:
        """Return the byte size of this record when serialized as a JSONL line + newline."""
        return len(self.to_jsonl_line().encode("utf-8")) + 1  # +1 for '\n'


class EmitterConfig(BaseModel):
    """Configuration for the JSONLEmitter."""

    output_dir: str = Field(
        default="output",
        description="Directory where JSONL output files are written.",
    )
    base_filename: str = Field(
        default="chunks",
        description="Base name for output files (e.g. chunks_001.jsonl).",
    )
    max_file_bytes: int = Field(
        default=9_500_000,  # 9.5 MB — safe margin under the strict 10 MB API limit
        gt=0,
        description="Maximum bytes per output JSONL file before rotation.",
    )
    usecase_id: str = Field(
        default="default",
        description="Default usecase_id to stamp on chunks.",
    )
    data_classification: str = Field(
        default="internal",
        description="Default data classification label.",
    )
    identifier: str = Field(
        default="",
        description="Default identifier for team/use-case filtering.",
    )
