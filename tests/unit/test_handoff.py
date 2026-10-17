"""Unit tests for Gap 2: First-Class Agent Handoffs."""

from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner
import pytest

from codemem.cli.main import cli, load_persistent_store, save_persistent_store
from codemem.core.models import (
    HandoffInput,
    HandoffStatus,
    RepositoryInfo,
)
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.mcp.server import CodeMemMCPServer


@pytest.fixture
def test_env(tmp_path: Path):
    store = InMemoryGraphStore()
    service = MemoryService(store=store)
    repo = RepositoryInfo(
        repository_id="repo-handoff-test",
        root_path=str(tmp_path),
        head_commit="abcdef123456",
        git_available=True,
    )
    store.upsert_repository(repo)
    return service, repo, tmp_path


def test_service_handoff_lifecycle(test_env):
    service, repo, _ = test_env

    # 1. Record handoff
    inp = HandoffInput(
        repository_id=repo.repository_id,
        originating_agent_id="planner-agent",
        receiving_agent_id="coder-agent",
        task_objective="Implement OAuth2 Token Refresh Flow",
        completed_work="Designed TokenPair schema and redis cache key structure",
        open_risks=["Token rotation race condition under heavy concurrency", "Skewed TTL"],
        relevant_memory_ids=["mem-101", "mem-102"],
    )
    handoff = service.record_handoff(inp)
    assert handoff.handoff_id.startswith("handoff_") or handoff.handoff_id.startswith("handoff-")
    assert handoff.status == HandoffStatus.PENDING
    assert handoff.originating_agent_id == "planner-agent"
    assert handoff.receiving_agent_id == "coder-agent"
    assert len(handoff.open_risks) == 2
    assert len(handoff.relevant_memory_ids) == 2

    # 2. Get handoff
    retrieved = service.get_handoff(handoff.handoff_id, repo_id=repo.repository_id)
    assert retrieved is not None
    assert retrieved.task_objective == inp.task_objective

    # 3. Scope violation check
    with pytest.raises(PermissionError):
        service.get_handoff(handoff.handoff_id, repo_id="different-repo-id")

    # 4. List handoffs
    pending_list = service.list_handoffs(repo.repository_id, status=HandoffStatus.PENDING)
    assert len(pending_list) == 1
    assert pending_list[0].handoff_id == handoff.handoff_id

    # 5. Complete handoff
    completed = service.complete_handoff(
        handoff.handoff_id,
        repo_id=repo.repository_id,
        receiving_agent_id="coder-agent-instance-2",
    )
    assert completed.status == HandoffStatus.COMPLETED
    assert completed.receiving_agent_id == "coder-agent-instance-2"

    # Verify status filtering after completion
    assert len(service.list_handoffs(repo.repository_id, status=HandoffStatus.PENDING)) == 0
    completed_list = service.list_handoffs(repo.repository_id, status=HandoffStatus.COMPLETED)
    assert len(completed_list) == 1
    assert completed_list[0].handoff_id == handoff.handoff_id


def test_handoff_persistence(tmp_path: Path):
    store = InMemoryGraphStore()
    repo = RepositoryInfo(
        repository_id="persist-repo",
        root_path=str(tmp_path),
        head_commit="111222333",
        git_available=True,
    )
    store.upsert_repository(repo)
    service = MemoryService(store=store)

    inp = HandoffInput(
        repository_id="persist-repo",
        originating_agent_id="agent-alpha",
        receiving_agent_id="agent-beta",
        task_objective="Migrate SQLite to FalkorDB Cypher",
        completed_work="Defined Cypher schemas",
        open_risks=["Verify graph connection timeout"],
    )
    handoff = service.record_handoff(inp)

    state_file = tmp_path / ".codemem" / "store.json"
    save_persistent_store(store, state_file)

    loaded_store = load_persistent_store(state_file)
    restored_handoff = loaded_store.get_handoff(handoff.handoff_id)
    assert restored_handoff is not None
    assert restored_handoff.task_objective == "Migrate SQLite to FalkorDB Cypher"
    assert restored_handoff.originating_agent_id == "agent-alpha"
    assert restored_handoff.open_risks == ["Verify graph connection timeout"]


def test_cli_handoff_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CODEMEM_STORE_PATH", str(tmp_path / "store.json"))
    runner = CliRunner()

    # Init
    init_res = runner.invoke(cli, ["init", "--path", str(tmp_path)])
    assert init_res.exit_code == 0

    # Record handoff
    rec_res = runner.invoke(
        cli,
        [
            "handoff",
            "record",
            "--path",
            str(tmp_path),
            "--from",
            "agent-architect",
            "--to",
            "agent-implementer",
            "--objective",
            "Implement FastMCP tools",
            "--completed",
            "Registered remember, recall, update tools",
            "--risk",
            "Handle JSON serialization of nested types",
            "--memory",
            "mem-101",
            "--json",
        ],
    )
    assert rec_res.exit_code == 0
    rec_data = json.loads(rec_res.output)
    h_id = rec_data["handoff_id"]
    assert rec_data["status"] == "PENDING"
    assert rec_data["task_objective"] == "Implement FastMCP tools"

    # List handoffs
    list_res = runner.invoke(cli, ["handoff", "list", "--path", str(tmp_path), "--json"])
    assert list_res.exit_code == 0
    list_data = json.loads(list_res.output)
    assert len(list_data) == 1
    assert list_data[0]["handoff_id"] == h_id

    # Complete handoff
    comp_res = runner.invoke(
        cli,
        [
            "handoff",
            "complete",
            h_id,
            "--path",
            str(tmp_path),
            "--agent",
            "agent-implementer",
            "--json",
        ],
    )
    assert comp_res.exit_code == 0
    comp_data = json.loads(comp_res.output)
    assert comp_data["status"] == "COMPLETED"
    assert comp_data["receiving_agent_id"] == "agent-implementer"


def test_mcp_handoff_tools(test_env):
    service, repo, tmp_path = test_env
    mcp_server = CodeMemMCPServer(service=service, repo_path=str(tmp_path))

    # Record
    rec_res = mcp_server.record_handoff(
        repository_id=repo.repository_id,
        originating_agent_id="mcp-lead",
        receiving_agent_id="mcp-worker",
        task_objective="Wire up background workers",
        completed_work="Created Celery tasks",
        open_risks=["Redis connection retry backoff missing"],
        relevant_memory_ids=["mem-workers"],
    )
    assert "handoff_id" in rec_res
    h_id = rec_res["handoff_id"]
    assert rec_res["status"] == "PENDING"

    # List
    list_res = mcp_server.list_handoffs(repo_id=repo.repository_id, status="PENDING")
    assert len(list_res) == 1
    assert list_res[0]["handoff_id"] == h_id

    # Complete
    comp_res = mcp_server.complete_handoff(
        handoff_id=h_id,
        repo_id=repo.repository_id,
        receiving_agent_id="mcp-worker-final",
    )
    assert comp_res["status"] == "COMPLETED"

    # Not found error handling
    err_res = mcp_server.complete_handoff(
        handoff_id="non-existent-handoff",
        repo_id=repo.repository_id,
    )
    assert err_res.get("error_code") == "HANDOFF_NOT_FOUND"
