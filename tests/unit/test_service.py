"""Unit tests for MemoryService core domain engine."""

import os
import tempfile
import pytest

from codemem.core.models import (
    EvidenceInput,
    EvidenceKind,
    MemoryInput,
    MemoryStatus,
    MemoryType,
    RepositoryInfo,
)
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore


@pytest.fixture
def temp_repo():
    """Create a temporary repository directory with dummy source files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a sample python file with a class and method
        auth_dir = os.path.join(tmp_dir, "src", "auth")
        os.makedirs(auth_dir, exist_ok=True)
        auth_file = os.path.join(auth_dir, "jwt.py")
        with open(auth_file, "w", encoding="utf-8") as f:
            f.write(
                "class TokenValidator:\n"
                "    def verify_token(self, token):\n"
                "        return token == 'valid'\n"
            )

        yield tmp_dir, auth_file


def test_register_repository(temp_repo):
    """Verify repository registration via MemoryService."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())

    repo_info = service.register_repository(repo_path)
    assert repo_info.repository_id is not None
    assert len(repo_info.repository_id) >= 12
    assert os.path.samefile(repo_info.root_path, repo_path)


def test_remember_with_verified_test(temp_repo):
    """Verify claim backed by passing test achieves VERIFIED status."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    mem_input = MemoryInput(
        repository_id=repo_info.repository_id,
        claim="TokenValidator passes all unit tests",
        evidence=[
            EvidenceInput(
                file_path="src/auth/jwt.py",
                test_command="pytest tests/test_jwt.py",
                test_passed=True,
            )
        ],
    )
    record = service.remember(mem_input)
    assert record.status == MemoryStatus.VERIFIED
    assert record.confidence_score >= 0.95


def test_remember_with_ast_evidence(temp_repo):
    """Verify claim backed by existing AST symbol achieves OBSERVED status."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    mem_input = MemoryInput(
        repository_id=repo_info.repository_id,
        claim="TokenValidator contains verify_token method",
        evidence=[
            EvidenceInput(
                file_path="src/auth/jwt.py",
                symbol="TokenValidator.verify_token",
            )
        ],
    )
    record = service.remember(mem_input)
    assert record.status == MemoryStatus.OBSERVED
    assert len(record.evidence_records) == 1
    assert record.evidence_records[0].content_hash is not None


def test_remember_unanchored_claim(temp_repo):
    """Verify claim with no valid code evidence receives INFERRED status."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    mem_input = MemoryInput(
        repository_id=repo_info.repository_id,
        claim="System might be rewritten in Rust someday",
        evidence=[],
    )
    record = service.remember(mem_input)
    assert record.status == MemoryStatus.INFERRED
    assert record.confidence_score <= 0.65


def test_recall_and_abstention(temp_repo):
    """Verify recall retrieves relevant memories and abstains when evidence is missing."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="Authentication middleware validates JWT tokens",
            tags=["auth", "jwt"],
            evidence=[
                EvidenceInput(
                    file_path="src/auth/jwt.py",
                    symbol="TokenValidator.verify_token",
                )
            ],
        )
    )

    # 1. Query matching stored knowledge
    recall_resp = service.recall(repo_id=repo_info.repository_id, query="Where is JWT authentication validated?")
    assert recall_resp.abstained is False
    assert len(recall_resp.results) == 1
    assert "JWT" in recall_resp.results[0].memory.claim
    assert recall_resp.results[0].final_score > 0.45

    # 2. Query for completely absent knowledge -> Explicit Abstention (LongMemEval)
    empty_resp = service.recall(repo_id=repo_info.repository_id, query="How does the Kubernetes ingress controller work?")
    assert empty_resp.abstained is True
    assert "No verified or current evidence" in empty_resp.abstain_reason


def test_update_and_history(temp_repo):
    """Verify updating a fact supersedes the old version and preserves revision history."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    # Version 1
    v1 = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="Tokens expire after 15 minutes",
        )
    )

    # Version 2
    v2 = service.update(
        memory_id=v1.id,
        new_claim="Tokens expire after 60 minutes",
        reason="Security policy extended token lifetime",
        repo_id=repo_info.repository_id,
    )

    assert v2.logical_fact_id == v1.logical_fact_id
    assert v2.id != v1.id
    assert v2.supersedes_id == v1.id

    # Verify history chain
    hist = service.history(identifier=v1.id, repo_id=repo_info.repository_id)
    assert len(hist.versions) == 2
    assert hist.current_version_id == v2.id


def test_check_changes_flags_stale_evidence(temp_repo):
    """Verify modifying a referenced evidence file flags the memory as STALE."""
    repo_path, auth_file = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    mem = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="TokenValidator checks valid tokens",
            evidence=[
                EvidenceInput(
                    file_path="src/auth/jwt.py",
                    symbol="TokenValidator.verify_token",
                )
            ],
        )
    )
    assert mem.status == MemoryStatus.OBSERVED

    # Modify the referenced source file
    with open(auth_file, "a", encoding="utf-8") as f:
        f.write("\n# Added new security comment changing hash\n")

    # Run change detection
    changes = service.check_changes(repo_id=repo_info.repository_id)
    assert changes.stale_count == 1
    assert changes.affected_memories[0].id == mem.id
    assert changes.affected_memories[0].status == MemoryStatus.STALE
    assert "src/auth/jwt.py" in changes.changed_files


def test_remember_idempotent_deduplication(temp_repo):
    """Verify writing the same fact twice is idempotent and does not create duplicate nodes."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    mem_in1 = MemoryInput(
        repository_id=repo_info.repository_id,
        claim="Authentication middleware validates JWT tokens",
        subject="AuthMiddleware",
        predicate="validates",
        object="JWT tokens",
    )
    v1 = service.remember(mem_in1)

    # Write the identical claim again with slightly different casing/whitespace
    mem_in2 = MemoryInput(
        repository_id=repo_info.repository_id,
        claim="Authentication middleware   validates  JWT tokens! ",
        subject="AuthMiddleware",
        predicate="validates",
        object="JWT tokens",
    )
    v2 = service.remember(mem_in2)

    assert v1.id == v2.id
    all_mems = service.store.get_all_memories(repo_info.repository_id)
    assert len(all_mems) == 1


def test_remember_deduplication_upgrades_status(temp_repo):
    """Verify re-submitting an existing claim with test verification upgrades its status."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    v1 = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="Database connects via connection pool",
        )
    )
    assert v1.status == MemoryStatus.INFERRED

    # Submit same claim with passing test
    v2 = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="Database connects via connection pool",
            evidence=[
                EvidenceInput(
                    file_path="src/auth/jwt.py",
                    test_command="pytest tests/test_db.py",
                    test_passed=True,
                )
            ],
        )
    )
    assert v1.id == v2.id
    assert v2.status == MemoryStatus.VERIFIED
    assert len(service.store.get_all_memories(repo_info.repository_id)) == 1


def test_remember_conflict_detection(temp_repo):
    """Verify facts with same subject+predicate but conflicting object are linked via contradicts_id."""
    repo_path, _ = temp_repo
    service = MemoryService(store=InMemoryGraphStore())
    repo_info = service.register_repository(repo_path)

    f1 = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="AuthMiddleware uses JWT tokens",
            subject="AuthMiddleware",
            predicate="uses",
            object="JWT tokens",
        )
    )

    f2 = service.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="AuthMiddleware uses SessionCookies",
            subject="AuthMiddleware",
            predicate="uses",
            object="SessionCookies",
        )
    )

    assert f1.id != f2.id
    assert f2.contradicts_id == f1.id
    assert len(service.store.get_all_memories(repo_info.repository_id)) == 2


def test_repository_scoping_isolation(temp_repo):
    """Verify strict multi-repository isolation and cross-repo scope violation protection."""
    repo_path, _ = temp_repo
    store = InMemoryGraphStore()
    service = MemoryService(store=store)

    repo_a = service.register_repository(repo_path)
    # Register a second distinct repository scope
    repo_b = store.upsert_repository(
        RepositoryInfo(
            repository_id="repo-secondary-scope-456",
            repo_name="secondary-repo",
            root_path=tempfile.mkdtemp(),
            head_commit="b" * 40,
        )
    )

    # 1. Remember in repo_a
    mem_a = service.remember(
        MemoryInput(
            repository_id=repo_a.repository_id,
            claim="Service A connects to Postgres database",
            subject="Service A",
            predicate="connects",
            object="Postgres",
            tags=["postgres", "database"],
        )
    )

    # 2. Recall in repo_b must NOT see memories from repo_a
    recall_b = service.recall(query="Postgres database", repo_id=repo_b.repository_id)
    assert recall_b.abstained is True
    assert len(recall_b.results) == 0

    # 3. Attempt to update mem_a under repo_b scope must raise PermissionError (Scope Violation)
    with pytest.raises(PermissionError) as exc_update:
        service.update(
            memory_id=mem_a.id,
            new_claim="Service A connects to MySQL",
            reason="Migration",
            repo_id=repo_b.repository_id,
        )
    assert "Scope violation" in str(exc_update.value)

    # 4. Attempt to query history of mem_a under repo_b scope must raise PermissionError
    with pytest.raises(PermissionError) as exc_hist:
        service.history(identifier=mem_a.id, repo_id=repo_b.repository_id)
    assert "Scope violation" in str(exc_hist.value)

    # 5. Attempt to explain mem_a under repo_b scope must raise PermissionError
    with pytest.raises(PermissionError) as exc_explain:
        service.explain(memory_id=mem_a.id, repo_id=repo_b.repository_id)
    assert "Scope violation" in str(exc_explain.value)


def test_persistence_across_restart(temp_repo):
    """Verify state persistence across process restart: saving to disk, rehydrating, and querying."""
    from pathlib import Path
    from codemem.cli.main import save_persistent_store, load_persistent_store

    repo_path, _ = temp_repo
    store1 = InMemoryGraphStore()
    service1 = MemoryService(store=store1)
    repo_info = service1.register_repository(repo_path)

    # 1. Add v1
    v1 = service1.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="AuthMiddleware verifies JWT token",
            subject="AuthMiddleware",
            predicate="verifies",
            object="JWT token",
            tags=["auth"],
            evidence=[
                EvidenceInput(
                    file_path="src/auth/jwt.py",
                    test_command="pytest tests/test_auth.py",
                    test_passed=True,
                )
            ],
        )
    )
    assert v1.status == MemoryStatus.VERIFIED

    # 2. Supersede v1 with v2
    v2 = service1.update(
        memory_id=v1.id,
        new_claim="AuthMiddleware verifies Ed25519 tokens",
        reason="Upgraded cryptographic algorithm",
        repo_id=repo_info.repository_id,
    )
    assert v2.supersedes_id == v1.id

    # 3. Add conflicting fact
    v_conflict = service1.remember(
        MemoryInput(
            repository_id=repo_info.repository_id,
            claim="AuthMiddleware verifies SessionCookies",
            subject="AuthMiddleware",
            predicate="verifies",
            object="SessionCookies",
        )
    )
    assert v_conflict.contradicts_id is not None

    # 4. Persist to file
    state_file = Path(tempfile.mkdtemp()) / "store.json"
    save_persistent_store(store1, state_file)
    assert state_file.is_file()

    # 5. Simulate clean restart: drop store1 completely and re-instantiate from file
    del service1
    del store1

    store2 = load_persistent_store(state_file)
    service2 = MemoryService(store=store2)

    # Verify repository restored
    restored_repo = store2.get_repository(repo_info.repository_id)
    assert restored_repo is not None
    assert restored_repo.repository_id == repo_info.repository_id

    # Verify memories restored
    all_mems = store2.get_all_memories(repo_info.repository_id, include_superseded=True)
    assert len(all_mems) == 3

    # Verify supersession edge restored
    hist = service2.history(identifier=v2.id, repo_id=repo_info.repository_id)
    assert len(hist.versions) == 2
    assert hist.current_version_id == v2.id

    # Verify contradiction edge restored
    assert store2._contradicts_edges.get(v_conflict.id) == v_conflict.contradicts_id

    # Verify recall works on restored store
    recall_resp = service2.recall(query="Ed25519 tokens", repo_id=repo_info.repository_id)
    assert recall_resp.abstained is False
    assert len(recall_resp.results) > 0
    assert recall_resp.results[0].memory.id == v2.id


