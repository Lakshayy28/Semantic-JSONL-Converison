"""
StructuredParser — OpenAPI/Swagger Semantic Endpoint Chunker
==============================================================
Parses .json and .yaml files with smart detection:

  • **API Specs** (OpenAPI/Swagger): Traverses the `paths` object and
    bundles Method + Path + Summary + Parameters + Request Body +
    Responses into one cohesive chunk per endpoint.

  • **Generic JSON/YAML**: Falls back to RecursiveCharacterTextSplitter
    on the formatted string representation.

Why not generic JSON splitters?
  Deeply nested arrays (parameters, responses) get orphaned from their
  parent route, destroying the semantic relationship between an endpoint
  and its contract. This parser keeps them bundled together.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200

# HTTP methods recognized in OpenAPI path items
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

# Top-level keys that indicate an API specification
API_SPEC_INDICATORS = {"openapi", "swagger"}


class StructuredParser:
    """
    Semantic chunker for JSON and YAML files.

    Automatically detects OpenAPI/Swagger specs and applies
    endpoint-level bundling. Falls back to recursive text splitting
    for generic structured data.

    Usage:
        parser = StructuredParser()
        chunks = parser.parse_file("/path/to/spec.yaml")
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
        )

    # ── Public API ──────────────────────────────────────────────────

    def parse_file(self, file_path: str) -> List[Dict[str, str]]:
        """
        Read a .json or .yaml/.yml file and produce semantic chunks.

        Returns:
            List of dicts with keys: "raw_context", "source_file"
        """
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        ext = path.suffix.lower()

        # Parse into dict
        data = self._load(content, ext)
        if data is None:
            logger.warning("Failed to parse %s — skipping.", path.name)
            return []

        return self._route(data, path.name, ext)

    def parse_text(
        self,
        text: str,
        source_file: str = "unknown.json",
        ext: str = ".json",
    ) -> List[Dict[str, str]]:
        """Parse from a raw text string (useful for testing)."""
        data = self._load(text, ext)
        if data is None:
            return []
        return self._route(data, source_file, ext)

    # ── Internal: Load ──────────────────────────────────────────────

    @staticmethod
    def _load(content: str, ext: str) -> Optional[Any]:
        """Parse JSON or YAML string into a Python object."""
        try:
            if ext == ".json":
                return json.loads(content)
            else:  # .yaml, .yml
                return yaml.safe_load(content)
        except (json.JSONDecodeError, yaml.YAMLError) as e:
            logger.error("Parse error: %s", e)
            return None

    # ── Internal: Route ─────────────────────────────────────────────

    def _route(
        self,
        data: Any,
        source_file: str,
        ext: str,
    ) -> List[Dict[str, str]]:
        """Detect API spec vs generic data and dispatch accordingly."""
        if isinstance(data, dict) and self._is_api_spec(data):
            logger.info(
                "Detected API spec in %s — using endpoint-level chunking.",
                source_file,
            )
            return self._parse_api_spec(data, source_file)
        else:
            logger.info(
                "Generic structured data in %s — using recursive fallback.",
                source_file,
            )
            return self._parse_generic(data, source_file, ext)

    @staticmethod
    def _is_api_spec(data: dict) -> bool:
        """Check for OpenAPI/Swagger top-level keys."""
        return bool(API_SPEC_INDICATORS & set(data.keys()))

    # ── Internal: API Spec Parsing ──────────────────────────────────

    def _parse_api_spec(
        self,
        spec: dict,
        source_file: str,
    ) -> List[Dict[str, str]]:
        """
        Traverse the `paths` object and produce one chunk per endpoint
        (method + path combination).
        """
        # Extract API metadata for context
        info = spec.get("info", {})
        api_title = info.get("title", "Unknown API")
        api_version = info.get("version", "")

        paths = spec.get("paths", {})
        if not paths:
            logger.warning("API spec %s has no paths — skipping.", source_file)
            return []

        chunks: List[Dict[str, str]] = []

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method, operation in path_item.items():
                if method.lower() not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue

                raw_context = self._build_endpoint_context(
                    api_title=api_title,
                    api_version=api_version,
                    path=path,
                    method=method.upper(),
                    operation=operation,
                )

                chunks.append({
                    "raw_context": raw_context,
                    "source_file": source_file,
                })

        logger.info(
            "StructuredParser produced %d endpoint chunks from %s.",
            len(chunks),
            source_file,
        )
        return chunks

    @staticmethod
    def _build_endpoint_context(
        api_title: str,
        api_version: str,
        path: str,
        method: str,
        operation: dict,
    ) -> str:
        """
        Assemble a cohesive raw_context string for a single API endpoint.

        Format:
            API: <title> (v<version>).
            API Endpoint: <METHOD> <path>.
            Summary: <summary>.
            Description: <description>.
            Tags: <tag1>, <tag2>.
            Parameters: <name> (<in>, <type>, required/optional). ...
            Request Body: <description>.
            Responses: <code> (<description>). ...
        """
        parts: List[str] = []

        # API identity
        if api_title:
            version_str = f" (v{api_version})" if api_version else ""
            parts.append(f"API: {api_title}{version_str}.")

        # Endpoint identity
        parts.append(f"API Endpoint: {method} {path}.")

        # Summary
        summary = operation.get("summary", "")
        if summary:
            parts.append(f"Summary: {summary}.")

        # Description
        description = operation.get("description", "")
        if description:
            parts.append(f"Description: {description}.")

        # Tags
        tags = operation.get("tags", [])
        if tags:
            parts.append(f"Tags: {', '.join(tags)}.")

        # Operation ID
        op_id = operation.get("operationId", "")
        if op_id:
            parts.append(f"Operation ID: {op_id}.")

        # Parameters
        params = operation.get("parameters", [])
        if params:
            param_strs = []
            for p in params:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "?")
                location = p.get("in", "?")
                p_type = p.get("schema", {}).get("type", p.get("type", ""))
                required = "required" if p.get("required", False) else "optional"
                param_desc = f"{name} ({location}, {p_type}, {required})" if p_type else f"{name} ({location}, {required})"
                param_strs.append(param_desc)
            parts.append(f"Parameters: {'; '.join(param_strs)}.")

        # Request Body
        request_body = operation.get("requestBody", {})
        if isinstance(request_body, dict):
            rb_desc = request_body.get("description", "")
            rb_required = "required" if request_body.get("required", False) else "optional"
            if rb_desc:
                parts.append(f"Request Body ({rb_required}): {rb_desc}.")
            elif request_body.get("content"):
                content_types = list(request_body["content"].keys())
                parts.append(f"Request Body ({rb_required}): accepts {', '.join(content_types)}.")

        # Responses
        responses = operation.get("responses", {})
        if responses:
            resp_strs = []
            for code, resp in responses.items():
                if isinstance(resp, dict):
                    desc = resp.get("description", "")
                    resp_strs.append(f"{code} ({desc})" if desc else str(code))
                else:
                    resp_strs.append(str(code))
            parts.append(f"Responses: {'; '.join(resp_strs)}.")

        return " ".join(parts)

    # ── Internal: Generic Fallback ──────────────────────────────────

    def _parse_generic(
        self,
        data: Any,
        source_file: str,
        ext: str,
    ) -> List[Dict[str, str]]:
        """
        For non-API JSON/YAML files, dump to a formatted string and
        split using RecursiveCharacterTextSplitter.
        """
        # Serialize to human-readable form
        if ext == ".json":
            text = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            text = yaml.dump(data, default_flow_style=False, allow_unicode=True)

        if not text.strip():
            return []

        # Split if needed
        if len(text) > self._chunk_size:
            sub_texts = self._recursive_splitter.split_text(text)
            logger.info(
                "Split generic %s into %d chunks.", source_file, len(sub_texts)
            )
        else:
            sub_texts = [text]

        return [
            {"raw_context": t.strip(), "source_file": source_file}
            for t in sub_texts
            if t.strip()
        ]
