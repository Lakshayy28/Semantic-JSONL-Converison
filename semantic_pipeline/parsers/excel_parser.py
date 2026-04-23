"""
ExcelParser — Merged-Cell-Aware Row-Level Tabular Chunker
==========================================================
Processes .xlsx files sheet-by-sheet with full merged cell resolution,
then stringifies each row by mapping column headers to cell values.

Pipeline:
  1. openpyxl loads the workbook and unmerges all merged cell ranges,
     copying the top-left value into every cell that was part of the merge.
  2. The cleaned workbook is written to an in-memory BytesIO buffer
     (zero disk I/O).
  3. pandas reads all sheets from the buffer.
  4. Each row is stringified as:
     "[Sheet: <name>] [<col1>: <val1>] [<col2>: <val2>] ..."
  5. Empty rows are dropped; NaN values are omitted from the chunk.

Why not just use pandas directly?
  Merged cells → pandas sees NaN in all but the top-left cell.
  Row-level stringification → blind splitters destroy column-header context.
"""

from __future__ import annotations

import io
import logging
from copy import copy
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell

logger = logging.getLogger(__name__)


class ExcelParser:
    """
    Semantic chunker for Excel (.xlsx) workbooks.

    Each row in each sheet becomes an independent chunk with full
    column-header context and sheet name injected into raw_context.

    Usage:
        parser = ExcelParser()
        chunks = parser.parse_file("/path/to/data.xlsx")
    """

    # ── Public API ──────────────────────────────────────────────────

    def parse_file(self, file_path: str) -> List[Dict[str, str]]:
        """
        Read an .xlsx file, unmerge cells, and produce row-level chunks.

        Returns:
            List of dicts with keys: "raw_context", "source_file"
        """
        path = Path(file_path)
        logger.info("ExcelParser: Loading %s", path.name)

        # Step 1: Load with openpyxl and unmerge all cells
        wb = load_workbook(str(path))
        self._unmerge_all_cells(wb)

        # Step 2: Write cleaned workbook to in-memory buffer
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        wb.close()

        # Step 3: Read all sheets with pandas
        sheets = pd.read_excel(buffer, sheet_name=None, engine="openpyxl")

        # Step 4: Stringify each row
        chunks: List[Dict[str, str]] = []
        for sheet_name, df in sheets.items():
            sheet_chunks = self._stringify_sheet(df, sheet_name, path.name)
            chunks.extend(sheet_chunks)

        logger.info(
            "ExcelParser produced %d chunks from %s (%d sheets).",
            len(chunks),
            path.name,
            len(sheets),
        )
        return chunks

    def parse_buffer(
        self,
        buffer: io.BytesIO,
        source_file: str = "unknown.xlsx",
    ) -> List[Dict[str, str]]:
        """
        Parse from an in-memory buffer (useful for testing without disk I/O).
        """
        # Load, unmerge, re-save to new buffer
        buffer.seek(0)
        wb = load_workbook(buffer)
        self._unmerge_all_cells(wb)

        clean_buffer = io.BytesIO()
        wb.save(clean_buffer)
        clean_buffer.seek(0)
        wb.close()

        sheets = pd.read_excel(clean_buffer, sheet_name=None, engine="openpyxl")

        chunks: List[Dict[str, str]] = []
        for sheet_name, df in sheets.items():
            sheet_chunks = self._stringify_sheet(df, sheet_name, source_file)
            chunks.extend(sheet_chunks)

        return chunks

    # ── Internal: Merged Cell Resolution ────────────────────────────

    @staticmethod
    def _unmerge_all_cells(wb) -> None:
        """
        Iterate every sheet in the workbook. For each merged range:
          1. Extract the value from the top-left cell.
          2. Unmerge the range.
          3. Write the extracted value into every cell that was merged.

        This ensures pandas sees the correct value in every cell,
        not NaN in the non-top-left positions.
        """
        for ws in wb.worksheets:
            # We must collect ranges first because unmerging while
            # iterating mutates the collection.
            merged_ranges = list(ws.merged_cells.ranges)

            for merge_range in merged_ranges:
                # Top-left cell holds the value
                min_row = merge_range.min_row
                min_col = merge_range.min_col
                top_left_value = ws.cell(row=min_row, column=min_col).value

                # Unmerge first (required by openpyxl before writing to merged cells)
                ws.unmerge_cells(str(merge_range))

                # Fill every cell in the previously merged range
                for row in range(merge_range.min_row, merge_range.max_row + 1):
                    for col in range(merge_range.min_col, merge_range.max_col + 1):
                        ws.cell(row=row, column=col).value = top_left_value

            if merged_ranges:
                logger.info(
                    "Unmerged %d cell ranges in sheet '%s'.",
                    len(merged_ranges),
                    ws.title,
                )

    # ── Internal: Row Stringification ───────────────────────────────

    @staticmethod
    def _stringify_sheet(
        df: pd.DataFrame,
        sheet_name: str,
        source_file: str,
    ) -> List[Dict[str, str]]:
        """
        Convert a dataframe into row-level chunk dicts.

        Format: "[Sheet: <name>] [<col>: <val>] [<col>: <val>] ..."

        Rules:
          • Completely empty rows are dropped.
          • NaN values are omitted from the chunk string.
          • Column headers are cleaned (stripped of whitespace).
        """
        # Drop fully empty rows
        df = df.dropna(how="all").reset_index(drop=True)

        # Clean column names
        columns = [str(c).strip() for c in df.columns]

        chunks: List[Dict[str, str]] = []

        for _, row in df.iterrows():
            parts: List[str] = [f"[Sheet: {sheet_name}]"]

            for col_name, value in zip(columns, row.values):
                # Skip NaN / None
                if pd.isna(value):
                    continue
                # Convert value to string and strip
                val_str = str(value).strip()
                if val_str:
                    parts.append(f"[{col_name}: {val_str}]")

            # A row with only the sheet header has no data — skip it
            if len(parts) <= 1:
                continue

            raw_context = " ".join(parts)
            chunks.append({
                "raw_context": raw_context,
                "source_file": source_file,
            })

        return chunks
