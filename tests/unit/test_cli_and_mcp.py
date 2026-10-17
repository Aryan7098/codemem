"""Unit and integration tests for CodeMem CLI and MCP Server transports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from codemem.cli.main import cli
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.mcp.server import CodeMemMCPServer, create_mcp_server


@pytest.fixture
def temp_repo_env():
    """Create a temporary repository directory with dummy source code for testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        src_dir = Path(tmp_dir) / "src" / "auth"
        src_dir.mkdir(parents=True, exist_ok=True)
        py_file = src_dir / "validator.py"
        py_file.write_text(
            "class TokenValidator:\n"
            "    def verify_token(self, token: str) -> bool:\n"
            "        return token == 'valid'\n",
            encoding="utf-8",
        )
        yield tmp_dir, py_file


def test_cli_full_lifecycle(temp_repo_env):
    """Test full CLI lifecycle commands: init, remember, recall, update, history, explain, check-changes."""
    repo_path, py_file = temp_repo_env
    runner = CliRunner()
    store_file = Path(repo_path) / ".codemem" / "store.json"
    cli_env = {"CODEMEM_STORE_PATH": str(store_file)}

    # 1. init (text and JSON)
    res_init_json = runner.invoke(cli, ["init", "--path", repo_path, "--json"], env=cli_env)
    assert res_init_json.exit_code == 0, res_init_json.output
    repo_data = json.loads(res_init_json.output)
    repo_id = repo_data["repository_id"]
    assert repo_id is not None

    res_init_text = runner.invoke(cli, ["init", "--path", repo_path], env=cli_env)
    assert res_init_text.exit_code == 0
    assert "Repository initialized:" in res_init_text.output

    # 2. remember with evidence (JSON)
    res_rem_json = runner.invoke(
        cli,
        [
            "remember",
            "TokenValidator verifies bearer tokens",
            "--path",
            repo_path,
            "--subject",
            "TokenValidator",
            "--predicate",
            "verifies",
            "--object",
            "tokens",
            "--type",
            "architecture",
            "--tag",
            "auth",
            "--tag",
            "security",
            "--evidence",
            "src/auth/validator.py:TokenValidator.verify_token:1-3",
            "--json",
        ],
        env=cli_env,
    )
    assert res_rem_json.exit_code == 0, res_rem_json.output
    mem1_data = json.loads(res_rem_json.output)
    mem1_id = mem1_data["id"]
    logical_fact_id = mem1_data["logical_fact_id"]
    assert mem1_data["status"] == "OBSERVED"
    assert len(mem1_data["evidence_records"]) == 1

    # remember with verified test (text)
    res_rem_test = runner.invoke(
        cli,
        [
            "remember",
            "TokenValidator passes unit test suite",
            "--path",
            repo_path,
            "--evidence",
            "src/auth/validator.py",
            "--test-command",
            "pytest tests/test_validator.py",
            "--test-passed",
        ],
        env=cli_env,
    )
    assert res_rem_test.exit_code == 0
    assert "Memory recorded:" in res_rem_test.output
    assert "[VERIFIED]" in res_rem_test.output

    # remember unanchored (text)
    res_rem_unanchored = runner.invoke(
        cli,
        [
            "remember",
            "May migrate to OAuth3 in future",
            "--path",
            repo_path,
        ],
        env=cli_env,
    )
    assert res_rem_unanchored.exit_code == 0
    assert "[INFERRED]" in res_rem_unanchored.output

    # 3. recall (matching - JSON & text)
    res_recall_json = runner.invoke(
        cli,
        ["recall", "TokenValidator", "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_recall_json.exit_code == 0
    recall_data = json.loads(res_recall_json.output)
    assert recall_data["abstained"] is False
    assert len(recall_data["results"]) >= 1

    res_recall_text = runner.invoke(
        cli,
        ["recall", "TokenValidator", "--path", repo_path],
        env=cli_env,
    )
    assert res_recall_text.exit_code == 0
    assert "Found" in res_recall_text.output

    # recall with abstention
    res_recall_abstain = runner.invoke(
        cli,
        ["recall", "completely nonexistent alien technology", "--path", repo_path],
        env=cli_env,
    )
    assert res_recall_abstain.exit_code == 0
    assert "ABSTAINED:" in res_recall_abstain.output

    # 4. update (JSON and text)
    res_update_json = runner.invoke(
        cli,
        [
            "update",
            mem1_id,
            "TokenValidator verifies cryptographic JWT tokens",
            "--reason",
            "Clarified cryptographic signature requirement",
            "--path",
            repo_path,
            "--evidence",
            "src/auth/validator.py:TokenValidator.verify_token:1-3",
            "--json",
        ],
        env=cli_env,
    )
    assert res_update_json.exit_code == 0, res_update_json.output
    updated_mem = json.loads(res_update_json.output)
    mem2_id = updated_mem["id"]
    assert updated_mem["status"] == "OBSERVED"
    assert updated_mem["logical_fact_id"] == logical_fact_id

    # 5. history (JSON and text)
    res_hist_json = runner.invoke(
        cli,
        ["history", logical_fact_id, "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_hist_json.exit_code == 0
    hist_data = json.loads(res_hist_json.output)
    assert len(hist_data["versions"]) >= 2

    res_hist_text = runner.invoke(
        cli,
        ["history", mem2_id, "--path", repo_path],
        env=cli_env,
    )
    assert res_hist_text.exit_code == 0
    assert "History for Logical Fact:" in res_hist_text.output

    # 6. explain (JSON and text)
    res_explain_json = runner.invoke(
        cli,
        ["explain", mem2_id, "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_explain_json.exit_code == 0
    explain_data = json.loads(res_explain_json.output)
    assert explain_data["memory"]["id"] == mem2_id
    assert explain_data["freshness_status"] in ("VERIFIED", "OBSERVED")

    res_explain_text = runner.invoke(
        cli,
        ["explain", mem2_id, "--path", repo_path],
        env=cli_env,
    )
    assert res_explain_text.exit_code == 0
    assert "Memory Explanation:" in res_explain_text.output

    # 7. check-changes (JSON and text)
    res_check_clean = runner.invoke(
        cli,
        ["check-changes", "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_check_clean.exit_code == 0
    check_clean_data = json.loads(res_check_clean.output)
    assert check_clean_data["stale_count"] == 0

    res_check_clean_text = runner.invoke(
        cli,
        ["check-changes", "--path", repo_path],
        env=cli_env,
    )
    assert res_check_clean_text.exit_code == 0
    assert "All active memories are fresh." in res_check_clean_text.output

    # Mutate the evidence file to trigger stale detection
    py_file.write_text("# modified contents\n", encoding="utf-8")
    res_check_stale = runner.invoke(
        cli,
        ["check-changes", "--path", repo_path],
        env=cli_env,
    )
    assert res_check_stale.exit_code == 0
    assert "Detected" in res_check_stale.output
    assert "stale memory record(s)!" in res_check_stale.output


def test_cli_errors(temp_repo_env):
    """Test CLI error handling and structured error responses."""
    repo_path, _ = temp_repo_env
    runner = CliRunner()
    store_file = Path(repo_path) / ".codemem" / "store.json"
    cli_env = {"CODEMEM_STORE_PATH": str(store_file)}

    runner.invoke(cli, ["init", "--path", repo_path], env=cli_env)

    # 1. Invalid evidence path escaping repository boundary
    res_esc = runner.invoke(
        cli,
        [
            "remember",
            "Escaping claim",
            "--path",
            repo_path,
            "--evidence",
            "../outside.py",
            "--json",
        ],
        env=cli_env,
    )
    assert res_esc.exit_code != 0
    assert "INVALID_EVIDENCE_PATH" in res_esc.output

    # 2. Update nonexistent memory
    res_update_none = runner.invoke(
        cli,
        [
            "update",
            "nonexistent-mem-id",
            "New claim",
            "--reason",
            "Testing error",
            "--path",
            repo_path,
            "--json",
        ],
        env=cli_env,
    )
    assert res_update_none.exit_code != 0
    assert "MEMORY_NOT_FOUND" in res_update_none.output

    # 3. History nonexistent identifier
    res_hist_none = runner.invoke(
        cli,
        ["history", "nonexistent-fact-id", "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_hist_none.exit_code != 0
    assert "FACT_NOT_FOUND" in res_hist_none.output

    # 4. Explain nonexistent memory
    res_exp_none = runner.invoke(
        cli,
        ["explain", "nonexistent-mem-id", "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert res_exp_none.exit_code != 0
    assert "MEMORY_NOT_FOUND" in res_exp_none.output


def test_mcp_server_full_lifecycle(temp_repo_env):
    """Test CodeMemMCPServer tool calls across full lifecycle."""
    repo_path, py_file = temp_repo_env
    server = CodeMemMCPServer()

    # 1. register_repository
    repo_info = server.register_repository(path=repo_path)
    assert "repository_id" in repo_info
    repo_id = repo_info["repository_id"]

    # 2. remember (anchored with AST evidence)
    mem1 = server.remember(
        repository_id=repo_id,
        claim="TokenValidator checks tokens",
        subject="TokenValidator",
        predicate="checks",
        object="tokens",
        memory_type="architecture",
        tags=["auth", "tokens"],
        evidence=[
            {
                "file_path": "src/auth/validator.py",
                "symbol": "TokenValidator.verify_token",
            }
        ],
    )
    assert "id" in mem1
    assert mem1["status"] == "OBSERVED"
    mem1_id = mem1["id"]
    logical_fact_id = mem1["logical_fact_id"]

    # remember (anchored with file evidence that will stay active)
    mem_file = server.remember(
        repository_id=repo_id,
        claim="TokenValidator is defined in validator.py",
        evidence=[
            {
                "file_path": "src/auth/validator.py",
            }
        ],
    )
    assert mem_file["status"] == "OBSERVED"

    # 3. remember (unanchored)
    mem2 = server.remember(
        repository_id=repo_id,
        claim="Future system might use WebSockets",
        memory_type="architecture",
    )
    assert mem2["status"] == "INFERRED"

    # 4. recall (match)
    recall_resp = server.recall(repo_id=repo_id, query="TokenValidator")
    assert recall_resp["abstained"] is False
    assert len(recall_resp["results"]) >= 1

    # recall (abstain)
    recall_abstain = server.recall(repo_id=repo_id, query="quantum mechanics warp drive")
    assert recall_abstain["abstained"] is True
    assert recall_abstain["abstain_reason"] is not None

    # 5. update
    updated_mem = server.update(
        memory_id=mem1_id,
        new_claim="TokenValidator checks tokens and expiration",
        reason="Added token expiration requirement",
        repo_id=repo_id,
        new_evidence=[
            {
                "file_path": "src/auth/validator.py",
                "symbol": "TokenValidator.verify_token",
            }
        ],
    )
    assert updated_mem["id"] != mem1_id
    assert updated_mem["logical_fact_id"] == logical_fact_id
    assert updated_mem["status"] == "OBSERVED"
    mem3_id = updated_mem["id"]

    # 6. history
    hist = server.history(identifier=logical_fact_id, repo_id=repo_id)
    assert hist["logical_fact_id"] == logical_fact_id
    assert len(hist["versions"]) == 2

    # 7. explain
    explanation = server.explain(memory_id=mem3_id, repo_id=repo_id)
    assert explanation["memory"]["id"] == mem3_id
    assert explanation["freshness_status"] == "OBSERVED"
    assert len(explanation["evidence_records"]) == 1

    # 8. check_changes
    changes = server.check_changes(repo_id=repo_id)
    assert changes["stale_count"] == 0

    # Mutate file and re-check
    py_file.write_text("# mutated\n", encoding="utf-8")
    changes_stale = server.check_changes(repo_id=repo_id)
    assert changes_stale["stale_count"] > 0


def test_mcp_server_errors_and_edge_cases(temp_repo_env):
    """Test CodeMemMCPServer error responses and structured CodeMemErrorDetail contracts."""
    repo_path, _ = temp_repo_env
    server = CodeMemMCPServer()
    repo_info = server.register_repository(path=repo_path)
    repo_id = repo_info["repository_id"]

    # 1. Empty query error
    err_query = server.recall(repo_id=repo_id, query="   ")
    assert err_query.get("error_code") == "INVALID_QUERY"

    # 2. Repo not found on recall
    err_recall_repo = server.recall(repo_id="nonexistent-repo", query="test")
    assert err_recall_repo.get("error_code") == "REPO_NOT_FOUND"

    # 3. Repo not found on remember
    err_rem_repo = server.remember(repository_id="nonexistent-repo", claim="claim")
    assert err_rem_repo.get("error_code") == "REPO_NOT_FOUND"

    # 4. Invalid evidence path on remember
    err_rem_ev = server.remember(
        repository_id=repo_id,
        claim="Invalid path claim",
        evidence=[{"file_path": "../escaping.py"}],
    )
    assert err_rem_ev.get("error_code") == "INVALID_EVIDENCE_PATH"

    # 5. Memory not found on update
    err_up_mem = server.update(
        memory_id="nonexistent-mem",
        new_claim="New claim",
        reason="Testing",
        repo_id=repo_id,
    )
    assert err_up_mem.get("error_code") == "MEMORY_NOT_FOUND"

    # 6. Memory not found on explain
    err_exp_mem = server.explain(memory_id="nonexistent-mem", repo_id=repo_id)
    assert err_exp_mem.get("error_code") == "MEMORY_NOT_FOUND"

    # 7. Fact not found on history
    err_hist = server.history(identifier="nonexistent-id", repo_id=repo_id)
    assert err_hist.get("error_code") == "FACT_NOT_FOUND"

    # 8. Check changes on unknown repo
    err_check = server.check_changes(repo_id="nonexistent-repo")
    assert err_check.get("error_code") == "REPO_NOT_FOUND"

    # 9. Scope violation on update and explain
    mem = server.remember(repository_id=repo_id, claim="Scoped claim")
    mem_id = mem["id"]
    err_scope_up = server.update(
        memory_id=mem_id,
        new_claim="Hacked claim",
        reason="Testing scope",
        repo_id="other-repo-id",
    )
    assert err_scope_up.get("error_code") == "SCOPE_VIOLATION"

    err_scope_exp = server.explain(memory_id=mem_id, repo_id="other-repo-id")
    assert err_scope_exp.get("error_code") == "SCOPE_VIOLATION"


def test_create_mcp_server_factory():
    """Test create_mcp_server factory function."""
    store = InMemoryGraphStore()
    service = MemoryService(store=store)
    srv = create_mcp_server(service=service)
    assert isinstance(srv, CodeMemMCPServer)
    assert srv.service is service
