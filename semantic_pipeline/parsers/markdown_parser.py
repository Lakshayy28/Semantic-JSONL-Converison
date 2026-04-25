"""
MarkdownParser — Header-Aware Semantic Chunker
================================================
Parses Markdown text using LangChain's MarkdownHeaderTextSplitter to
preserve document hierarchy, then stitches header metadata back into
the raw_context field so that embeddings capture full semantic context.

Key design choices:
  • Headers are stitched into raw_context as a breadcrumb path
    (e.g., "Section: Overview > Subsection: Setup. Content: ...")
    because the downstream API computes embeddings ONLY on raw_context.
  • Large sections (>chunk_size) are sub-split with RecursiveCharacterTextSplitter
    with 10% overlap; parent headers are prepended to every sub-chunk.
  • Plain-text files (.txt) can also be routed here — they produce
    a single chunk since they lack header structure.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

logger = logging.getLogger(__name__)

# ── Header levels recognized by the splitter ──────────────────────
HEADERS_TO_SPLIT_ON = [
    ("#", "H1"),
    ("##", "H2"),
    ("###", "H3"),
    ("####", "H4"),
]

# ── Defaults ──────────────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200  # 10% of 2000


class MarkdownParser:
    """
    Semantic chunker for Markdown content.

    Usage:
        parser = MarkdownParser()
        chunks = parser.parse_file("/path/to/doc.md")
        # or
        chunks = parser.parse_text(markdown_string, source_filename="doc.md")

    Returns:
        List of dicts with keys: "raw_context", "source_file"
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=HEADERS_TO_SPLIT_ON,
            strip_headers=False,
        )

        self._recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
        )

    # ── Public API ──────────────────────────────────────────────────

    def parse_file(self, file_path: str) -> List[Dict[str, str]]:
        """Read a .md or .txt file from disk and parse it."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.parse_text(content, source_file=path.name)

    def parse_text(
        self,
        markdown_text: str,
        source_file: str = "unknown.md",
    ) -> List[Dict[str, str]]:
        """
        Parse a Markdown string into semantically chunked dicts.

        Each dict contains:
            - "raw_context": header breadcrumb + section content
            - "source_file": original filename
        """
        if not markdown_text.strip():
            logger.warning("Empty content for %s — skipping.", source_file)
            return []

        # Step 1: Split by headers
        header_docs = self._header_splitter.split_text(markdown_text)

        chunks: List[Dict[str, str]] = []

        for doc in header_docs:
            header_prefix = self._build_header_prefix(doc.metadata)
            section_content = doc.page_content.strip()

            if not section_content:
                continue

            # Step 2: Check if section needs recursive sub-splitting
            if len(section_content) > self._chunk_size:
                sub_chunks = self._recursive_splitter.split_text(section_content)
                logger.info(
                    "Sub-split large section (%d chars) into %d sub-chunks for %s",
                    len(section_content),
                    len(sub_chunks),
                    source_file,
                )
                for sub_text in sub_chunks:
                    raw_context = self._assemble_raw_context(
                        header_prefix, sub_text.strip()
                    )
                    chunks.append(
                        {"raw_context": raw_context, "source_file": source_file}
                    )
            else:
                raw_context = self._assemble_raw_context(
                    header_prefix, section_content
                )
                chunks.append(
                    {"raw_context": raw_context, "source_file": source_file}
                )

        logger.info(
            "MarkdownParser produced %d chunks from %s", len(chunks), source_file
        )
        return chunks

    # ── Internal Helpers ────────────────────────────────────────────

    @staticmethod
    def _build_header_prefix(metadata: dict) -> str:
        """
        Build a breadcrumb string from the header metadata dict.

        Input metadata example:
            {"H1": "Architecture", "H2": "Components", "H3": "Cache"}
        Output:
            "Section: Architecture > Components > Cache."
        """
        parts = []
        for key in ("H1", "H2", "H3", "H4"):
            if key in metadata:
                parts.append(metadata[key])

        if not parts:
            return ""

        return "Section: " + " > ".join(parts) + "."

    @staticmethod
    def _assemble_raw_context(header_prefix: str, content: str) -> str:
        """
        Combine header breadcrumb with section content into the
        final raw_context string for embedding.
        """
        if header_prefix:
            return f"{header_prefix} Content: {content}"
        return content
