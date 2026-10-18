"""Unit tests for CodebaseScanner and automated graph bootstrapping."""

from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner
import pytest

from codemem.cli.main import cli
from codemem.core.models import ScanReport
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.mcp.server import CodeMemMCPServer


@pytest.fixture
def sample_external_repo(tmp_path: Path):
    """Create a realistic sample repository on disk to scan."""
    repo_dir = tmp_path / "sample_app"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # 1. pyproject.toml
    pyproject = repo_dir / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'name = "my-sample-app"\n'
        'version = "1.0.0"\n'
        'dependencies = [\n'
        '    "fastapi>=0.100.0",\n'
        '    "pydantic>=2.0.0",\n'
        '    "redis>=5.0.0"\n'
        ']\n',
        encoding="utf-8",
    )

    # 2. Source code
    src_dir = repo_dir / "src" / "services"
    src_dir.mkdir(parents=True, exist_ok=True)
    auth_file = src_dir / "auth.py"
    auth_file.write_text(
        'class SessionAuthenticator:\n'
        '    """Manages token creation and claims verification for active user sessions."""\n'
        '    def authenticate(self, token: str) -> bool:\n'
        '        return True\n',
        encoding="utf-8",
    )

    db_file = src_dir / "database.py"
    db_file.write_text(
        'class DatabasePool:\n'
        '    """Initializes connection pooling for Redis and relational databases."""\n'
        '    def get_connection(self):\n'
        '        pass\n',
        encoding="utf-8",
    )

    # 3. Tests
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_file = tests_dir / "test_auth.py"
    test_file.write_text("def test_dummy(): pass\n", encoding="utf-8")

    return repo_dir


def test_service_scan_repository(sample_external_repo: Path):
    store = InMemoryGraphStore()
    service = MemoryService(store=store)

    report = service.scan_repository(path=str(sample_external_repo))
    assert isinstance(report, ScanReport)
    assert report.files_scanned >= 2
    assert report.memories_created >= 3
    assert "fastapi" in report.dependencies_found or "pydantic" in report.dependencies_found
    assert "SessionAuthenticator" in report.symbols_indexed
    assert "DatabasePool" in report.symbols_indexed

    # Verify that memories are immediately queryable via recall!
    recall_resp = service.recall(repo_id=report.repository_id, query="SessionAuthenticator")
    assert not recall_resp.abstained
    assert len(recall_resp.results) >= 1
    top_mem = recall_resp.results[0].memory
    assert "SessionAuthenticator" in top_mem.claim
    assert top_mem.evidence_records[0].symbol == "SessionAuthenticator"


def test_cli_scan_command(sample_external_repo: Path, monkeypatch):
    monkeypatch.setenv("CODEMEM_STORE_PATH", str(sample_external_repo / "store.json"))
    runner = CliRunner()

    res = runner.invoke(cli, ["scan", "--path", str(sample_external_repo), "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["files_scanned"] >= 2
    assert data["memories_created"] >= 3
    assert "SessionAuthenticator" in data["symbols_indexed"]


def test_mcp_scan_tool(sample_external_repo: Path):
    store = InMemoryGraphStore()
    service = MemoryService(store=store)
    mcp_server = CodeMemMCPServer(service=service, repo_path=str(sample_external_repo))

    res = mcp_server.scan_repository(path=str(sample_external_repo))
    assert "memories_created" in res
    assert res["memories_created"] >= 3
    assert "SessionAuthenticator" in res["symbols_indexed"]
