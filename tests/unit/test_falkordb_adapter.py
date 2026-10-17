"""Unit tests for FalkorDBAdapter implementing GraphStoreProtocol."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from codemem.core.models import (
    EvidenceRecord,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
)
from codemem.graph.adapter import FalkorDBAdapter
from codemem.graph.protocol import GraphStoreProtocol


class MockQueryResult:
    """Mock result object mimicking FalkorDB QueryResult."""

    def __init__(self, result_set: Optional[List[List[Any]]] = None) -> None:
        self.result_set = result_set or []


class MockNode:
    """Mock FalkorDB Node object holding properties."""

    def __init__(self, properties: Dict[str, Any]) -> None:
        self.properties = dict(properties)


class MockFalkorGraph:
    """Mock FalkorDB Graph client storing in-memory graph state for Cypher unit tests."""

    def __init__(self) -> None:
        self.queries_executed: List[Tuple[str, Dict[str, Any]]] = []
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.evidence: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []

    def query(self, q: str, params: Optional[Dict[str, Any]] = None) -> MockQueryResult:
        p = params or {}
        self.queries_executed.append((q.strip(), p))

        # 1. RETURN 1 (Health check)
        if "RETURN 1" in q:
            return MockQueryResult([[1]])

        # 1.1 Clear (DETACH DELETE)
        if "DETACH DELETE" in q:
            if p.get("repo_id"):
                r_id = p["repo_id"]
                self.nodes = {k: v for k, v in self.nodes.items() if v.get("repository_id") != r_id and v.get("id") != r_id}
            else:
                self.nodes.clear()
                self.evidence.clear()
                self.edges.clear()
            return MockQueryResult([])

        # 2. Repository operations
        if "MERGE (r:Repository" in q:
            repo_id = p["id"]
            self.nodes[f"repo_{repo_id}"] = {
                "id": repo_id,
                "root_path": p["root_path"],
                "head_commit": p.get("head_commit"),
                "git_remote_url": p.get("git_remote_url"),
                "git_available": p.get("git_available", False),
                "created_at": p.get("updated_at"),
                "updated_at": p.get("updated_at"),
            }
            return MockQueryResult([[MockNode(self.nodes[f"repo_{repo_id}"])]])

        if "MATCH (r:Repository {id: $id})" in q:
            repo_id = p["id"]
            node = self.nodes.get(f"repo_{repo_id}")
            if node:
                return MockQueryResult([[MockNode(node)]])
            return MockQueryResult([])

        # 3. Memory creation
        if "MERGE (m:Memory {id: $id})" in q:
            mem_id = p["id"]
            self.nodes[f"mem_{mem_id}"] = dict(p)
            return MockQueryResult([[MockNode(self.nodes[f"mem_{mem_id}"])]])

        # 4. Evidence creation
        if "MERGE (e:Evidence {id: $id})" in q:
            ev_id = p["id"]
            self.evidence[ev_id] = dict(p)
            self.edges.append({"from": p["memory_id"], "to": ev_id, "type": "SUPPORTED_BY"})
            return MockQueryResult([])

        # 5. Concept creation
        if "MERGE (c:Concept" in q:
            return MockQueryResult([])

        # 6. Status update
        if "SET m.status = $status" in q:
            mem_id = p["memory_id"]
            if f"mem_{mem_id}" in self.nodes:
                self.nodes[f"mem_{mem_id}"]["status"] = p["status"]
            return MockQueryResult([])

        # 7. Supersede
        if "SET old_m.status = 'SUPERSEDED'" in q:
            old_id = p["old_id"]
            new_id = p["new_id"]
            if f"mem_{old_id}" in self.nodes:
                self.nodes[f"mem_{old_id}"]["status"] = "SUPERSEDED"
                self.nodes[f"mem_{old_id}"]["superseded_by_id"] = new_id
            if f"mem_{new_id}" in self.nodes:
                self.nodes[f"mem_{new_id}"]["supersedes_id"] = old_id
            self.edges.append({"from": new_id, "to": old_id, "type": "SUPERSEDES", "reason": p.get("reason")})
            return MockQueryResult([])

        # 8. Get memory by ID
        if "MATCH (m:Memory {id: $memory_id})" in q:
            mem_id = p["memory_id"]
            repo_id = p.get("repo_id")
            node = self.nodes.get(f"mem_{mem_id}")
            if node and (repo_id is None or node.get("repository_id") == repo_id):
                attached_ev = [
                    MockNode(self.evidence[e["to"]])
                    for e in self.edges
                    if e["from"] == mem_id and e["type"] == "SUPPORTED_BY" and e["to"] in self.evidence
                ]
                return MockQueryResult([[MockNode(node), attached_ev]])
            return MockQueryResult([])

        # 9. Get memories by logical fact
        if "MATCH (m:Memory {logical_fact_id: $logical_fact_id" in q:
            fact_id = p["logical_fact_id"]
            repo_id = p["repo_id"]
            rows: List[List[Any]] = []
            for k, node in self.nodes.items():
                if k.startswith("mem_") and node.get("logical_fact_id") == fact_id and node.get("repository_id") == repo_id:
                    attached_ev = [
                        MockNode(self.evidence[e["to"]])
                        for e in self.edges
                        if e["from"] == node["id"] and e["type"] == "SUPPORTED_BY" and e["to"] in self.evidence
                    ]
                    rows.append([MockNode(node), attached_ev])
            return MockQueryResult(rows)

        # 10. Get all memories
        if "MATCH (m:Memory {repository_id: $repo_id})" in q:
            repo_id = p["repo_id"]
            inc_sup = p.get("include_superseded", False)
            inc_stale = p.get("include_stale", True)
            rows = []
            for k, node in self.nodes.items():
                if k.startswith("mem_") and node.get("repository_id") == repo_id:
                    if not inc_sup and node.get("status") == "SUPERSEDED":
                        continue
                    if not inc_stale and node.get("status") == "STALE":
                        continue
                    attached_ev = [
                        MockNode(self.evidence[e["to"]])
                        for e in self.edges
                        if e["from"] == node["id"] and e["type"] == "SUPPORTED_BY" and e["to"] in self.evidence
                    ]
                    rows.append([MockNode(node), attached_ev])
            return MockQueryResult(rows)

        # 11. Concept graph traversal
        if "MATCH (c:Concept)" in q and "expanded:Concept" in q:
            concepts = p.get("concepts", [])
            repo_id = p.get("repo_id")
            rows = []
            for k, node in self.nodes.items():
                if k.startswith("mem_") and node.get("repository_id") == repo_id:
                    node_tags = [t.lower() for t in node.get("tags", [])]
                    if any(c in node_tags or c == "auth" for c in concepts):
                        attached_ev = [
                            MockNode(self.evidence[e["to"]])
                            for e in self.edges
                            if e["from"] == node["id"] and e["type"] == "SUPPORTED_BY" and e["to"] in self.evidence
                        ]
                        rows.append([MockNode(node), attached_ev])
            return MockQueryResult(rows)

        # 12. Referencing file
        if "e:Evidence {file_path: $file_path}" in q:
            f_path = p["file_path"]
            repo_id = p["repo_id"]
            rows = []
            for k, node in self.nodes.items():
                if k.startswith("mem_") and node.get("repository_id") == repo_id:
                    for e in self.edges:
                        if e["from"] == node["id"] and e["type"] == "SUPPORTED_BY":
                            ev = self.evidence.get(e["to"])
                            if ev and ev.get("file_path") == f_path:
                                attached_ev = [
                                    MockNode(self.evidence[ed["to"]])
                                    for ed in self.edges
                                    if ed["from"] == node["id"] and ed["to"] in self.evidence
                                ]
                                rows.append([MockNode(node), attached_ev])
                                break
            return MockQueryResult(rows)

        # 13. Clear
        if "DETACH DELETE" in q:
            if p.get("repo_id"):
                r_id = p["repo_id"]
                self.nodes = {k: v for k, v in self.nodes.items() if v.get("repository_id") != r_id and v.get("id") != r_id}
            else:
                self.nodes.clear()
                self.evidence.clear()
                self.edges.clear()
            return MockQueryResult([])

        return MockQueryResult([])


@pytest.fixture
def mock_falkordb_adapter():
    """Create a FalkorDBAdapter with injected MockFalkorGraph client."""
    mock_client = MockFalkorGraph()
    adapter = FalkorDBAdapter(graph_client=mock_client)
    return adapter, mock_client


def test_falkordb_adapter_satisfies_protocol(mock_falkordb_adapter):
    """Verify FalkorDBAdapter satisfies GraphStoreProtocol."""
    adapter, _ = mock_falkordb_adapter
    assert isinstance(adapter, GraphStoreProtocol)


def test_falkordb_health_check(mock_falkordb_adapter):
    """Verify health_check queries FalkorDB."""
    adapter, _ = mock_falkordb_adapter
    assert adapter.health_check() is True


def test_falkordb_repository_lifecycle(mock_falkordb_adapter):
    """Verify upsert_repository and get_repository with Cypher parameters."""
    adapter, _ = mock_falkordb_adapter
    repo = RepositoryInfo(
        repository_id="repo_falkor_01",
        root_path="/code/my_project",
        head_commit="commit_123",
        git_available=True,
    )
    saved = adapter.upsert_repository(repo)
    assert saved.repository_id == "repo_falkor_01"

    fetched = adapter.get_repository("repo_falkor_01")
    assert fetched is not None
    assert fetched.repository_id == "repo_falkor_01"
    assert fetched.root_path == "/code/my_project"
    assert fetched.head_commit == "commit_123"

    assert adapter.get_repository("nonexistent_repo") is None


def test_falkordb_memory_storage_and_query(mock_falkordb_adapter):
    """Verify save_memory, get_memory, and query_memories_by_terms with evidence."""
    adapter, _ = mock_falkordb_adapter
    repo_id = "repo_test"
    adapter.upsert_repository(RepositoryInfo(repository_id=repo_id, root_path="/repo"))

    ev = EvidenceRecord(
        repository_id=repo_id,
        file_path="src/auth/jwt.py",
        symbol="TokenValidator.verify",
        content_hash="sha_abc123",
    )
    mem = MemoryRecord(
        repository_id=repo_id,
        logical_fact_id="fact_jwt_01",
        claim="TokenValidator validates JWT tokens",
        subject="TokenValidator",
        predicate="validates",
        object="JWT tokens",
        status=MemoryStatus.OBSERVED,
        tags=["auth", "jwt"],
    )

    saved_mem = adapter.save_memory(mem, [ev])
    assert saved_mem.id == mem.id
    assert len(saved_mem.evidence_records) == 1

    # Fetch by ID
    fetched = adapter.get_memory(mem.id, repo_id=repo_id)
    assert fetched is not None
    assert fetched.claim == mem.claim
    assert len(fetched.evidence_records) == 1
    assert fetched.evidence_records[0].file_path == "src/auth/jwt.py"

    # Query by terms
    matches = adapter.query_memories_by_terms(repo_id=repo_id, terms=["jwt", "token"])
    assert len(matches) == 1
    assert matches[0].id == mem.id

    # Scope isolation: query wrong repo returns 0
    other_matches = adapter.query_memories_by_terms(repo_id="other_repo", terms=["jwt"])
    assert len(other_matches) == 0


def test_falkordb_supersede_and_history(mock_falkordb_adapter):
    """Verify supersede_memory links [:SUPERSEDES] edge and history retrieval."""
    adapter, _ = mock_falkordb_adapter
    repo_id = "repo_test"

    v1 = MemoryRecord(
        repository_id=repo_id,
        logical_fact_id="fact_db_01",
        claim="Database uses Redis for cache",
        status=MemoryStatus.OBSERVED,
    )
    adapter.save_memory(v1, [])

    v2 = MemoryRecord(
        repository_id=repo_id,
        logical_fact_id="fact_db_01",
        claim="Database uses FalkorDB for graph memory and cache",
        status=MemoryStatus.VERIFIED,
    )
    adapter.save_memory(v2, [])
    adapter.supersede_memory(old_memory_id=v1.id, new_memory=v2, reason="Upgraded to FalkorDB")

    # Check v1 is SUPERSEDED
    old_fetched = adapter.get_memory(v1.id, repo_id=repo_id)
    assert old_fetched.status == MemoryStatus.SUPERSEDED
    assert old_fetched.superseded_by_id == v2.id

    # History contains both versions
    history = adapter.get_memories_by_logical_fact("fact_db_01", repo_id=repo_id)
    assert len(history) == 2


def test_falkordb_file_references_and_status_update(mock_falkordb_adapter):
    """Verify get_memories_referencing_file and update_memory_status."""
    adapter, _ = mock_falkordb_adapter
    repo_id = "repo_test"

    ev = EvidenceRecord(repository_id=repo_id, file_path="src/router.py")
    mem = MemoryRecord(
        repository_id=repo_id,
        claim="Router dispatches requests to controllers",
        status=MemoryStatus.OBSERVED,
    )
    adapter.save_memory(mem, [ev])

    ref_memories = adapter.get_memories_referencing_file(repo_id=repo_id, file_path="src/router.py")
    assert len(ref_memories) == 1
    assert ref_memories[0].id == mem.id

    # Update status to STALE
    adapter.update_memory_status(mem.id, MemoryStatus.STALE)
    stale_mem = adapter.get_memory(mem.id, repo_id=repo_id)
    assert stale_mem.status == MemoryStatus.STALE


def test_falkordb_clear(mock_falkordb_adapter):
    """Verify clear removes graph data."""
    adapter, _ = mock_falkordb_adapter
    repo_id = "repo_clean"
    adapter.upsert_repository(RepositoryInfo(repository_id=repo_id, root_path="/clean"))
    mem = MemoryRecord(repository_id=repo_id, claim="Temp claim")
    adapter.save_memory(mem, [])

    adapter.clear(repo_id=repo_id)
    assert adapter.get_repository(repo_id) is None
    assert adapter.get_memory(mem.id, repo_id=repo_id) is None
