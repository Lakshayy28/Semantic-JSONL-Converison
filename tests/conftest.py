"""
Pytest configuration for the test suite.

Sets GEMINI_API_KEY=MOCK at module level so that:
  - load_dotenv() in api.py (called at import time) does NOT override it
    (load_dotenv skips keys already present in os.environ by default)
  - SemanticRouter → GeminiContextClient picks up MOCK and skips HTTP
  - GeminiVisionClient._execute_request picks up MOCK and returns a stub
    without any network call

This keeps every test fast and fully offline — no real API calls.
"""
from __future__ import annotations

import os

# ── Force MOCK key BEFORE test_api.py imports api.py ────────────────────────
# Must be at module level (not inside a fixture) so it executes before
# pytest collects and imports test modules.
# load_dotenv() in api.py will NOT override this because python-dotenv
# skips env vars that are already set in os.environ.
os.environ["GEMINI_API_KEY"] = "MOCK"
