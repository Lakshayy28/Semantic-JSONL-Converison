"""
GeminiVisionClient — OpenAI-Compatible Vision Payload Builder
==============================================================
Constructs payloads for the gemini-2.5-pro model to triage and
transcribe images extracted from enterprise documents.

Strategy — "Triage & Transcribe":
  • Decorative images (logos, stock photos) → discard
  • Flowcharts, diagrams, graphs           → step-by-step transcription

Resilience:
  • Explicit 60-second timeout on the HTTP execution layer so the
    pipeline waits patiently for complex diagram analysis.
  • Exponential backoff (via tenacity) retries up to 3 attempts on
    429 / 503 / Timeout / ConnectionError.
  • Graceful degradation: if all retries are exhausted the pipeline
    returns a stable fallback string and does NOT crash.

The `_execute_request()` method is a mock stub.  A downstream
service is responsible for the actual HTTP call to the LLM API.
"""

from __future__ import annotations

import base64
import logging


from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    RetryError,
)

logger = logging.getLogger(__name__)

# The system prompt instructs the LLM to triage the image first.
SYSTEM_PROMPT = (
    "You are an expert systems architect analyzing an image extracted "
    "from an enterprise document. "
    "1. If this image is a decorative graphic, logo, or stock photo, "
    "reply EXACTLY with the word 'DECORATIVE_DISCARD'. "
    "2. If this image is a flowchart, system diagram, or graph, "
    "carefully transcribe the logical flow step-by-step. "
    "3. Format your response as clean text."
)

DECORATIVE_MARKER = "DECORATIVE_DISCARD"

# Stable prefix — downstream consumers can match on this.
FALLBACK_PREFIX = "[IMAGE_TRANSCRIPTION_FAILED: API Error]"

# ── Timeout & Retry Defaults ──────────────────────────────────────
DEFAULT_TIMEOUT_SECONDS = 60
MAX_RETRY_ATTEMPTS = 3
BACKOFF_MIN_SECONDS = 2    # first retry waits ~2 s
BACKOFF_MAX_SECONDS = 10   # cap exponential growth


class GeminiVisionClient:
    """
    Builds OpenAI-compatible payloads for gemini-2.5-pro and runs
    a triage-then-transcribe pipeline on embedded document images.

    Resilience features:
      • 60-second fulfillment timeout (configurable)
      • Exponential backoff retry (3 attempts max)
      • Graceful degradation on exhausted retries

    Usage:
        client = GeminiVisionClient()
        text = client.transcribe_image(image_bytes, "Some context")
        if text != "DECORATIVE_DISCARD":
            inject text into the chunk
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRY_ATTEMPTS,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries

    # ── Public API ──────────────────────────────────────────────────

    def prepare_payload(
        self,
        image_bytes: bytes,
        surrounding_context: str = "",
    ) -> dict:
        """
        Build an OpenAI-compatible chat-completion request body.

        Args:
            image_bytes:        Raw bytes of the image (JPEG/PNG).
            surrounding_context: Text from the document near the image.

        Returns:
            Dict matching the OpenAI chat-completion schema with
            base64-encoded image inlined.
        """
        base64_string = base64.b64encode(image_bytes).decode("utf-8")

        return {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"The surrounding text context is: {surrounding_context}",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_string}"
                            },
                        }
                    ],
                },
            ],
            "top_p": 1,
        }

    def transcribe_image(
        self,
        image_bytes: bytes,
        surrounding_context: str = "",
    ) -> str:
        """
        Triage and transcribe an image with full resilience.

        Builds the payload via ``prepare_payload()`` then passes it to
        ``_execute_with_retry()`` which wraps ``_execute_request()``
        with exponential backoff.

        If all retries are exhausted or a hard error occurs the method
        returns a stable fallback string — it **never** raises to the
        caller.

        Returns:
            Transcription text, "DECORATIVE_DISCARD", or the
            ``[IMAGE_TRANSCRIPTION_FAILED: ...]`` fallback.
        """
        payload = self.prepare_payload(image_bytes, surrounding_context)

        try:
            result = self._execute_with_retry(payload)
            logger.debug(
                "Vision result (%d chars): %.80s...", len(result), result
            )
            return result.strip()

        except RetryError as e:
            # All retry attempts exhausted
            underlying = e.last_attempt.exception() if e.last_attempt else e
            logger.warning(
                "Vision API failed after %d retries: %s",
                self.max_retries,
                underlying,
            )
            return f"{FALLBACK_PREFIX} {underlying}"

        except Exception as e:
            # Any other unexpected error — hard timeout, connection
            # reset, malformed response, etc.
            logger.warning("Vision API unexpected error: %s", e)
            return f"{FALLBACK_PREFIX} {e}"

    # ── Retry-Wrapped Execution ─────────────────────────────────────

    def _execute_with_retry(self, payload: dict) -> str:
        """
        Call ``_execute_request`` with tenacity retry.

        The decorator is applied dynamically so that ``max_retries``
        and backoff parameters from ``__init__`` are honoured and so
        that tests can freely monkey-patch ``_execute_request``.
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

    # ── Real Execution ───────────────────────────────────────────────────

    def _execute_request(self, payload: dict) -> str:
        """
        Executes a real gemini-2.5-pro HTTP request via httpx.
        Converts the OpenAI-style multimodal payload to native Gemini format.
        """
        import os
        import httpx

        # We fall back to os.environ safely since api_key might not be in __init__
        api_key = getattr(self, "api_key", os.environ.get("GEMINI_API_KEY", "MOCK"))

        if api_key == "MOCK":
            return "MOCK_TRANSCRIPTION_FLOWCHART: Step 1 -> Step 2"

        # Translate OpenAI multimodal payload to Gemini API
        gemini_payload = {"contents": []}
        
        for msg in payload.get("messages", []):
            if msg["role"] == "system":
                gemini_payload["systemInstruction"] = {"parts": [{"text": msg["content"]}]}
            elif msg["role"] == "user":
                content = msg["content"]
                if isinstance(content, str):
                    gemini_payload["contents"].append({"parts": [{"text": content}]})
                elif isinstance(content, list):
                    # Handle image/text arrays
                    parts = []
                    for item in content:
                        if item.get("type") == "image_url":
                            # Parse "data:image/jpeg;base64,ABC..."
                            data_uri = item["image_url"]["url"]
                            header, b64_data = data_uri.split(",", 1)
                            mime_type = header.split(":", 1)[1].split(";")[0]
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data
                                }
                            })
                        elif item.get("type") == "text":
                            parts.append({"text": item.get("text", "")})
                    
                    if parts:
                        gemini_payload["contents"].append({"parts": parts})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{payload['model']}:generateContent?key={api_key}"

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=gemini_payload)
            resp.raise_for_status()
            data = resp.json()
            
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return FALLBACK_PREFIX
