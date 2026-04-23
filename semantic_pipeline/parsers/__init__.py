"""
Parsers Package
================
Format-specific semantic chunking parsers.

Each parser accepts a file path and returns a list of ParsedChunk
dicts that the SemanticRouter converts into ChunkRecords.
"""

from .excel_parser import ExcelParser
from .markdown_parser import MarkdownParser
from .word_parser import WordParser

__all__ = ["ExcelParser", "MarkdownParser", "WordParser"]

