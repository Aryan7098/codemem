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
