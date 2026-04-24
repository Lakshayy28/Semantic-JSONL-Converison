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
from typing import Dict, List, Optional

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from .markdown_parser import MarkdownParser
from ..vision_client import GeminiVisionClient, DECORATIVE_MARKER

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
        vision_client: Optional[GeminiVisionClient] = None,
    ) -> None:
        self._md_parser = MarkdownParser(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._vision_client = vision_client or GeminiVisionClient()

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
        markdown_text = self._docx_to_markdown(doc, self._vision_client)

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
    def _docx_to_markdown(
        doc: Document,
        vision_client: Optional[GeminiVisionClient] = None,
    ) -> str:
        """
        Walk all body elements in a Word document (paragraphs, tables,
        and inline images) in document order and produce a well-formed
        Markdown string.

        Rules:
          • Heading paragraphs → `# heading text` (with correct level)
          • Body paragraphs → plain text separated by blank lines
          • Empty paragraphs → ignored (collapse whitespace)
          • List items → prefixed with "- " for bullet-style
          • Tables → converted to Markdown table syntax:
              | Col1 | Col2 |
              |---|---|
              | Val1 | Val2 |
          • Inline images → triaged via GeminiVisionClient;
            flowcharts are transcribed and injected as
            [Diagram Transcription: ...]
        """
        from docx.table import Table as DocxTable
        from docx.text.paragraph import Paragraph

        lines: List[str] = []
        last_paragraph_text = ""  # used as surrounding context for images

        # Iterate the document body in element order so that tables
        # appear at their correct position relative to paragraphs.
        for element in doc.element.body:
            tag = element.tag.split("}")[-1]  # strip namespace

            if tag == "p":
                para = Paragraph(element, doc)
                text = para.text.strip()

                # ── Check for inline images in this paragraph ──────
                if vision_client is not None:
                    for run in para.runs:
                        inline_shapes = run.element.findall(
                            './/{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline'
                        )
                        for inline in inline_shapes:
                            blip = inline.find(
                                './/{http://schemas.openxmlformats.org/drawingml/2006/main}blip'
                            )
                            if blip is not None:
                                embed_id = blip.get(
                                    '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
                                )
                                if embed_id and embed_id in doc.part.rels:
                                    image_part = doc.part.rels[embed_id].target_part
                                    image_bytes = image_part.blob
                                    transcription = vision_client.transcribe_image(
                                        image_bytes,
                                        surrounding_context=last_paragraph_text,
                                    )
                                    if transcription != DECORATIVE_MARKER:
                                        if lines and lines[-1] != "":
                                            lines.append("")
                                        lines.append(
                                            f"[Diagram Transcription: {transcription}]"
                                        )
                                        lines.append("")

                if not text:
                    continue

                last_paragraph_text = text
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

