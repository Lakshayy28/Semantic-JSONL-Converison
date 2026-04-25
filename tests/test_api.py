"""
Core API Test Suite
===================
Tests the important functionalities only:
  - Health endpoint
  - /convert endpoint (chunked and unchunked)
  - /convert/batch endpoint
  - Unsupported file type rejection
  - Vision client payload structure and graceful degradation
  - Context client (Anthropic contextual chunking pattern) payload and fallback
  - LLM connectivity (mock path) for both clients
  - Chunked vs unchunked output difference
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from unittest.mock import MagicMock, patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from api import app
from semantic_pipeline.context_client import (
    GeminiContextClient,
    FALLBACK_STRING,
    MOCK_CONTEXT,
)
from semantic_pipeline.vision_client import (
    GeminiVisionClient,
    DECORATIVE_MARKER,
    FALLBACK_PREFIX,
)

client = TestClient(app)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _minimal_markdown() -> bytes:
    return b"# Title\n\nSome body content for testing purposes.\n"


def _minimal_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Value"])
    ws.append(["Alpha", "100"])
    ws.append(["Beta", "200"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _minimal_png() -> bytes:
    # 1×1 pixel red PNG (valid minimal PNG, 67 bytes)
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
        b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ─── 1. Health Endpoint ──────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_status_field(self):
        body = client.get("/health").json()
        assert body["status"] == "healthy"

    def test_health_lists_supported_extensions(self):
        body = client.get("/health").json()
        exts = body["supported_extensions"]
        assert ".xlsx" in exts
        assert ".docx" in exts
        assert ".md" in exts


# ─── 2. /convert Endpoint ───────────────────────────────────────────────────

class TestConvertEndpoint:
    def test_convert_markdown_chunked_returns_jsonl(self):
        resp = client.post(
            "/convert",
            files={"file": ("test.md", _minimal_markdown(), "text/markdown")},
        )
        assert resp.status_code == 200
        assert "application/x-ndjson" in resp.headers["content-type"]
        # Each line must be valid JSON
        for line in resp.content.decode().strip().splitlines():
            obj = json.loads(line)
            assert "raw_context" in obj

    def test_convert_xlsx_chunked_returns_jsonl(self):
        resp = client.post(
            "/convert",
            files={"file": ("data.xlsx", _minimal_xlsx(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        lines = resp.content.decode().strip().splitlines()
        assert len(lines) >= 1

    def test_convert_unsupported_type_returns_400(self):
        resp = client.post(
            "/convert",
            files={"file": ("photo.png", b"fake-data", "image/png")},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_convert_returns_x_chunks_produced_header(self):
        resp = client.post(
            "/convert",
            files={"file": ("test.md", _minimal_markdown(), "text/markdown")},
        )
        assert "x-chunks-produced" in resp.headers

    def test_convert_content_disposition_filename(self):
        resp = client.post(
            "/convert",
            files={"file": ("report.md", _minimal_markdown(), "text/markdown")},
        )
        assert "report.jsonl" in resp.headers.get("content-disposition", "")


# ─── 3. Chunked vs Unchunked ────────────────────────────────────────────────

class TestChunkedVsUnchunked:
    def test_unchunked_markdown_returns_single_record(self):
        resp = client.post(
            "/convert?chunked=false",
            files={"file": ("doc.md", _minimal_markdown(), "text/markdown")},
        )
        assert resp.status_code == 200
        lines = resp.content.decode().strip().splitlines()
        assert len(lines) == 1

    def test_chunked_md_may_return_multiple_records(self):
        # A multi-section doc should produce at least 1 chunk
        md = b"# Intro\n\nIntro body.\n\n# Details\n\nDetails body.\n"
        resp = client.post(
            "/convert?chunked=true",
            files={"file": ("multi.md", md, "text/markdown")},
        )
        assert resp.status_code == 200
        lines = resp.content.decode().strip().splitlines()
        assert len(lines) >= 1

    def test_unchunked_filename_suffix(self):
        resp = client.post(
            "/convert?chunked=false",
            files={"file": ("report.md", _minimal_markdown(), "text/markdown")},
        )
        assert "unchunked" in resp.headers.get("content-disposition", "")

    def test_chunked_filename_no_unchunked_suffix(self):
        resp = client.post(
            "/convert?chunked=true",
            files={"file": ("report.md", _minimal_markdown(), "text/markdown")},
        )
        assert "unchunked" not in resp.headers.get("content-disposition", "")

    def test_unchunked_xlsx_single_record(self):
        resp = client.post(
            "/convert?chunked=false",
            files={"file": ("data.xlsx", _minimal_xlsx(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert resp.status_code == 200
        lines = resp.content.decode().strip().splitlines()
        assert len(lines) == 1


# ─── 4. /convert/batch Endpoint ─────────────────────────────────────────────

class TestBatchEndpoint:
    def test_batch_returns_zip(self):
        resp = client.post(
            "/convert/batch",
            files=[
                ("files", ("a.md", _minimal_markdown(), "text/markdown")),
                ("files", ("b.md", _minimal_markdown(), "text/markdown")),
            ],
        )
        assert resp.status_code == 200
        assert "zip" in resp.headers["content-type"]

    def test_batch_zip_contains_jsonl_per_file(self):
        resp = client.post(
            "/convert/batch",
            files=[
                ("files", ("first.md", _minimal_markdown(), "text/markdown")),
                ("files", ("second.md", _minimal_markdown(), "text/markdown")),
            ],
        )
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "first.jsonl" in names
        assert "second.jsonl" in names

    def test_batch_skips_unsupported_files(self):
        resp = client.post(
            "/convert/batch",
            files=[
                ("files", ("ok.md", _minimal_markdown(), "text/markdown")),
                ("files", ("bad.png", b"data", "image/png")),
            ],
        )
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "ok.jsonl" in names
        assert not any("png" in n for n in names)

    def test_batch_mixed_formats(self):
        resp = client.post(
            "/convert/batch",
            files=[
                ("files", ("doc.md", _minimal_markdown(), "text/markdown")),
                ("files", ("sheet.xlsx", _minimal_xlsx(),
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ],
        )
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "doc.jsonl" in names
        assert "sheet.jsonl" in names


# ─── 5. Vision Client ────────────────────────────────────────────────────────

class TestVisionClient:
    def test_payload_has_messages_key(self):
        vc = GeminiVisionClient()
        payload = vc.prepare_payload(_minimal_png())
        assert "messages" in payload

    def test_payload_includes_base64_image(self):
        vc = GeminiVisionClient()
        payload = vc.prepare_payload(_minimal_png())
        # Find the user message that contains image_url
        image_found = False
        for msg in payload["messages"]:
            if isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if item.get("type") == "image_url":
                        image_found = True
        assert image_found

    def test_payload_has_system_prompt(self):
        vc = GeminiVisionClient()
        payload = vc.prepare_payload(_minimal_png())
        system_msgs = [m for m in payload["messages"] if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert len(system_msgs[0]["content"]) > 10

    def test_mock_key_returns_mock_transcription(self):
        vc = GeminiVisionClient()
        # _execute_request returns mock when api_key is MOCK
        result = vc.transcribe_image(_minimal_png())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_decorative_marker_constant_is_stable(self):
        assert DECORATIVE_MARKER == "DECORATIVE_DISCARD"

    def test_graceful_degradation_on_failure(self):
        vc = GeminiVisionClient(max_retries=1)
        vc._execute_request = MagicMock(side_effect=RuntimeError("API down"))
        result = vc.transcribe_image(_minimal_png())
        assert FALLBACK_PREFIX in result

    def test_no_exception_raised_to_caller_on_failure(self):
        vc = GeminiVisionClient(max_retries=1)
        vc._execute_request = MagicMock(side_effect=ConnectionError("network"))
        # Must not raise
        result = vc.transcribe_image(_minimal_png())
        assert isinstance(result, str)


# ─── 6. Context Client (Anthropic Contextual Chunking Pattern) ──────────────

class TestContextClient:
    def test_mock_key_returns_mock_context_string(self):
        ctx = GeminiContextClient(api_key="MOCK")
        result = ctx.generate_global_context("Some document text here.")
        assert result == MOCK_CONTEXT

    def test_empty_document_returns_fallback(self):
        ctx = GeminiContextClient(api_key="MOCK")
        result = ctx.generate_global_context("")
        assert result == FALLBACK_STRING

    def test_whitespace_only_document_returns_fallback(self):
        ctx = GeminiContextClient(api_key="MOCK")
        result = ctx.generate_global_context("   \n  ")
        assert result == FALLBACK_STRING

    def test_payload_has_system_prompt(self):
        ctx = GeminiContextClient()
        payload = ctx.prepare_payload("sample document text")
        system_msgs = [m for m in payload["messages"] if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert len(system_msgs[0]["content"]) > 10

    def test_payload_includes_document_text_as_user_message(self):
        ctx = GeminiContextClient()
        doc = "This is the full document content."
        payload = ctx.prepare_payload(doc)
        user_msgs = [m for m in payload["messages"] if m["role"] == "user"]
        assert any(doc in m["content"] for m in user_msgs)

    def test_graceful_degradation_on_api_failure(self):
        ctx = GeminiContextClient(api_key="REAL_BUT_FAKE", max_retries=1)
        ctx._execute_request = MagicMock(side_effect=RuntimeError("500 error"))
        result = ctx.generate_global_context("some text")
        assert result == FALLBACK_STRING

    def test_no_exception_raised_to_caller_on_failure(self):
        ctx = GeminiContextClient(api_key="REAL_BUT_FAKE", max_retries=1)
        ctx._execute_request = MagicMock(side_effect=ConnectionError("down"))
        result = ctx.generate_global_context("some text")
        assert isinstance(result, str)

    def test_default_timeout_is_reasonable(self):
        ctx = GeminiContextClient()
        assert ctx.timeout >= 30

    def test_default_max_retries(self):
        ctx = GeminiContextClient()
        assert ctx.max_retries >= 1


# ─── 7. LLM Connectivity (Mock Path) ────────────────────────────────────────

class TestLLMConnectivity:
    def test_context_client_mock_does_not_call_network(self):
        """With api_key=MOCK no HTTP call should be made."""
        ctx = GeminiContextClient(api_key="MOCK")
        with patch("httpx.Client.post") as mock_post:
            ctx.generate_global_context("doc text")
            mock_post.assert_not_called()

    def test_vision_client_mock_does_not_call_network(self):
        """Vision client with MOCK key should not hit the network."""
        # GEMINI_API_KEY is globally set to MOCK by conftest.py,
        # so _execute_request returns early without calling httpx.
        vc = GeminiVisionClient()
        with patch("httpx.Client.post") as mock_post:
            vc.transcribe_image(_minimal_png())
            mock_post.assert_not_called()

    def test_context_client_real_key_calls_network(self):
        """With a real (but invalid) key the client should attempt an HTTP call."""
        ctx = GeminiContextClient(api_key="FAKE_REAL_KEY", max_retries=1)
        with patch("httpx.Client.post", side_effect=ConnectionError("blocked")) as mock_post:
            ctx.generate_global_context("doc text")
            mock_post.assert_called_once()

    def test_vision_client_payload_top_p(self):
        vc = GeminiVisionClient()
        payload = vc.prepare_payload(_minimal_png())
        assert payload.get("top_p") == 1

    def test_context_client_payload_top_p(self):
        ctx = GeminiContextClient()
        payload = ctx.prepare_payload("text")
        assert payload.get("top_p") == 1
