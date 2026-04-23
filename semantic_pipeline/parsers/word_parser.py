"""
WordParser — DOCX to Semantic Chunks via Markdown Conversion
==============================================================
Reads Word documents using python-docx, converts them to a valid
Markdown string by mapping Word heading styles to Markdown header
syntax, then delegates to MarkdownParser for semantic chunking.

This ensures DOCX and .md files share identical chunking semantics
— the only difference is the initial extraction step.

Heading style mapping:
    'Heading 1' / 'Title'  → #
    'Heading 2'            → ##
    'Heading 3'            → ###
    'Heading 4'            → ####
    Everything else        → body paragraph
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from docx import Document

from .markdown_parser import MarkdownParser

logger = logging.getLogger(__name__)

# ── Word style → Markdown heading map ─────────────────────────────
HEADING_STYLE_MAP: Dict[str, str] = {
    "Title": "#",
    "Heading 1": "#",
    "Heading 2": "##",
    "Heading 3": "###",
    "Heading 4": "####",
}


class WordParser:
    """
    Converts .docx files into semantic chunks.

    Pipeline:
        1. python-docx extracts paragraphs with style metadata.
        2. Heading styles are mapped to Markdown headers.
        3. The resulting Markdown string is passed to MarkdownParser.

    Usage:
        parser = WordParser()
        chunks = parser.parse_file("/path/to/doc.docx")
    """

    def __init__(
        self,
        chunk_size: int = 2000,
        chunk_overlap: int = 200,
    ) -> None:
        self._md_parser = MarkdownParser(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    # ── Public API ──────────────────────────────────────────────────

    def parse_file(self, file_path: str) -> List[Dict[str, str]]:
        """
        Read a .docx file, convert to Markdown, then semantically chunk it.

        Returns:
            List of dicts with keys: "raw_context", "source_file"
        """
        path = Path(file_path)
        logger.info("WordParser: Converting %s to Markdown.", path.name)

        doc = Document(str(path))
        markdown_text = self._docx_to_markdown(doc)

        logger.debug(
            "Converted %s to %d chars of Markdown.", path.name, len(markdown_text)
        )

        # Delegate to the shared MarkdownParser
        chunks = self._md_parser.parse_text(
            markdown_text, source_file=path.name
        )

        logger.info(
            "WordParser produced %d chunks from %s", len(chunks), path.name
        )
        return chunks

    # ── Internal: DOCX → Markdown Conversion ────────────────────────

    @staticmethod
    def _docx_to_markdown(doc: Document) -> str:
        """
        Walk all body elements in a Word document (paragraphs AND tables)
        in document order and produce a well-formed Markdown string.

        Rules:
          • Heading paragraphs → `# heading text` (with correct level)
          • Body paragraphs → plain text separated by blank lines
          • Empty paragraphs → ignored (collapse whitespace)
          • List items → prefixed with "- " for bullet-style
          • Tables → converted to Markdown table syntax:
              | Col1 | Col2 |
              |---|---|
              | Val1 | Val2 |
        """
        from docx.table import Table as DocxTable
        from docx.text.paragraph import Paragraph

        lines: List[str] = []

        # Iterate the document body in element order so that tables
        # appear at their correct position relative to paragraphs.
        for element in doc.element.body:
            tag = element.tag.split("}")[-1]  # strip namespace

            if tag == "p":
                para = Paragraph(element, doc)
                text = para.text.strip()
                if not text:
                    continue

                style_name = para.style.name if para.style else ""

                if style_name in HEADING_STYLE_MAP:
                    prefix = HEADING_STYLE_MAP[style_name]
                    if lines and lines[-1] != "":
                        lines.append("")
                    lines.append(f"{prefix} {text}")
                    lines.append("")  # blank line after heading
                elif style_name.startswith("List"):
                    lines.append(f"- {text}")
                else:
                    lines.append(text)
                    lines.append("")  # paragraph spacing

            elif tag == "tbl":
                table = DocxTable(element, doc)
                md_table = WordParser._table_to_markdown(table)
                if md_table:
                    if lines and lines[-1] != "":
                        lines.append("")
                    lines.append(md_table)
                    lines.append("")  # spacing after table

        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _table_to_markdown(table) -> str:
        """
        Convert a python-docx Table object into Markdown table syntax.

        Example output:
            | Name | Score | Grade |
            |---|---|---|
            | Alice | 95 | A |
            | Bob | 88 | B |
        """
        rows = table.rows
        if not rows:
            return ""

        md_rows: List[str] = []

        for i, row in enumerate(rows):
            cells = [cell.text.strip().replace("|", "\\|") for cell in row.cells]
            md_rows.append("| " + " | ".join(cells) + " |")

            # Add separator after the first row (header)
            if i == 0:
                md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")

        return "\n".join(md_rows)

