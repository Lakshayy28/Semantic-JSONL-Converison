"""
Phase 6 Tests — Vision Extraction Module
==========================================
Validates:
  1. GeminiVisionClient payload schema (OpenAI-compatible, base64)
  2. WordParser image extraction → transcription injection
  3. ExcelParser image extraction → row-level injection
  4. Decorative triage (DECORATIVE_DISCARD filtering)
  5. Integration through the full SemanticRouter pipeline
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from semantic_pipeline.vision_client import (
    GeminiVisionClient,
    DECORATIVE_MARKER,
    SYSTEM_PROMPT,
)
from semantic_pipeline.parsers.word_parser import WordParser
from semantic_pipeline.parsers.excel_parser import ExcelParser
from semantic_pipeline.models import EmitterConfig
from semantic_pipeline.router import SemanticRouter


# ── Helpers ────────────────────────────────────────────────────────

def _create_1x1_png() -> bytes:
    """Create a minimal valid 1×1 red PNG image (67 bytes)."""
    import struct
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw_data = b"\x00\xff\x00\x00"  # filter byte + RGB
    idat = _chunk(b"IDAT", zlib.compress(raw_data))
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _make_docx_with_image(image_bytes: bytes) -> str:
    """Create a temporary DOCX file containing a paragraph + inline image."""
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.add_paragraph("Before the diagram.")

    # Write image to a temp file so add_picture can read it
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_f:
        img_f.write(image_bytes)
        img_path = img_f.name

    doc.add_picture(img_path, width=Inches(1.0))
    doc.add_paragraph("After the diagram.")

    path = tempfile.mktemp(suffix=".docx")
    doc.save(path)

    # Clean up the temporary image file
    Path(img_path).unlink(missing_ok=True)

    return path


def _make_xlsx_with_image(image_bytes: bytes, anchor_cell: str = "B2") -> str:
    """Create a temporary XLSX file with data and an image anchored to a cell."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XlImage

    wb = Workbook()
    ws = wb.active
    ws.title = "TestSheet"

    # Row 1 = headers
    ws["A1"] = "Name"
    ws["B1"] = "Score"
    ws["C1"] = "Grade"

    # Row 2 = data
    ws["A2"] = "Alice"
    ws["B2"] = 95
    ws["C2"] = "A"

    # Row 3 = more data
    ws["A3"] = "Bob"
    ws["B3"] = 88
    ws["C3"] = "B"

    # Write image to a temp file — must persist until wb.save() completes
    img_path = tempfile.mktemp(suffix=".png")
    with open(img_path, "wb") as f:
        f.write(image_bytes)

    img = XlImage(img_path)
    ws.add_image(img, anchor_cell)

    path = tempfile.mktemp(suffix=".xlsx")
    wb.save(path)
    wb.close()

    # Clean up temp image file now that save is done
    Path(img_path).unlink(missing_ok=True)

    return path


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 1 — GeminiVisionClient Payload Schema
# ═══════════════════════════════════════════════════════════════════


class TestPayloadSchema:
    """Verify the OpenAI-compatible payload structure."""

    def setup_method(self):
        self.client = GeminiVisionClient()
        self.dummy_bytes = b"test-image-bytes-1234"

    def test_payload_has_correct_model(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        assert payload["model"] == "gemini-2.5-pro"

    def test_payload_has_three_messages(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        assert len(payload["messages"]) == 3

    def test_system_message_is_first(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == SYSTEM_PROMPT

    def test_context_message_is_second(self):
        payload = self.client.prepare_payload(
            self.dummy_bytes, surrounding_context="credit rules"
        )
        msg = payload["messages"][1]
        assert msg["role"] == "user"
        assert "credit rules" in msg["content"]

    def test_image_message_is_third(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        msg = payload["messages"][2]
        assert msg["role"] == "user"
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "image_url"

    def test_base64_encoding_correct(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        url = payload["messages"][2]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        b64_part = url.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == self.dummy_bytes

    def test_top_p_is_set(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        assert payload["top_p"] == 1

    def test_payload_is_json_serializable(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        dumped = json.dumps(payload)
        assert isinstance(dumped, str)

    def test_empty_context_default(self):
        payload = self.client.prepare_payload(self.dummy_bytes)
        msg = payload["messages"][1]
        assert "The surrounding text context is: " in msg["content"]


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 2 — GeminiVisionClient Transcription
# ═══════════════════════════════════════════════════════════════════


class TestTranscription:
    """Verify transcribe_image calls prepare_payload and _execute_request."""

    def test_mock_returns_flowchart(self):
        client = GeminiVisionClient()
        result = client.transcribe_image(b"fake-image")
        assert result == "MOCK_TRANSCRIPTION_FLOWCHART: Step 1 -> Step 2"

    def test_custom_execute_request(self):
        client = GeminiVisionClient()
        client._execute_request = lambda p: "Custom Output"
        result = client.transcribe_image(b"fake-image")
        assert result == "Custom Output"

    def test_decorative_discard_passthrough(self):
        client = GeminiVisionClient()
        client._execute_request = lambda p: "DECORATIVE_DISCARD"
        result = client.transcribe_image(b"fake-image")
        assert result == DECORATIVE_MARKER


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 3 — WordParser Image Extraction
# ═══════════════════════════════════════════════════════════════════


class TestWordImageExtraction:
    """Verify DOCX inline images are extracted and transcribed."""

    def test_transcription_appears_in_chunks(self):
        """An image in a DOCX should produce a [Diagram Transcription: ...] tag."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            parser = WordParser()
            chunks = parser.parse_file(docx_path)
            combined = " ".join(c["raw_context"] for c in chunks)
            assert "MOCK_TRANSCRIPTION_FLOWCHART: Step 1 -> Step 2" in combined
            assert "[Diagram Transcription:" in combined
        finally:
            Path(docx_path).unlink(missing_ok=True)

    def test_surrounding_text_preserved(self):
        """Text before and after the image must still appear in chunks."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            parser = WordParser()
            chunks = parser.parse_file(docx_path)
            combined = " ".join(c["raw_context"] for c in chunks)
            assert "Before the diagram" in combined
            assert "After the diagram" in combined
        finally:
            Path(docx_path).unlink(missing_ok=True)

    def test_decorative_discard_in_word(self):
        """When the vision client returns DECORATIVE_DISCARD, no tag is injected."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            client = GeminiVisionClient()
            client._execute_request = lambda p: "DECORATIVE_DISCARD"

            parser = WordParser(vision_client=client)
            chunks = parser.parse_file(docx_path)
            combined = " ".join(c["raw_context"] for c in chunks)
            assert "[Diagram Transcription:" not in combined
            # Text should still be present
            assert "Before the diagram" in combined
        finally:
            Path(docx_path).unlink(missing_ok=True)

    def test_no_vision_client_no_crash(self):
        """A DOCX with images should still parse without errors if vision_client is default."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            # Default client uses mock — should just work
            parser = WordParser()
            chunks = parser.parse_file(docx_path)
            assert len(chunks) > 0
        finally:
            Path(docx_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 4 — ExcelParser Image Extraction
# ═══════════════════════════════════════════════════════════════════


class TestExcelImageExtraction:
    """Verify images anchored in Excel sheets are extracted and injected."""

    def test_image_transcription_in_row(self):
        """An image anchored to B2 should inject transcription into row 2's chunk."""
        image_bytes = _create_1x1_png()
        xlsx_path = _make_xlsx_with_image(image_bytes, anchor_cell="B2")

        try:
            parser = ExcelParser()
            chunks = parser.parse_file(xlsx_path)

            # Find the chunk for the row containing "Alice"
            alice_chunks = [c for c in chunks if "Alice" in c["raw_context"]]
            assert len(alice_chunks) >= 1

            alice_ctx = alice_chunks[0]["raw_context"]
            assert "[Image at Col B:" in alice_ctx
            assert "MOCK_TRANSCRIPTION_FLOWCHART" in alice_ctx
        finally:
            Path(xlsx_path).unlink(missing_ok=True)

    def test_non_image_rows_unaffected(self):
        """Rows without images should not have [Image at ...] tags."""
        image_bytes = _create_1x1_png()
        xlsx_path = _make_xlsx_with_image(image_bytes, anchor_cell="B2")

        try:
            parser = ExcelParser()
            chunks = parser.parse_file(xlsx_path)

            # Bob is in row 3, image is only on row 2
            bob_chunks = [c for c in chunks if "Bob" in c["raw_context"]]
            assert len(bob_chunks) >= 1
            assert "[Image at" not in bob_chunks[0]["raw_context"]
        finally:
            Path(xlsx_path).unlink(missing_ok=True)

    def test_decorative_discard_in_excel(self):
        """Images classified as decorative should not appear in any chunk."""
        image_bytes = _create_1x1_png()
        xlsx_path = _make_xlsx_with_image(image_bytes, anchor_cell="B2")

        try:
            client = GeminiVisionClient()
            client._execute_request = lambda p: "DECORATIVE_DISCARD"

            parser = ExcelParser(vision_client=client)
            chunks = parser.parse_file(xlsx_path)

            combined = " ".join(c["raw_context"] for c in chunks)
            assert "[Image at" not in combined
            # Data should still be present
            assert "Alice" in combined
            assert "Bob" in combined
        finally:
            Path(xlsx_path).unlink(missing_ok=True)

    def test_data_integrity_with_images(self):
        """All tabular data should remain intact when images are present."""
        image_bytes = _create_1x1_png()
        xlsx_path = _make_xlsx_with_image(image_bytes, anchor_cell="B2")

        try:
            parser = ExcelParser()
            chunks = parser.parse_file(xlsx_path)

            combined = " ".join(c["raw_context"] for c in chunks)
            assert "Alice" in combined
            assert "95" in combined
            assert "Bob" in combined
            assert "88" in combined
        finally:
            Path(xlsx_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 5 — Triage Logic (DECORATIVE_DISCARD)
# ═══════════════════════════════════════════════════════════════════


class TestTriageLogic:
    """Verify the triage filtering works across both parsers."""

    def test_decorative_marker_constant(self):
        assert DECORATIVE_MARKER == "DECORATIVE_DISCARD"

    def test_decorative_result_stripped(self):
        """If _execute_request returns DECORATIVE_DISCARD with whitespace, it's trimmed."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: "  DECORATIVE_DISCARD  "
        result = client.transcribe_image(b"fake")
        assert result == DECORATIVE_MARKER

    def test_mock_is_not_decorative(self):
        """The default mock should NOT match DECORATIVE_DISCARD."""
        client = GeminiVisionClient()
        result = client.transcribe_image(b"fake")
        assert result != DECORATIVE_MARKER


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 6 — Integration through SemanticRouter
# ═══════════════════════════════════════════════════════════════════


class TestPhase6Integration:
    """End-to-end integration: DOCX and XLSX with images through the router."""

    def test_router_docx_with_image(self):
        """Full pipeline: DOCX with image → ChunkRecords with transcription."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config = EmitterConfig(
                    output_dir=tmpdir,
                    base_filename="vision_test",
                    usecase_id="vision",
                )

                with SemanticRouter(config) as router:
                    records = router.ingest_file(docx_path)

                assert len(records) > 0
                combined = " ".join(r.raw_context for r in records)
                assert "MOCK_TRANSCRIPTION_FLOWCHART" in combined
        finally:
            Path(docx_path).unlink(missing_ok=True)

    def test_router_xlsx_with_image(self):
        """Full pipeline: XLSX with image → ChunkRecords with transcription."""
        image_bytes = _create_1x1_png()
        xlsx_path = _make_xlsx_with_image(image_bytes, anchor_cell="B2")

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config = EmitterConfig(
                    output_dir=tmpdir,
                    base_filename="vision_xlsx",
                    usecase_id="vision",
                )

                with SemanticRouter(config) as router:
                    records = router.ingest_file(xlsx_path)

                assert len(records) > 0
                alice_records = [r for r in records if "Alice" in r.raw_context]
                assert len(alice_records) >= 1
                assert "Image at Col B" in alice_records[0].raw_context
        finally:
            Path(xlsx_path).unlink(missing_ok=True)

    def test_unchunked_docx_with_image(self):
        """Unchunked mode: single record should still contain image transcription."""
        image_bytes = _create_1x1_png()
        docx_path = _make_docx_with_image(image_bytes)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config = EmitterConfig(
                    output_dir=tmpdir,
                    base_filename="vision_unchunked",
                    usecase_id="vision",
                )

                with SemanticRouter(config) as router:
                    records = router.ingest_file_unchunked(docx_path)

                assert len(records) == 1
                assert "MOCK_TRANSCRIPTION_FLOWCHART" in records[0].raw_context
                assert "Before the diagram" in records[0].raw_context
        finally:
            Path(docx_path).unlink(missing_ok=True)
