"""Unit tests for CodeMem core Pydantic data models."""

import pytest
from pydantic import ValidationError

from codemem.core.models import (
    EvidenceInput,
    EvidenceKind,
    EvidenceRecord,
    MemoryInput,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    RepositoryInfo,
    ScoredMemoryRecord,
)


def test_evidence_input_valid():
    """Verify valid evidence input parses cleanly."""
    evi = EvidenceInput(
        file_path="src/auth/jwt.py",
        kind=EvidenceKind.SOURCE_SPAN,
        symbol="TokenValidator.verify",
        line_start=10,
        line_end=25,
        excerpt="def verify(token): return True",
        content_hash="a" * 64,
    )
    assert evi.file_path == "src/auth/jwt.py"
    assert evi.line_start == 10
    assert evi.line_end == 25


def test_evidence_input_rejects_escaping_paths():
    """Ensure paths containing '..' or leading slashes are rejected for safety."""
    with pytest.raises(ValidationError) as exc:
        EvidenceInput(file_path="../secret.py")
    assert "cannot contain '..'" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        EvidenceInput(file_path="/etc/passwd")
    assert "must be relative" in str(exc.value)


def test_evidence_input_rejects_inverted_lines():
    """Ensure line_end cannot precede line_start."""
    with pytest.raises(ValidationError) as exc:
        EvidenceInput(file_path="src/app.py", line_start=50, line_end=20)
    assert "cannot be less than line_start" in str(exc.value)


def test_memory_input_validation():
    """Verify memory input enforces required fields and valid enums."""
    mem_in = MemoryInput(
        repository_id="repo_1234567890",
        claim="Authentication middleware validates JWT tokens",
        subject="AuthMiddleware",
        predicate="validates",
        object="JWT tokens",
        memory_type=MemoryType.ARCHITECTURE,
        confidence_score=0.9,
    )
    assert mem_in.repository_id == "repo_1234567890"
    assert mem_in.confidence_score == 0.9

    # Test confidence score bounds
    with pytest.raises(ValidationError):
        MemoryInput(
            repository_id="repo_123",
            claim="Valid claim text",
            confidence_score=1.5,
        )


def test_memory_record_serialization():
    """Verify MemoryRecord serialization and deserialization roundtrip."""
    evidence = EvidenceRecord(
        repository_id="repo_abc",
        file_path="src/tokens.py",
        kind=EvidenceKind.SOURCE_SPAN,
        content_hash="b" * 64,
    )
    record = MemoryRecord(
        repository_id="repo_abc",
        claim="Tokens expire after 1 hour",
        status=MemoryStatus.OBSERVED,
        confidence_score=0.85,
        evidence_records=[evidence],
    )

    data = record.model_dump()
    assert data["status"] == "OBSERVED"
    assert len(data["evidence_records"]) == 1

    restored = MemoryRecord.model_validate(data)
    assert restored.id == record.id
    assert restored.claim == "Tokens expire after 1 hour"
    assert restored.evidence_records[0].file_path == "src/tokens.py"


def test_scored_memory_record_bounds():
    """Verify ScoredMemoryRecord checks score boundaries (0.0 to 1.0)."""
    record = MemoryRecord(
        repository_id="repo_abc",
        claim="Sample claim",
        status=MemoryStatus.VERIFIED,
    )
    scored = ScoredMemoryRecord(
        memory=record,
        final_score=0.85,
        match_score=0.90,
        status_weight=1.0,
        freshness_multiplier=0.95,
    )
    assert scored.final_score == 0.85

    with pytest.raises(ValidationError):
        ScoredMemoryRecord(
            memory=record,
            final_score=1.2,
            match_score=0.9,
            status_weight=1.0,
            freshness_multiplier=1.0,
        )
