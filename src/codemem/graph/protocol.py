"""Abstract Protocol defining graph storage operations for CodeMem."""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from codemem.core.models import (
    EvidenceRecord,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
)


@runtime_checkable
class GraphStoreProtocol(Protocol):
    """Interface that both FalkorDBAdapter and InMemoryGraphStore must implement."""

    def upsert_repository(self, repo: RepositoryInfo) -> RepositoryInfo:
        """Create or update a repository scope node."""
        ...

    def get_repository(self, repo_id: str) -> Optional[RepositoryInfo]:
        """Fetch repository metadata by ID."""
        ...

    def save_memory(
        self,
        memory: MemoryRecord,
        evidence_records: List[EvidenceRecord],
    ) -> MemoryRecord:
        """Store a memory node, its evidence nodes, and establish graph edges."""
        ...

    def get_memory(self, memory_id: str, repo_id: Optional[str] = None) -> Optional[MemoryRecord]:
        """Retrieve a specific memory version, optionally verifying repo scope."""
        ...

    def get_memories_by_logical_fact(
        self,
        logical_fact_id: str,
        repo_id: str,
    ) -> List[MemoryRecord]:
        """Retrieve all versions of a logical fact ordered chronologically."""
        ...

    def supersede_memory(
        self,
        old_memory_id: str,
        new_memory: MemoryRecord,
        reason: str,
    ) -> None:
        """Establish SUPERSEDES edge from new to old memory and transition old status."""
        ...

    def get_all_memories(
        self,
        repo_id: str,
        include_superseded: bool = False,
    ) -> List[MemoryRecord]:
        """Retrieve all memories belonging to a repository scope."""
        ...

    def query_memories_by_terms(
        self,
        repo_id: str,
        terms: List[str],
        include_stale: bool = False,
        top_k: int = 5,
    ) -> List[MemoryRecord]:
        """Retrieve active memories matching search terms within repository scope."""
        ...

    def traverse_concept_graph(
        self,
        repo_id: str,
        concepts: List[str],
        max_hops: int = 2,
    ) -> List[MemoryRecord]:
        """Traverse concept-memory relationships bounded by max_hops."""
        ...

    def get_memories_referencing_file(
        self,
        repo_id: str,
        file_path: str,
    ) -> List[MemoryRecord]:
        """Find all active memories supported by evidence from a specific file."""
        ...

    def update_memory_status(self, memory_id: str, status: MemoryStatus) -> None:
        """Update the lifecycle status of a specific memory version."""
        ...

    def clear(self, repo_id: Optional[str] = None) -> None:
        """Clear graph data (for isolated test teardowns)."""
        ...
