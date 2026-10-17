"""Data models and schemas for CodeMem memory engine."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePath
from typing import Any, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class MemoryStatus(str, Enum):
    """Controlled lifecycle statuses for memory records."""

    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    CONTRADICTED = "CONTRADICTED"


class MemoryType(str, Enum):
    """Categorical types of codebase knowledge."""

    ARCHITECTURE = "architecture"
    DEPENDENCY = "dependency"
    API_CONTRACT = "api_contract"
    DECISION = "decision"
    CONVENTION = "convention"
    BUG = "bug"
    HANDOFF = "handoff"


class EvidenceKind(str, Enum):
    """Types of grounding evidence supporting a claim."""

    SOURCE_SPAN = "source_span"
    FILE_SYMBOL = "file_symbol"
    COMMIT = "commit"
    TEST_RESULT = "test_result"
    COMMAND_OUTPUT = "command_output"


# ------------------------------------------------------------------------------
# Evidence Models
# ------------------------------------------------------------------------------


class EvidenceInput(BaseModel):
    """Input payload for attaching evidence to a claim."""

    file_path: str = Field(..., description="Path relative to repository root")
    kind: EvidenceKind = Field(default=EvidenceKind.SOURCE_SPAN)
    symbol: Optional[str] = Field(None, description="AST symbol (e.g. ClassName.method_name)")
    line_start: Optional[int] = Field(None, ge=1)
    line_end: Optional[int] = Field(None, ge=1)
    excerpt: Optional[str] = Field(None, max_length=2000, description="Exact code snippet")
    content_hash: Optional[str] = Field(None, description="SHA-256 hash of the snippet or file content")
    commit_hash: Optional[str] = Field(None, description="Git commit hash when observed")
    test_command: Optional[str] = Field(None, description="Verification command (e.g. pytest tests/test_auth.py)")
    test_passed: Optional[bool] = Field(None, description="Whether verification command succeeded")

    @field_validator("file_path")
    @classmethod
    def validate_safe_relative_path(cls, v: str) -> str:
        """Ensure file path is relative and does not escape the repository root."""
        clean = v.replace("\\", "/").strip()
        path = PurePath(clean)
        if clean.startswith("/") or ":" in clean or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Path must be relative to repository root and cannot contain '..' or root prefixes: {v}")
        return clean

    @field_validator("line_end")
    @classmethod
    def validate_line_order(cls, v: Optional[int], info: Any) -> Optional[int]:
        """Ensure line_end >= line_start if both are supplied."""
        line_start = info.data.get("line_start")
        if v is not None and line_start is not None and v < line_start:
            raise ValueError(f"line_end ({v}) cannot be less than line_start ({line_start})")
        return v


class EvidenceRecord(EvidenceInput):
    """Persisted evidence entity with graph identity and timestamps."""

    id: str = Field(default_factory=lambda: f"evi_{uuid4().hex[:12]}")
    repository_id: str
    observed_at: str = Field(default_factory=_utc_now_iso)


# ------------------------------------------------------------------------------
# Memory Models
# ------------------------------------------------------------------------------


class MemoryInput(BaseModel):
    """Input payload for creating or proposing a new memory."""

    repository_id: str = Field(..., min_length=1, max_length=64)
    claim: str = Field(..., min_length=3, max_length=1000, description="Clear factual statement")
    subject: Optional[str] = Field(None, max_length=256, description="Component, module, or entity")
    predicate: Optional[str] = Field(None, max_length=128, description="Relation or behavior")
    object: Optional[str] = Field(None, max_length=512, description="Target or outcome")
    memory_type: MemoryType = Field(default=MemoryType.ARCHITECTURE)
    confidence_score: float = Field(default=0.85, ge=0.0, le=1.0)
    source_agent_id: str = Field(default="unknown-agent", max_length=128)
    source_session_id: str = Field(default="default-session", max_length=128)
    workspace_id: str = Field(default="default", max_length=64)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    evidence: List[EvidenceInput] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    """Complete versioned memory entity in the graph."""

    id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:12]}")
    logical_fact_id: str = Field(default_factory=lambda: f"fact_{uuid4().hex[:12]}")
    repository_id: str
    workspace_id: str = "default"
    claim: str
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    memory_type: MemoryType = MemoryType.ARCHITECTURE
    status: MemoryStatus = MemoryStatus.OBSERVED
    confidence_score: float = Field(default=0.85, ge=0.0, le=1.0)
    source_agent_id: str = "unknown-agent"
    source_session_id: str = "default-session"
    source_commit: Optional[str] = None
    recorded_at: str = Field(default_factory=_utc_now_iso)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None
    contradicts_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    evidence_records: List[EvidenceRecord] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# Repository & Scoping Models
# ------------------------------------------------------------------------------


class RepositoryInfo(BaseModel):
    """Metadata describing a registered codebase scope."""

    repository_id: str = Field(..., description="Stable 16-character identifier")
    root_path: str = Field(..., description="Canonical absolute path on local system")
    head_commit: Optional[str] = None
    remote_url: Optional[str] = None
    git_available: bool = True
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)


# ------------------------------------------------------------------------------
# Response & Query Models
# ------------------------------------------------------------------------------


class ScoredMemoryRecord(BaseModel):
    """Retrieved memory enriched with relevance and freshness scoring."""

    memory: MemoryRecord
    final_score: float = Field(..., ge=0.0, le=1.0)
    match_score: float = Field(..., ge=0.0, le=1.0)
    status_weight: float = Field(..., ge=0.0, le=1.0)
    freshness_multiplier: float = Field(..., ge=0.0, le=1.0)
    staleness_warning: Optional[str] = None


class MemoryRecallResponse(BaseModel):
    """Structured response for memory recall queries."""

    repository_id: str
    query: str
    results: List[ScoredMemoryRecord] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: Optional[str] = None
    total_candidates_examined: int = 0


class MemoryExplanation(BaseModel):
    """Inspectable provenance and evidence breakdown for a claim."""

    memory: MemoryRecord
    evidence_records: List[EvidenceRecord] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    freshness_status: str
    changed_evidence_files: List[str] = Field(default_factory=list)


class MemoryHistoryResponse(BaseModel):
    """Chronological revision chain for a logical fact."""

    logical_fact_id: str
    repository_id: str
    current_version_id: Optional[str] = None
    versions: List[MemoryRecord] = Field(default_factory=list)
    change_reasons: List[dict[str, Any]] = Field(default_factory=list)


class ChangeDetectionResponse(BaseModel):
    """Summary of repository source changes and affected stale memories."""

    repository_id: str
    checked_at: str = Field(default_factory=_utc_now_iso)
    stale_count: int = 0
    affected_memories: List[MemoryRecord] = Field(default_factory=list)
    changed_files: List[str] = Field(default_factory=list)
    revalidation_recommendations: List[str] = Field(default_factory=list)


class CodeMemErrorDetail(BaseModel):
    """Standardized error contract for CLI and MCP responses."""

    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
