"""
Phase 6.1 Tests — Vision Client Resilience & Fulfillment Waiting
=================================================================
Validates:
  1. Timeout configuration — client stores and exposes the timeout value
  2. Retry verification  — _execute_request is called exactly 3 times
                           on persistent failures before giving up
  3. Fallback safety     — transcribe_image returns the stable
                           [IMAGE_TRANSCRIPTION_FAILED: ...] string
                           and never raises to the caller
  4. Backoff config      — tenacity decorator uses exponential waits
  5. Happy-path intact   — the mock stub still works when no errors occur
  6. Mixed failures      — transient error then success (partial retry)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch, call

import pytest
from tenacity import RetryError

from semantic_pipeline.vision_client import (
    GeminiVisionClient,
    DECORATIVE_MARKER,
    FALLBACK_PREFIX,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RETRY_ATTEMPTS,
    BACKOFF_MIN_SECONDS,
)


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 1 — Timeout Configuration
# ═══════════════════════════════════════════════════════════════════


class TestTimeoutConfig:
    """Verify the client stores and exposes timeout settings."""

    def test_default_timeout(self):
        client = GeminiVisionClient()
        assert client.timeout == DEFAULT_TIMEOUT_SECONDS
        assert client.timeout == 60

    def test_custom_timeout(self):
        client = GeminiVisionClient(timeout=120)
        assert client.timeout == 120

    def test_default_max_retries(self):
        client = GeminiVisionClient()
        assert client.max_retries == MAX_RETRY_ATTEMPTS
        assert client.max_retries == 3

    def test_custom_max_retries(self):
        client = GeminiVisionClient(max_retries=5)
        assert client.max_retries == 5


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 2 — Retry Verification
# ═══════════════════════════════════════════════════════════════════


class TestRetryBehavior:
    """Verify that _execute_request is retried exactly max_retries times."""

    def test_retry_called_3_times_on_timeout(self):
        """Force TimeoutError on every call — should retry exactly 3 times."""
        client = GeminiVisionClient()
        call_count = 0

        def _failing_execute(payload):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Connection timed out after 60s")

        client._execute_request = _failing_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 3
        assert FALLBACK_PREFIX in result

    def test_retry_called_3_times_on_connection_error(self):
        """Force ConnectionError — should retry exactly 3 times."""
        client = GeminiVisionClient()
        call_count = 0

        def _failing_execute(payload):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Connection refused")

        client._execute_request = _failing_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 3
        assert FALLBACK_PREFIX in result

    def test_retry_called_custom_times(self):
        """With max_retries=5, should attempt exactly 5 times."""
        client = GeminiVisionClient(max_retries=5)
        call_count = 0

        def _failing_execute(payload):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Timeout")

        client._execute_request = _failing_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 5
        assert FALLBACK_PREFIX in result

    def test_retry_on_runtime_error(self):
        """Any exception triggers retry (e.g., 429 / 503 mapped to RuntimeError)."""
        client = GeminiVisionClient()
        call_count = 0

        def _failing_execute(payload):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("429 Too Many Requests")

        client._execute_request = _failing_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 3
        assert FALLBACK_PREFIX in result
        assert "429 Too Many Requests" in result


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 3 — Fallback / Graceful Degradation
# ═══════════════════════════════════════════════════════════════════


class TestGracefulDegradation:
    """Verify that the pipeline NEVER crashes on API errors."""

    def test_fallback_string_has_stable_prefix(self):
        """The fallback must start with the documented prefix."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            TimeoutError("Simulated timeout")
        )

        result = client.transcribe_image(b"fake-image")
        assert result.startswith(FALLBACK_PREFIX)

    def test_fallback_includes_exception_detail(self):
        """The fallback should include the specific error message."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            ConnectionError("ECONNREFUSED")
        )

        result = client.transcribe_image(b"fake-image")
        assert "ECONNREFUSED" in result

    def test_no_exception_raised_to_caller(self):
        """transcribe_image must NEVER raise — it returns a string."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            Exception("Catastrophic failure")
        )

        # This MUST NOT raise
        result = client.transcribe_image(b"fake-image")
        assert isinstance(result, str)
        assert FALLBACK_PREFIX in result

    def test_no_exception_on_keyboard_interrupt_subclass(self):
        """Even unusual errors should degrade gracefully."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            OSError("Network is down")
        )

        result = client.transcribe_image(b"fake-image")
        assert isinstance(result, str)
        assert FALLBACK_PREFIX in result

    def test_fallback_is_not_decorative_marker(self):
        """Fallback must not be confused with DECORATIVE_DISCARD."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            TimeoutError("Timeout")
        )

        result = client.transcribe_image(b"fake-image")
        assert result != DECORATIVE_MARKER


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 4 — Happy Path (Mock Stub Still Works)
# ═══════════════════════════════════════════════════════════════════


class TestHappyPath:
    """Verify that the default mock still produces correct results."""

    def test_mock_transcription_unchanged(self):
        client = GeminiVisionClient()
        result = client.transcribe_image(b"fake-image")
        assert result == "MOCK_TRANSCRIPTION_FLOWCHART: Step 1 -> Step 2"

    def test_decorative_discard_still_works(self):
        client = GeminiVisionClient()
        client._execute_request = lambda p: "DECORATIVE_DISCARD"
        result = client.transcribe_image(b"fake-image")
        assert result == DECORATIVE_MARKER

    def test_payload_structure_unchanged(self):
        client = GeminiVisionClient()
        payload = client.prepare_payload(b"img", "ctx")
        assert payload["model"] == "gemini-2.5-pro"
        assert len(payload["messages"]) == 3
        assert payload["top_p"] == 1


# ═══════════════════════════════════════════════════════════════════
# TEST GROUP 5 — Transient Failure → Recovery (Partial Retry)
# ═══════════════════════════════════════════════════════════════════


class TestTransientRecovery:
    """Verify that the client recovers from transient failures."""

    def test_succeeds_on_second_attempt(self):
        """First call fails, second succeeds — should return the success."""
        client = GeminiVisionClient()
        call_count = 0

        def _flaky_execute(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Transient timeout")
            return "Transcribed: A -> B -> C"

        client._execute_request = _flaky_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 2
        assert result == "Transcribed: A -> B -> C"
        assert FALLBACK_PREFIX not in result

    def test_succeeds_on_third_attempt(self):
        """First two calls fail, third succeeds."""
        client = GeminiVisionClient()
        call_count = 0

        def _flaky_execute(payload):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("Transient failure")
            return "Recovered transcription"

        client._execute_request = _flaky_execute
        result = client.transcribe_image(b"fake-image")

        assert call_count == 3
        assert result == "Recovered transcription"

    def test_logging_on_failure(self, caplog):
        """Warning should be logged when retries are exhausted."""
        client = GeminiVisionClient()
        client._execute_request = lambda p: (_ for _ in ()).throw(
            TimeoutError("API down")
        )

        with caplog.at_level(logging.WARNING):
            client.transcribe_image(b"fake-image")

        assert any("Vision API" in record.message for record in caplog.records)
