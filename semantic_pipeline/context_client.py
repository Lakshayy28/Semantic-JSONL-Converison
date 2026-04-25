"""
GeminiContextClient — Contextual Chunking via Global Document Summary
======================================================================
Implements the Anthropic "Contextual Chunking" pattern:  before a
document is split into chunks we extract its full text, pass it to
``gemini-2.5-pro`` to generate a 3-5 sentence global summary, and
then prepend that summary to **every** chunk so that vector retrieval
always has the document's global context.

Resilience (mirrors GeminiVisionClient):
  • 60-second timeout for complex documents.
  • Exponential backoff (tenacity) with max 3 retries.
  • Graceful degradation — returns ``[GLOBAL_CONTEXT_FAILED]`` on
    exhausted retries so the pipeline never crashes.

The ``_execute_request()`` method is a mock stub.  When
``GEMINI_API_KEY`` (env var or constructor arg) is ``"MOCK"``,
a deterministic mock string is returned.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    RetryError,
)

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────
CONTEXT_SYSTEM_PROMPT = (
    "You are an expert AI data architect. Read the following enterprise "
    "document and provide a concise, high-level summary (3-5 sentences) "
    "of its overall purpose, main entities, and global context. Do not "
    "include introductory filler. This summary will be prepended to "
    "smaller chunks of the document to give them context during vector "
    "retrieval."
)

# ── Constants ─────────────────────────────────────────────────────
FALLBACK_STRING = "[GLOBAL_CONTEXT_FAILED]"
MOCK_CONTEXT = (
    "MOCK_GLOBAL_CONTEXT: This document contains business rules and "
    "architectural guidelines for the core system."
)

DEFAULT_TIMEOUT_SECONDS = 60
MAX_RETRY_ATTEMPTS = 3
BACKOFF_MIN_SECONDS = 2
BACKOFF_MAX_SECONDS = 10


class GeminiContextClient:
    """
    Generates a global document summary using ``gemini-2.5-pro``
    via the Anthropic "Contextual Chunking" pattern.

    Usage::

        client = GeminiContextClient()
        summary = client.generate_global_context(full_doc_text)
        # summary is prepended to every chunk's raw_context
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRY_ATTEMPTS,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "MOCK")
        self.timeout = timeout
        self.max_retries = max_retries

    # ── Public API ──────────────────────────────────────────────────

    def prepare_payload(self, full_document_text: str) -> dict:
        """
        Build an OpenAI-compatible chat-completion request body for
        ``gemini-2.5-pro``.

        Args:
            full_document_text: The complete extracted text of the document.

        Returns:
            Dict matching the OpenAI chat-completion schema.
        """
        return {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": CONTEXT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": full_document_text,
                },
            ],
            "top_p": 1,
        }

    def generate_global_context(self, full_document_text: str) -> str:
        """
        Generate a global context summary for the document.

        If ``api_key`` is ``"MOCK"`` the deterministic mock string is
        returned directly (no network call).

        Returns:
            Summary string, or ``[GLOBAL_CONTEXT_FAILED]`` on error.
        """
        if not full_document_text or not full_document_text.strip():
            logger.warning("Empty document text — returning fallback.")
            return FALLBACK_STRING

        # ── Mock path ──────────────────────────────────────────────
        if self.api_key == "MOCK":
            logger.debug("Using mock global context (GEMINI_API_KEY=MOCK).")
            return MOCK_CONTEXT

        # ── Real path with retry ───────────────────────────────────
        payload = self.prepare_payload(full_document_text)

        try:
            result = self._execute_with_retry(payload)
            logger.debug(
                "Context result (%d chars): %.80s...", len(result), result
            )
            return result.strip()

        except RetryError as e:
            underlying = e.last_attempt.exception() if e.last_attempt else e
            logger.warning(
                "Context API failed after %d retries: %s",
                self.max_retries,
                underlying,
            )
            return FALLBACK_STRING

        except Exception as e:
            logger.warning("Context API unexpected error: %s", e)
            return FALLBACK_STRING

    # ── Retry-Wrapped Execution ─────────────────────────────────────

    def _execute_with_retry(self, payload: dict) -> str:
        """
        Call ``_execute_request`` with tenacity retry.
        """

        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(
                multiplier=1,
                min=BACKOFF_MIN_SECONDS,
                max=BACKOFF_MAX_SECONDS,
            ),
            reraise=True,
        )
        def _inner():
            return self._execute_request(payload)

        return _inner()

    # ── Mock Stub ───────────────────────────────────────────────────

    def _execute_request(self, payload: dict) -> str:
        """
        Executes a real gemini-2.5-pro HTTP request.
        """
        if self.api_key == "MOCK":
            return MOCK_CONTEXT

        import httpx

        # Map OpenAI payload schema to Gemini generateContent schema
        gemini_payload = {"contents": []}
        for msg in payload.get("messages", []):
            if msg["role"] == "system":
                gemini_payload["systemInstruction"] = {"parts": [{"text": msg["content"]}]}
            elif msg["role"] == "user":
                gemini_payload["contents"].append({"parts": [{"text": msg["content"]}]})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{payload['model']}:generateContent?key={self.api_key}"

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=gemini_payload)
            resp.raise_for_status()
            data = resp.json()
            
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return FALLBACK_STRING

