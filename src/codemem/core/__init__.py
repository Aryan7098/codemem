"""Core domain layer for CodeMem memory engine."""

from codemem.core.models import (
    ChangeDetectionResponse,
    CodeMemErrorDetail,
    EvidenceInput,
    EvidenceKind,
    EvidenceRecord,
    MemoryExplanation,
    MemoryHistoryResponse,
    MemoryInput,
    MemoryRecallResponse,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    RepositoryInfo,
    ScoredMemoryRecord,
)

from codemem.core.service import MemoryService

__all__ = [
    "ChangeDetectionResponse",
    "CodeMemErrorDetail",
    "EvidenceInput",
    "EvidenceKind",
    "EvidenceRecord",
    "MemoryExplanation",
    "MemoryHistoryResponse",
    "MemoryInput",
    "MemoryRecallResponse",
    "MemoryRecord",
    "MemoryService",
    "MemoryStatus",
    "MemoryType",
    "RepositoryInfo",
    "ScoredMemoryRecord",
]
