"""Core domain layer for CodeMem memory engine."""

from codemem.core.models import (
    AgentHandoffRecord,
    ChangeDetectionResponse,
    CodeMemErrorDetail,
    EvidenceInput,
    EvidenceKind,
    EvidenceRecord,
    HandoffInput,
    HandoffStatus,
    MemoryExplanation,
    MemoryHistoryResponse,
    MemoryInput,
    MemoryRecallResponse,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    RepositoryInfo,
    ScanReport,
    ScoredMemoryRecord,
)

from codemem.core.service import MemoryService

__all__ = [
    "AgentHandoffRecord",
    "ChangeDetectionResponse",
    "CodeMemErrorDetail",
    "EvidenceInput",
    "EvidenceKind",
    "EvidenceRecord",
    "HandoffInput",
    "HandoffStatus",
    "MemoryExplanation",
    "MemoryHistoryResponse",
    "MemoryInput",
    "MemoryRecallResponse",
    "MemoryRecord",
    "MemoryService",
    "MemoryStatus",
    "MemoryType",
    "RepositoryInfo",
    "ScanReport",
    "ScoredMemoryRecord",
]
