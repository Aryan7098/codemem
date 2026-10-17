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

    # 8. revalidate memory
    # Restore code file and revalidate with passing test
    py_file.write_text(
        "class TokenValidator:\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        return token == 'valid'\n",
        encoding="utf-8",
    )
    res_reval = runner.invoke(
        cli,
        [
            "revalidate",
            mem2_id,
            "--path",
            repo_path,
            "--test-cmd",
            "pytest tests/test_validator.py",
            "--test-passed",
            "--json",
        ],
        env=cli_env,
    )
    assert res_reval.exit_code == 0, res_reval.output
    reval_data = json.loads(res_reval.output)
    assert reval_data["status"] == "VERIFIED"
    assert reval_data["confidence_score"] == 0.95


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

    # 5. Scope violation on CLI update, history, explain
    rem_scoped = runner.invoke(
        cli,
        ["remember", "CLI scoped claim", "--path", repo_path, "--json"],
        env=cli_env,
    )
    assert rem_scoped.exit_code == 0
    scoped_id = json.loads(rem_scoped.output)["id"]

    res_cli_scope_up = runner.invoke(
        cli,
        ["update", scoped_id, "Hacked claim", "--reason", "Scope test", "--path", repo_path, "--repo-id", "other-repo-id", "--json"],
        env=cli_env,
    )
    assert res_cli_scope_up.exit_code != 0
    assert "SCOPE_VIOLATION" in res_cli_scope_up.output

    res_cli_scope_hist = runner.invoke(
        cli,
        ["history", scoped_id, "--path", repo_path, "--repo-id", "other-repo-id", "--json"],
        env=cli_env,
    )
    assert res_cli_scope_hist.exit_code != 0
    assert "SCOPE_VIOLATION" in res_cli_scope_hist.output

    res_cli_scope_exp = runner.invoke(
        cli,
        ["explain", scoped_id, "--path", repo_path, "--repo-id", "other-repo-id", "--json"],
        env=cli_env,
    )
    assert res_cli_scope_exp.exit_code != 0
    assert "SCOPE_VIOLATION" in res_cli_scope_exp.output


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

    # 9. revalidate_memory
    py_file.write_text(
        "class TokenValidator:\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        return token == 'valid'\n",
        encoding="utf-8",
    )
    reval_resp = server.revalidate_memory(
        memory_id=mem3_id,
        repo_id=repo_id,
        test_command="pytest tests/test_validator.py",
        test_passed=True,
    )
    assert reval_resp["status"] == "VERIFIED"
    assert reval_resp["confidence_score"] == 0.95


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

    # 9. Scope violation on update, explain, and history
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

    err_scope_hist = server.history(identifier=mem_id, repo_id="other-repo-id")
    assert err_scope_hist.get("error_code") == "SCOPE_VIOLATION"


def test_create_mcp_server_factory():
    """Test create_mcp_server factory function."""
    store = InMemoryGraphStore()
    service = MemoryService(store=store)
    srv = create_mcp_server(service=service)
    assert isinstance(srv, CodeMemMCPServer)
    assert srv.service is service


def test_semantic_vector_search_without_lexical_overlap(tmp_path: Path) -> None:
    """
    Verify P1 Semantic Vector Search:
    Query 'Where do we check credentials?' retrieves 'TokenValidator verifies bearer tokens'
    even with zero shared surface words and no explicit tags.
    """
    from codemem.core.embeddings import FalkorDBVectorIndexSpec
    from codemem.core.models import EvidenceInput, MemoryInput

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "security.py").write_text(
        "class TokenValidator:\n    def verify(self, token: str) -> bool:\n        return True\n",
        encoding="utf-8",
    )

    service = MemoryService(store=InMemoryGraphStore())
    repo = service.register_repository(str(tmp_path))

    # Record claim with NO shared words with 'Where do we check credentials?' and NO tags
    mem = service.remember(
        MemoryInput(
            repository_id=repo.repository_id,
            claim="TokenValidator verifies bearer tokens",
            evidence=[EvidenceInput(file_path="src/security.py", symbol="TokenValidator")],
        )
    )
    assert mem.embedding is not None
    assert len(mem.embedding) == 64

    # Query using purely semantic phrasing
    recall_resp = service.recall(
        repo_id=repo.repository_id,
        query="Where do we check credentials?",
    )
    assert recall_resp.abstained is False
    assert len(recall_resp.results) >= 1
    assert recall_resp.results[0].memory.id == mem.id

    # Verify FalkorDB Vector Index Cypher generation
    ddl = FalkorDBVectorIndexSpec.create_vector_index_cypher(dimension=64)
    assert "CREATE VECTOR INDEX FOR (m:Memory) ON (m.embedding)" in ddl
    cypher, params = FalkorDBVectorIndexSpec.vector_query_cypher(
        repo_id=repo.repository_id,
        query_embedding=mem.embedding,
        top_k=5,
    )
    assert "db.idx.vector.queryNodes('Memory', 'embedding'" in cypher
    assert params["repo_id"] == repo.repository_id


def test_falkordb_backend_switching_via_env(tmp_path: Path, monkeypatch) -> None:
    """Verify CODEMEM_BACKEND=falkordb switches both factory, CLI, and MCP to FalkorDBGraphStore."""
    from codemem.graph import factory
    from codemem.graph.falkordb_store import FalkorDBGraphStore

    # Verify env resolution
    monkeypatch.setenv("CODEMEM_BACKEND", "falkordb")
    assert factory.get_backend_name() == "falkordb"

    # When no FalkorDB container is running on an unused port, it raises DATABASE_UNAVAILABLE
    monkeypatch.setenv("FALKORDB_PORT", "63999")
    try:
        FalkorDBGraphStore()
        assert False, "Expected RuntimeError when FalkorDB container is unreachable"
    except RuntimeError as exc:
        assert "DATABASE_UNAVAILABLE" in str(exc)


def test_falkordb_store_with_mock_client():
    """Verify FalkorDBGraphStore Cypher node and edge generation using mock client."""
    from unittest.mock import MagicMock
    from codemem.graph.falkordb_store import FalkorDBGraphStore
    from codemem.core.models import RepositoryInfo, MemoryRecord, EvidenceRecord, MemoryType, MemoryStatus

    mock_graph = MagicMock()
    mock_res = MagicMock()
    mock_res.result_set = []
    mock_graph.query.return_value = mock_res
    mock_client = MagicMock()
    mock_client.select_graph.return_value = mock_graph

    store = FalkorDBGraphStore(client=mock_client)
    assert mock_graph.query.called

    repo = RepositoryInfo(
        repository_id="repo-mock",
        root_path="/mock/path",
        head_commit="abc1234",
        remote_url="https://github.com/example/repo",
        git_available=True,
    )
    store.upsert_repository(repo)
    cypher_calls = [c[0][0] for c in mock_graph.query.call_args_list]
    assert any("MERGE (r:Repository" in c for c in cypher_calls)

    mem = MemoryRecord(
        repository_id="repo-mock",
        claim="FalkorDB stores graph nodes",
        memory_type=MemoryType.ARCHITECTURE,
        status=MemoryStatus.VERIFIED,
        tags=["database", "falkordb"],
    )
    ev = EvidenceRecord(
        repository_id="repo-mock",
        file_path="src/codemem/graph/falkordb_store.py",
    )
    store.save_memory(mem, [ev])
    cypher_calls_after = [c[0][0] for c in mock_graph.query.call_args_list]
    assert any("MERGE (m:Memory" in c for c in cypher_calls_after)
    assert any("MERGE (e:Evidence" in c for c in cypher_calls_after)
    assert any("MERGE (c:Concept" in c for c in cypher_calls_after)


def test_backend_resolution_and_factory(monkeypatch):
    """Verify resolve_backend_name and create_graph_store configuration and defaults."""
    from unittest.mock import MagicMock
    from codemem.graph.adapter import FalkorDBAdapter
    from codemem.graph.factory import create_graph_store, resolve_backend_name
    from codemem.graph.in_memory import InMemoryGraphStore

    # 1. Defaults to memory
    monkeypatch.delenv("CODEMEM_BACKEND", raising=False)
    assert resolve_backend_name() == "memory"
    assert isinstance(create_graph_store(), InMemoryGraphStore)

    # 2. Respects CODEMEM_BACKEND=falkordb
    monkeypatch.setenv("CODEMEM_BACKEND", "falkordb")
    assert resolve_backend_name() == "falkordb"

    # 3. Respects explicit backend
    assert resolve_backend_name("memory") == "memory"
    assert resolve_backend_name("FALKORDB") == "falkordb"

    # 4. create_graph_store("falkordb") reads env vars and passes them to FalkorDBAdapter
    monkeypatch.setenv("FALKORDB_HOST", "falkor-node.internal")
    monkeypatch.setenv("FALKORDB_PORT", "6380")
    monkeypatch.setenv("FALKORDB_GRAPH", "graph_test")
    monkeypatch.setenv("FALKORDB_PASSWORD", "secret123")

    mock_adapter_cls = MagicMock()
    mock_instance = MagicMock(spec=FalkorDBAdapter)
    mock_adapter_cls.return_value = mock_instance
    monkeypatch.setattr("codemem.graph.factory.FalkorDBAdapter", mock_adapter_cls)

    store = create_graph_store("falkordb")
    assert store is mock_instance
    mock_adapter_cls.assert_called_once()
    kwargs = mock_adapter_cls.call_args.kwargs
    assert kwargs["host"] == "falkor-node.internal"
    assert kwargs["port"] == 6380
    assert kwargs.get("graph_id") == "graph_test" or kwargs.get("graph_name") == "graph_test"
    assert kwargs["password"] == "secret123"


def test_cli_backend_switching_and_no_store_file(temp_repo_env, monkeypatch):
    """Verify running CLI with --backend falkordb or CODEMEM_BACKEND=falkordb routes to FalkorDBAdapter without writing store.json."""
    from unittest.mock import MagicMock
    from click.testing import CliRunner
    from codemem.cli.main import cli
    from codemem.core.models import RepositoryInfo
    from codemem.graph.adapter import FalkorDBAdapter

    repo_path, _ = temp_repo_env
    runner = CliRunner()
    store_file = Path(repo_path) / ".codemem" / "store.json"

    mock_adapter_instance = MagicMock(spec=FalkorDBAdapter)
    mock_repo = RepositoryInfo(
        repository_id="mock-falkor-repo",
        root_path=str(Path(repo_path).resolve()),
        git_available=False,
    )
    mock_adapter_instance.upsert_repository.return_value = mock_repo
    mock_adapter_instance.get_repository.return_value = mock_repo
    mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)
    monkeypatch.setattr("codemem.graph.factory.FalkorDBAdapter", mock_adapter_cls)

    # 1. Run with top-level --backend falkordb option
    res1 = runner.invoke(cli, ["--backend", "falkordb", "init", "--path", repo_path, "--json"])
    assert res1.exit_code == 0, res1.output
    assert not store_file.exists(), "store.json should NOT be created when using falkordb backend"

    # 2. Run with subcommand option recall "..." --backend falkordb
    mock_adapter_instance.get_all_memories.return_value = []
    mock_adapter_instance.traverse_concept_graph.return_value = []
    mock_adapter_instance.vector_search.return_value = []
    mock_adapter_instance.query_memories_by_terms.return_value = []
    res2 = runner.invoke(cli, ["recall", "test query", "--path", repo_path, "--backend", "falkordb", "--json"])
    assert res2.exit_code == 0, res2.output
    assert not store_file.exists(), "store.json should NOT be created when using falkordb backend"

    # 3. Run with CODEMEM_BACKEND=falkordb envvar
    res3 = runner.invoke(cli, ["init", "--path", repo_path, "--json"], env={"CODEMEM_BACKEND": "falkordb"})
    assert res3.exit_code == 0, res3.output
    assert not store_file.exists(), "store.json should NOT be created when using falkordb backend"


def test_mcp_server_backend_switching(monkeypatch):
    """Verify CodeMemMCPServer(backend='falkordb') initializes MemoryService with FalkorDBAdapter."""
    from unittest.mock import MagicMock
    from codemem.graph.adapter import FalkorDBAdapter
    from codemem.mcp.server import CodeMemMCPServer, create_mcp_server

    mock_adapter_instance = MagicMock(spec=FalkorDBAdapter)
    mock_adapter_cls = MagicMock(return_value=mock_adapter_instance)
    monkeypatch.setattr("codemem.graph.factory.FalkorDBAdapter", mock_adapter_cls)

    server = CodeMemMCPServer(backend="falkordb")
    assert server.service.store is mock_adapter_instance

    # Test factory with backend
    server2 = create_mcp_server(backend="falkordb")
    assert server2.service.store is mock_adapter_instance



