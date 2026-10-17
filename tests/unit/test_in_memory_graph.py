"""Unit tests for InMemoryGraphStore implementing GraphStoreProtocol."""

from codemem.core.models import (
    EvidenceRecord,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
)
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.graph.protocol import GraphStoreProtocol


def test_in_memory_store_satisfies_protocol():
    """Verify InMemoryGraphStore conforms to GraphStoreProtocol."""
    store = InMemoryGraphStore()
    assert isinstance(store, GraphStoreProtocol)


def test_repository_upsert_and_retrieval():
    """Verify repository registration and retrieval."""
    store = InMemoryGraphStore()
    repo = RepositoryInfo(
        repository_id="repo_12345",
        root_path="C:/Projects/test-repo",
        head_commit="abc1234",
    )
    store.upsert_repository(repo)

    fetched = store.get_repository("repo_12345")
    assert fetched is not None
    assert fetched.repository_id == "repo_12345"
    assert fetched.root_path == "C:/Projects/test-repo"


def test_memory_storage_and_scope_isolation():
    """Verify memories are strictly isolated by repository ID."""
    store = InMemoryGraphStore()
    ev1 = EvidenceRecord(
        repository_id="repo_A",
        file_path="src/auth.py",
        content_hash="h1",
    )
    mem_a = MemoryRecord(
        repository_id="repo_A",
        claim="Authentication uses JWT tokens",
        status=MemoryStatus.OBSERVED,
    )
    store.save_memory(mem_a, [ev1])

    # Query for repo_A succeeds
    res_a = store.query_memories_by_terms(repo_id="repo_A", terms=["jwt", "auth"])
    assert len(res_a) == 1
    assert res_a[0].id == mem_a.id
    assert len(res_a[0].evidence_records) == 1

    # Query for repo_B yields zero results (Isolation Guarantee)
    res_b = store.query_memories_by_terms(repo_id="repo_B", terms=["jwt", "auth"])
    assert len(res_b) == 0


def test_supersede_memory_lifecycle():
    """Verify superseding marks old version SUPERSEDED and establishes link."""
    store = InMemoryGraphStore()
    mem_v1 = MemoryRecord(
        repository_id="repo_A",
        logical_fact_id="fact_001",
        claim="Database uses SQLite for testing",
        status=MemoryStatus.OBSERVED,
    )
    store.save_memory(mem_v1, [])

    mem_v2 = MemoryRecord(
        repository_id="repo_A",
        logical_fact_id="fact_001",
        claim="Database uses FalkorDB for testing",
        status=MemoryStatus.VERIFIED,
    )
    store.save_memory(mem_v2, [])
    store.supersede_memory(
        old_memory_id=mem_v1.id,
        new_memory=mem_v2,
        reason="Upgraded from SQLite to FalkorDB",
    )

    # Old memory is SUPERSEDED
    old_fetched = store.get_memory(mem_v1.id)
    assert old_fetched.status == MemoryStatus.SUPERSEDED
    assert old_fetched.superseded_by_id == mem_v2.id
    assert mem_v2.supersedes_id == mem_v1.id

    # Default query returns only active mem_v2, not superseded mem_v1
    active = store.query_memories_by_terms(repo_id="repo_A", terms=["database"])
    assert len(active) == 1
    assert active[0].id == mem_v2.id

    # History returns both versions
    history = store.get_memories_by_logical_fact("fact_001", "repo_A")
    assert len(history) == 2


def test_concept_graph_traversal():
    """Verify bounded concept traversal expands synonyms across the graph."""
    store = InMemoryGraphStore()
    mem = MemoryRecord(
        repository_id="repo_A",
        claim="Authentication verified via JWT headers",
        tags=["auth"],
    )
    store.save_memory(mem, [])

    # Querying for "login" expands to "auth" via concept alias graph (max_hops=2)
    traversed = store.traverse_concept_graph(repo_id="repo_A", concepts=["login"], max_hops=2)
    assert len(traversed) == 1
    assert traversed[0].id == mem.id


def test_memories_referencing_file():
    """Verify finding memories supported by a specific file."""
    store = InMemoryGraphStore()
    ev = EvidenceRecord(
        repository_id="repo_A",
        file_path="src/payments.py",
        content_hash="hash123",
    )
    mem = MemoryRecord(
        repository_id="repo_A",
        claim="Payments use Stripe API",
    )
    store.save_memory(mem, [ev])

    referencing = store.get_memories_referencing_file("repo_A", "src/payments.py")
    assert len(referencing) == 1
    assert referencing[0].id == mem.id

    non_referencing = store.get_memories_referencing_file("repo_A", "src/other.py")
    assert len(non_referencing) == 0
