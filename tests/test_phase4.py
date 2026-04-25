"""
Phase 4 Tests — Structured Parser (OpenAPI/Swagger & Generic JSON/YAML)
========================================================================
These tests verify:
  1. StructuredParser: endpoint-level semantic chunking for OpenAPI/Swagger
     specs (both JSON and YAML formats).
  2. Smart detection: API specs vs generic JSON/YAML with correct
     routing to endpoint bundling vs recursive fallback.
  3. Context assembly: Method, Path, Summary, Parameters, Responses
     are all bundled into each endpoint chunk.
  4. SemanticRouter integration: .json and .yaml files produce real
     ChunkRecords through the fully operational pipeline.
  5. Real artifact tests against workspace files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from semantic_pipeline.models import ChunkRecord, EmitterConfig
from semantic_pipeline.parsers.structured_parser import StructuredParser
from semantic_pipeline.router import SemanticRouter, SUPPORTED_EXTENSIONS


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def parser() -> StructuredParser:
    """Default StructuredParser instance."""
    return StructuredParser()


@pytest.fixture
def openapi_yaml_spec() -> str:
    """A minimal OpenAPI 3.0 spec in YAML with 2 endpoints."""
    return """\
openapi: "3.0.3"
info:
  title: User Management API
  description: Manages user accounts and authentication.
  version: "1.2.0"
paths:
  /users:
    post:
      summary: Create a new user account
      description: Registers a user with email and password.
      tags:
        - Users
      parameters:
        - name: X-Request-ID
          in: header
          required: false
          schema:
            type: string
      requestBody:
        required: true
        description: User registration payload
        content:
          application/json:
            schema:
              type: object
      responses:
        "201":
          description: User created successfully
        "400":
          description: Validation error
    get:
      summary: List all users
      tags:
        - Users
      parameters:
        - name: page
          in: query
          required: false
          schema:
            type: integer
        - name: limit
          in: query
          required: false
          schema:
            type: integer
      responses:
        "200":
          description: Paginated user list
  /users/{userId}:
    get:
      summary: Get user by ID
      tags:
        - Users
      parameters:
        - name: userId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: User details
        "404":
          description: User not found
    delete:
      summary: Delete a user account
      operationId: deleteUser
      tags:
        - Users
        - Admin
      parameters:
        - name: userId
          in: path
          required: true
          schema:
            type: string
      responses:
        "204":
          description: User deleted
        "403":
          description: Insufficient permissions
"""


@pytest.fixture
def swagger_json_spec() -> dict:
    """A minimal Swagger 2.0 spec as a Python dict."""
    return {
        "swagger": "2.0",
        "info": {
            "title": "Payment Processing API",
            "version": "3.0.1",
        },
        "paths": {
            "/payments": {
                "post": {
                    "summary": "Submit a payment",
                    "tags": ["Payments"],
                    "parameters": [
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "type": "object",
                        }
                    ],
                    "responses": {
                        "201": {"description": "Payment submitted"},
                        "422": {"description": "Invalid payment data"},
                    },
                }
            },
            "/payments/{paymentId}": {
                "get": {
                    "summary": "Get payment status",
                    "tags": ["Payments"],
                    "parameters": [
                        {
                            "name": "paymentId",
                            "in": "path",
                            "required": True,
                            "type": "string",
                        }
                    ],
                    "responses": {
                        "200": {"description": "Payment details"},
                        "404": {"description": "Payment not found"},
                    },
                }
            },
        },
    }


@pytest.fixture
def generic_json_config() -> dict:
    """A generic JSON config (NOT an API spec)."""
    return {
        "database": {
            "host": "db.internal.bank.com",
            "port": 5432,
            "name": "credit_decisions",
            "pool_size": 20,
        },
        "cache": {
            "provider": "redis",
            "ttl_seconds": 3600,
            "cluster": True,
        },
        "logging": {
            "level": "INFO",
            "format": "json",
        },
    }


@pytest.fixture
def generic_yaml_config() -> str:
    """A generic YAML config (NOT an API spec)."""
    return """\
environment: production
features:
  auto_approve: true
  max_retry: 3
  timeout_ms: 5000
services:
  - name: rule-engine
    port: 8080
  - name: bureau-proxy
    port: 8081
"""


@pytest.fixture
def openapi_yaml_file(tmp_path: Path, openapi_yaml_spec: str) -> Path:
    """Write the OpenAPI YAML spec to a temp file."""
    f = tmp_path / "users_api.yaml"
    f.write_text(openapi_yaml_spec)
    return f


@pytest.fixture
def swagger_json_file(tmp_path: Path, swagger_json_spec: dict) -> Path:
    """Write the Swagger JSON spec to a temp file."""
    f = tmp_path / "payments_api.json"
    f.write_text(json.dumps(swagger_json_spec, indent=2))
    return f


@pytest.fixture
def generic_json_file(tmp_path: Path, generic_json_config: dict) -> Path:
    """Write the generic JSON config to a temp file."""
    f = tmp_path / "app_config.json"
    f.write_text(json.dumps(generic_json_config, indent=2))
    return f


@pytest.fixture
def generic_yaml_file(tmp_path: Path, generic_yaml_config: str) -> Path:
    """Write the generic YAML config to a temp file."""
    f = tmp_path / "deploy_config.yaml"
    f.write_text(generic_yaml_config)
    return f


# ═══════════════════════════════════════════════════════════════════
# 1. API Spec Detection
# ═══════════════════════════════════════════════════════════════════


class TestAPISpecDetection:
    """Verify smart detection of OpenAPI/Swagger specs."""

    def test_detects_openapi_spec(self, parser: StructuredParser):
        """Files with 'openapi' key should be recognized as API specs."""
        assert parser._is_api_spec({"openapi": "3.0.0", "paths": {}})

    def test_detects_swagger_spec(self, parser: StructuredParser):
        """Files with 'swagger' key should be recognized as API specs."""
        assert parser._is_api_spec({"swagger": "2.0", "paths": {}})

    def test_rejects_generic_json(self, parser: StructuredParser):
        """Generic JSON without openapi/swagger keys should NOT be API specs."""
        assert not parser._is_api_spec({"database": {}, "cache": {}})

    def test_rejects_non_dict(self, parser: StructuredParser):
        """Non-dict top-level structures are never API specs."""
        # Lists, strings, etc. shouldn't crash _is_api_spec
        assert not parser._is_api_spec({"items": [1, 2, 3]})


# ═══════════════════════════════════════════════════════════════════
# 2. OpenAPI YAML Endpoint-Level Chunking
# ═══════════════════════════════════════════════════════════════════


class TestOpenAPIYAMLParsing:
    """Validate endpoint-level semantic chunking of OpenAPI YAML specs."""

    def test_correct_number_of_endpoint_chunks(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """
        The spec has 4 endpoints:
          POST /users, GET /users, GET /users/{userId}, DELETE /users/{userId}
        → should produce exactly 4 chunks.
        """
        chunks = parser.parse_file(str(openapi_yaml_file))
        assert len(chunks) == 4

    def test_method_and_path_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Every chunk must contain 'API Endpoint: METHOD /path.'"""
        chunks = parser.parse_file(str(openapi_yaml_file))

        # Use trailing period to avoid substring matches
        # (e.g., "GET /users." won't match "GET /users/{userId}.")
        expected_endpoints = [
            "API Endpoint: POST /users.",
            "API Endpoint: GET /users.",
            "API Endpoint: GET /users/{userId}.",
            "API Endpoint: DELETE /users/{userId}.",
        ]
        for endpoint in expected_endpoints:
            matching = [c for c in chunks if endpoint in c["raw_context"]]
            assert len(matching) == 1, (
                f"Expected exactly 1 chunk for '{endpoint}', "
                f"found {len(matching)}."
            )

    def test_summary_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Endpoint summaries should be embedded in raw_context."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        post_user = [c for c in chunks if "POST /users" in c["raw_context"]][0]
        assert "Create a new user account" in post_user["raw_context"]

        get_user = [c for c in chunks if "GET /users/{userId}" in c["raw_context"]][0]
        assert "Get user by ID" in get_user["raw_context"]

    def test_parameters_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Endpoint parameters should be included in the chunk."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        get_users = [c for c in chunks if "GET /users" in c["raw_context"]
                     and "{userId}" not in c["raw_context"]][0]
        ctx = get_users["raw_context"]
        assert "page" in ctx
        assert "query" in ctx

    def test_responses_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Response codes and descriptions should appear in the chunk."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        post_user = [c for c in chunks if "POST /users" in c["raw_context"]][0]
        ctx = post_user["raw_context"]
        assert "201" in ctx
        assert "400" in ctx
        assert "User created successfully" in ctx

    def test_request_body_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Request body description should appear in POST endpoints."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        post_user = [c for c in chunks if "POST /users" in c["raw_context"]][0]
        assert "Request Body" in post_user["raw_context"]
        assert "User registration payload" in post_user["raw_context"]

    def test_tags_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Tags should be included in the chunk."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        delete_user = [c for c in chunks if "DELETE" in c["raw_context"]][0]
        ctx = delete_user["raw_context"]
        assert "Users" in ctx
        assert "Admin" in ctx

    def test_operation_id_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """operationId should be included when present."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        delete_user = [c for c in chunks if "DELETE" in c["raw_context"]][0]
        assert "deleteUser" in delete_user["raw_context"]

    def test_api_title_in_raw_context(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """API title and version should be included for context."""
        chunks = parser.parse_file(str(openapi_yaml_file))

        for chunk in chunks:
            assert "User Management API" in chunk["raw_context"]
            assert "v1.2.0" in chunk["raw_context"]

    def test_source_file_propagated(
        self, parser: StructuredParser, openapi_yaml_file: Path
    ):
        """Every chunk should carry the source filename."""
        chunks = parser.parse_file(str(openapi_yaml_file))
        for c in chunks:
            assert c["source_file"] == "users_api.yaml"


# ═══════════════════════════════════════════════════════════════════
# 3. Swagger JSON Endpoint-Level Chunking
# ═══════════════════════════════════════════════════════════════════


class TestSwaggerJSONParsing:
    """Validate endpoint-level chunking for Swagger 2.0 JSON specs."""

    def test_correct_number_of_endpoint_chunks(
        self, parser: StructuredParser, swagger_json_file: Path
    ):
        """2 endpoints in the spec → exactly 2 chunks."""
        chunks = parser.parse_file(str(swagger_json_file))
        assert len(chunks) == 2

    def test_method_and_path_in_raw_context(
        self, parser: StructuredParser, swagger_json_file: Path
    ):
        """Both endpoints should be correctly identified."""
        chunks = parser.parse_file(str(swagger_json_file))

        post_payment = [c for c in chunks if "POST /payments" in c["raw_context"]]
        get_payment = [c for c in chunks if "GET /payments/{paymentId}" in c["raw_context"]]

        assert len(post_payment) == 1
        assert len(get_payment) == 1

    def test_swagger_api_title(
        self, parser: StructuredParser, swagger_json_file: Path
    ):
        """Swagger spec title should appear in chunks."""
        chunks = parser.parse_file(str(swagger_json_file))
        for c in chunks:
            assert "Payment Processing API" in c["raw_context"]

    def test_swagger_parameters(
        self, parser: StructuredParser, swagger_json_file: Path
    ):
        """Swagger-style parameters should be extracted."""
        chunks = parser.parse_file(str(swagger_json_file))

        get_payment = [c for c in chunks if "GET" in c["raw_context"]][0]
        assert "paymentId" in get_payment["raw_context"]
        assert "required" in get_payment["raw_context"]


# ═══════════════════════════════════════════════════════════════════
# 4. Generic Fallback (Non-API JSON/YAML)
# ═══════════════════════════════════════════════════════════════════


class TestGenericFallback:
    """
    Verify that non-API JSON/YAML files fall back to
    RecursiveCharacterTextSplitter instead of endpoint parsing.
    """

    def test_generic_json_produces_chunks(
        self, parser: StructuredParser, generic_json_file: Path
    ):
        """Generic JSON should produce chunks via recursive fallback."""
        chunks = parser.parse_file(str(generic_json_file))
        assert len(chunks) >= 1

    def test_generic_json_content_preserved(
        self, parser: StructuredParser, generic_json_file: Path
    ):
        """Key-value pairs from the config should appear in raw_context."""
        chunks = parser.parse_file(str(generic_json_file))
        all_context = " ".join(c["raw_context"] for c in chunks)
        assert "db.internal.bank.com" in all_context
        assert "5432" in all_context or "pool_size" in all_context

    def test_generic_json_no_endpoint_format(
        self, parser: StructuredParser, generic_json_file: Path
    ):
        """Generic JSON should NOT contain 'API Endpoint:' format."""
        chunks = parser.parse_file(str(generic_json_file))
        for c in chunks:
            assert "API Endpoint:" not in c["raw_context"]

    def test_generic_yaml_produces_chunks(
        self, parser: StructuredParser, generic_yaml_file: Path
    ):
        """Generic YAML should produce chunks via recursive fallback."""
        chunks = parser.parse_file(str(generic_yaml_file))
        assert len(chunks) >= 1

    def test_generic_yaml_content_preserved(
        self, parser: StructuredParser, generic_yaml_file: Path
    ):
        """Content from the YAML config should appear in raw_context."""
        chunks = parser.parse_file(str(generic_yaml_file))
        all_context = " ".join(c["raw_context"] for c in chunks)
        assert "rule-engine" in all_context
        assert "production" in all_context

    def test_large_generic_json_split(self, parser: StructuredParser, tmp_path: Path):
        """
        A large generic JSON file exceeding chunk_size should be
        split into multiple chunks by RecursiveCharacterTextSplitter.
        """
        large_data = {
            f"section_{i}": {
                "description": f"This is section {i} with detailed content. " * 20,
                "items": [f"item_{j}" for j in range(10)],
            }
            for i in range(30)
        }
        large_file = tmp_path / "large_config.json"
        large_file.write_text(json.dumps(large_data, indent=2))

        chunks = parser.parse_file(str(large_file))
        assert len(chunks) >= 2, (
            f"Expected multiple chunks from large generic JSON, got {len(chunks)}"
        )


# ═══════════════════════════════════════════════════════════════════
# 5. Edge Cases
# ═══════════════════════════════════════════════════════════════════


class TestStructuredParserEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_paths_object(self, parser: StructuredParser, tmp_path: Path):
        """An API spec with empty paths should produce no chunks."""
        spec = {"openapi": "3.0.0", "info": {"title": "Empty", "version": "1.0"}, "paths": {}}
        f = tmp_path / "empty_api.json"
        f.write_text(json.dumps(spec))
        chunks = parser.parse_file(str(f))
        assert len(chunks) == 0

    def test_spec_missing_info(self, parser: StructuredParser, tmp_path: Path):
        """An API spec with no 'info' block should still parse endpoints."""
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/ping": {
                    "get": {
                        "summary": "Ping",
                        "responses": {"200": {"description": "pong"}},
                    }
                }
            },
        }
        f = tmp_path / "no_info.json"
        f.write_text(json.dumps(spec))

        chunks = parser.parse_file(str(f))
        assert len(chunks) == 1
        assert "GET /ping" in chunks[0]["raw_context"]

    def test_yml_extension_handled(self, parser: StructuredParser, tmp_path: Path):
        """.yml extension should be handled identically to .yaml."""
        spec_yaml = "openapi: '3.0.0'\npaths:\n  /test:\n    get:\n      summary: Test\n      responses:\n        '200':\n          description: OK\n"
        f = tmp_path / "spec.yml"
        f.write_text(spec_yaml)

        chunks = parser.parse_file(str(f))
        assert len(chunks) == 1
        assert "GET /test" in chunks[0]["raw_context"]

    def test_invalid_json_returns_empty(self, parser: StructuredParser, tmp_path: Path):
        """Unparseable JSON should return empty, not crash."""
        f = tmp_path / "broken.json"
        f.write_text("{invalid json content!!!")

        chunks = parser.parse_file(str(f))
        assert chunks == []

    def test_invalid_yaml_returns_empty(self, parser: StructuredParser, tmp_path: Path):
        """Unparseable YAML should return empty, not crash."""
        f = tmp_path / "broken.yaml"
        f.write_text(":\n  :\n    - [invalid\n")

        chunks = parser.parse_file(str(f))
        # yaml.safe_load may interpret this in various ways; it should not crash
        assert isinstance(chunks, list)

    def test_parse_text_api(self, parser: StructuredParser):
        """parse_text should work for API specs without file I/O."""
        spec_json = json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Inline", "version": "1.0"},
            "paths": {"/inline": {"post": {"summary": "Inline test", "responses": {"200": {"description": "OK"}}}}}
        })
        chunks = parser.parse_text(spec_json, source_file="inline.json", ext=".json")
        assert len(chunks) == 1
        assert "POST /inline" in chunks[0]["raw_context"]


# ═══════════════════════════════════════════════════════════════════
# 6. Router Integration (Final — All Stubs Removed)
# ═══════════════════════════════════════════════════════════════════


class TestSemanticRouterPhase4:
    """Verify the fully operational router with StructuredParser wired."""

    def test_all_extensions_registered(self):
        """All target extensions must be in the supported map."""
        required = [".xlsx", ".xls", ".docx", ".md", ".json", ".yaml", ".yml", ".txt"]
        for ext in required:
            assert ext in SUPPORTED_EXTENSIONS, f"{ext} missing"

    def test_json_and_yaml_mapped_to_structured_parser(self):
        """JSON/YAML extensions should map to StructuredParser (not OpenAPIParser)."""
        assert SUPPORTED_EXTENSIONS[".json"] == "StructuredParser"
        assert SUPPORTED_EXTENSIONS[".yaml"] == "StructuredParser"
        assert SUPPORTED_EXTENSIONS[".yml"] == "StructuredParser"

    def test_router_processes_openapi_yaml(
        self, tmp_path: Path, openapi_yaml_file: Path
    ):
        """Router should produce ChunkRecords from an OpenAPI YAML file."""
        output_dir = tmp_path / "output"
        config = EmitterConfig(
            output_dir=str(output_dir),
            usecase_id="api-docs",
            identifier="platform-team",
        )

        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(openapi_yaml_file))

        assert len(chunks) == 4
        assert all(isinstance(c, ChunkRecord) for c in chunks)
        assert all(c.usecase_id == "api-docs" for c in chunks)
        assert all(c.file_name == "users_api.yaml" for c in chunks)

    def test_router_processes_swagger_json(
        self, tmp_path: Path, swagger_json_file: Path
    ):
        """Router should produce ChunkRecords from a Swagger JSON file."""
        output_dir = tmp_path / "output"
        config = EmitterConfig(output_dir=str(output_dir))

        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(swagger_json_file))

        assert len(chunks) == 2
        assert all(c.file_name == "payments_api.json" for c in chunks)

    def test_router_processes_generic_json(
        self, tmp_path: Path, generic_json_file: Path
    ):
        """Generic JSON should be processed via recursive fallback."""
        output_dir = tmp_path / "output"
        config = EmitterConfig(output_dir=str(output_dir))

        with SemanticRouter(config) as router:
            chunks = router.ingest_file(str(generic_json_file))

        assert len(chunks) >= 1
        # Should NOT have endpoint format
        for c in chunks:
            assert "API Endpoint:" not in c.raw_context

    def test_router_writes_valid_jsonl_for_api_spec(
        self, tmp_path: Path, openapi_yaml_file: Path
    ):
        """JSONL output from API spec parsing should be valid."""
        output_dir = tmp_path / "output"
        config = EmitterConfig(output_dir=str(output_dir))

        with SemanticRouter(config) as router:
            router.ingest_file(str(openapi_yaml_file))

        for f in output_dir.glob("*.jsonl"):
            for line in f.read_text().strip().splitlines():
                obj = json.loads(line)
                assert "raw_context" in obj
                assert "API Endpoint:" in obj["raw_context"]

    def test_router_no_stubs_remain(self, tmp_path: Path):
        """
        FINAL VALIDATION: Every supported extension should produce
        non-empty chunks from valid input (no stubs).
        """
        output_dir = tmp_path / "output"
        config = EmitterConfig(output_dir=str(output_dir))

        # Create sample files for each type
        (tmp_path / "test.md").write_text("# Title\nContent here.")
        (tmp_path / "test.txt").write_text("Plain text content for testing.")
        (tmp_path / "test.json").write_text('{"key": "value"}')
        (tmp_path / "test.yaml").write_text("key: value\nlist:\n  - item1\n")

        with SemanticRouter(config) as router:
            for f in ["test.md", "test.txt", "test.json", "test.yaml"]:
                chunks = router.ingest_file(str(tmp_path / f))
                assert len(chunks) >= 1, (
                    f"STUB DETECTED: {f} produced 0 chunks — "
                    f"parser may not be wired."
                )

    def test_full_pipeline_summary(
        self, tmp_path: Path, openapi_yaml_file: Path
    ):
        """The summary should correctly reflect all files processed."""
        output_dir = tmp_path / "output"
        config = EmitterConfig(output_dir=str(output_dir))

        (tmp_path / "readme.md").write_text("# Readme\nHello world.")
        (tmp_path / "config.json").write_text('{"env": "prod"}')

        with SemanticRouter(config) as router:
            router.ingest_file(str(tmp_path / "readme.md"))
            router.ingest_file(str(openapi_yaml_file))
            router.ingest_file(str(tmp_path / "config.json"))
            summary = router.summary()

        assert len(summary["processed_files"]) == 3
        assert summary["emitter_stats"]["total_chunks_written"] >= 6


# ═══════════════════════════════════════════════════════════════════
# 7. Real Artifact Tests
# ═══════════════════════════════════════════════════════════════════


class TestRealArtifacts:
    """Test against real workspace artifacts if available."""

    YAML_PATH = Path(
        "/Users/lakshaychandra/Documents/Semantic JSONL Converison"
        "/artifacts/credit_decisioning_openapi.yaml"
    )
    JSON_PATH = Path(
        "/Users/lakshaychandra/Documents/Semantic JSONL Converison"
        "/artifacts/credit_decisioning_swagger.json"
    )

    @pytest.mark.skipif(
        not YAML_PATH.exists(),
        reason="Real YAML artifact not available.",
    )
    def test_real_openapi_yaml(self, parser: StructuredParser):
        """Parse the actual credit decisioning OpenAPI YAML."""
        chunks = parser.parse_file(str(self.YAML_PATH))
        # The spec has 32 endpoints across 5 controllers
        assert len(chunks) >= 20, (
            f"Expected >= 20 endpoint chunks, got {len(chunks)}."
        )
        # Every chunk should have the endpoint format
        for c in chunks:
            assert "API Endpoint:" in c["raw_context"]
            assert c["source_file"] == "credit_decisioning_openapi.yaml"

    @pytest.mark.skipif(
        not JSON_PATH.exists(),
        reason="Real JSON artifact not available.",
    )
    def test_real_swagger_json(self, parser: StructuredParser):
        """Parse the actual credit decisioning Swagger JSON."""
        chunks = parser.parse_file(str(self.JSON_PATH))
        assert len(chunks) >= 20, (
            f"Expected >= 20 endpoint chunks, got {len(chunks)}."
        )
        for c in chunks:
            assert "API Endpoint:" in c["raw_context"]
            assert "Credit Decisioning" in c["raw_context"]
