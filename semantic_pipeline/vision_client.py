"""
GeminiVisionClient — OpenAI-Compatible Vision Payload Builder
==============================================================
Constructs payloads for the gemini-2.5-pro model to triage and
transcribe images extracted from enterprise documents.

Strategy — "Triage & Transcribe":
  • Decorative images (logos, stock photos) → discard
  • Flowcharts, diagrams, graphs           → step-by-step transcription

The `_execute_request()` method is a mock stub.  A downstream
service is responsible for the actual HTTP call to the LLM API.
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

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


class GeminiVisionClient:
    """
    Builds OpenAI-compatible payloads for gemini-2.5-pro and runs
    a triage-then-transcribe pipeline on embedded document images.

    Usage:
        client = GeminiVisionClient()
        text = client.transcribe_image(image_bytes, "Some context")
        if text != "DECORATIVE_DISCARD":
            inject text into the chunk
    """

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
        Triage and transcribe an image.

        Builds the payload via `prepare_payload()` then passes it to
        `_execute_request()` for completion.

        Returns:
            Either "DECORATIVE_DISCARD" or the transcription text.
        """
        payload = self.prepare_payload(image_bytes, surrounding_context)
        result = self._execute_request(payload)
        logger.debug("Vision result (%d chars): %.80s...", len(result), result)
        return result.strip()

    # ── Mock Stub ───────────────────────────────────────────────────

    def _execute_request(self, payload: dict) -> str:
        """
        Stub — returns a mock transcription.

        In production, a downstream service replaces this with an
        actual HTTP call to the Gemini API.
        """
        return "MOCK_TRANSCRIPTION_FLOWCHART: Step 1 -> Step 2"
