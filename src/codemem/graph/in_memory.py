"""In-memory graph store implementation of GraphStoreProtocol for zero-dependency execution."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from codemem.core.models import (
    AgentHandoffRecord,
    EvidenceRecord,
    HandoffStatus,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
    _utc_now_iso,
)


class InMemoryGraphStore:
    """In-process thread-safe graph store double implementing GraphStoreProtocol."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._repositories: Dict[str, RepositoryInfo] = {}
        self._memories: Dict[str, MemoryRecord] = {}
        self._evidence: Dict[str, EvidenceRecord] = {}
        self._handoffs: Dict[str, AgentHandoffRecord] = {}

        # Graph Edges
        # (:Memory)-[:SUPPORTED_BY]->(:Evidence)
        self._memory_to_evidence: Dict[str, Set[str]] = {}
        # (:Memory)-[:SUPERSEDES]->(:Memory)  new_id -> (old_id, reason, timestamp)
        self._supersedes_edges: Dict[str, Tuple[str, str, str]] = {}
        # (:Memory)-[:CONTRADICTS]->(:Memory)  source_id -> target_id
        self._contradicts_edges: Dict[str, str] = {}
        # (:Concept)-[:RELATES_TO]->(:Concept)
        self._concept_aliases: Dict[str, Set[str]] = {
            "login": {"auth", "authentication", "token", "jwt", "session"},
            "auth": {"login", "authentication", "token", "jwt", "credential"},
            "database": {"falkordb", "redis", "postgres", "sql", "storage"},
            "api": {"endpoint", "route", "http", "contract"},
        }
        # (:Memory)-[:TAGGED_WITH]->(:Concept)
        self._concept_to_memories: Dict[str, Set[str]] = {}

    def upsert_repository(self, repo: RepositoryInfo) -> RepositoryInfo:
        """Create or update a repository node."""
        with self._lock:
            self._repositories[repo.repository_id] = repo
            return repo

    def get_repository(self, repo_id: str) -> Optional[RepositoryInfo]:
        """Fetch repository metadata by ID."""
        with self._lock:
            return self._repositories.get(repo_id)

    def save_memory(
        self,
        memory: MemoryRecord,
        evidence_records: List[EvidenceRecord],
    ) -> MemoryRecord:
        """Store a memory node, its evidence nodes, and establish graph edges."""
        with self._lock:
            # Attach evidence to record and graph index
            memory.evidence_records = list(evidence_records)
            self._memories[memory.id] = memory

            if memory.id not in self._memory_to_evidence:
                self._memory_to_evidence[memory.id] = set()

            for ev in evidence_records:
                self._evidence[ev.id] = ev
                self._memory_to_evidence[memory.id].add(ev.id)

            # Record conflict edge if present
            if memory.contradicts_id:
                self._contradicts_edges[memory.id] = memory.contradicts_id

            # Index tags/concepts
            for tag in memory.tags:
                tag_clean = tag.lower().strip()
                if tag_clean not in self._concept_to_memories:
                    self._concept_to_memories[tag_clean] = set()
                self._concept_to_memories[tag_clean].add(memory.id)

            return memory

    def get_memory(self, memory_id: str, repo_id: Optional[str] = None) -> Optional[MemoryRecord]:
        """Retrieve a specific memory version, optionally verifying repo scope."""
        with self._lock:
            mem = self._memories.get(memory_id)
            if mem is None:
                return None
            if repo_id is not None and mem.repository_id != repo_id:
                return None
            return mem

    def get_memories_by_logical_fact(
        self,
        logical_fact_id: str,
        repo_id: str,
    ) -> List[MemoryRecord]:
        """Retrieve all versions of a logical fact ordered chronologically."""
        with self._lock:
            versions = [
                m for m in self._memories.values()
                if m.logical_fact_id == logical_fact_id and m.repository_id == repo_id
            ]
            # Sort chronologically by recorded_at
            return sorted(versions, key=lambda m: m.recorded_at, reverse=True)

    def supersede_memory(
        self,
        old_memory_id: str,
        new_memory: MemoryRecord,
        reason: str,
    ) -> None:
        """Establish SUPERSEDES edge from new to old memory and transition old status."""
        with self._lock:
            old_mem = self._memories.get(old_memory_id)
            if old_mem is not None:
                old_mem.status = MemoryStatus.SUPERSEDED
                old_mem.superseded_by_id = new_memory.id
                new_memory.supersedes_id = old_memory_id
                now_iso = datetime.now(timezone.utc).isoformat()
                self._supersedes_edges[new_memory.id] = (old_memory_id, reason, now_iso)

    def get_all_memories(
        self,
        repo_id: str,
        include_superseded: bool = False,
    ) -> List[MemoryRecord]:
        """Retrieve all memories belonging to a repository scope."""
        with self._lock:
            return [
                m for m in self._memories.values()
                if m.repository_id == repo_id and (include_superseded or m.status != MemoryStatus.SUPERSEDED)
            ]

    def query_memories_by_terms(
        self,
        repo_id: str,
        terms: List[str],
        include_stale: bool = False,
        top_k: int = 5,
    ) -> List[MemoryRecord]:
        """Retrieve active memories matching search terms within repository scope."""
        with self._lock:
            lowered_terms = [t.lower() for t in terms if len(t) > 1]
            matches: List[Tuple[MemoryRecord, int]] = []

            for m in list(self._memories.values()):
                # Strict repository isolation filter
                if m.repository_id != repo_id:
                    continue

                # Exclude superseded or contradicted unless explicitly requested
                if m.status in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
                    continue

                # Exclude stale if include_stale is False
                if m.status == MemoryStatus.STALE and not include_stale:
                    continue

                searchable_text = f"{m.claim} {m.subject or ''} {m.predicate or ''} {m.object or ''} {' '.join(m.tags)}".lower()
                hit_count = sum(1 for term in lowered_terms if term in searchable_text)

                # Also check evidence paths / excerpts
                if hit_count == 0:
                    for ev in m.evidence_records:
                        ev_text = f"{ev.file_path} {ev.symbol or ''} {ev.excerpt or ''}".lower()
                        if any(term in ev_text for term in lowered_terms):
                            hit_count += 1
                            break

                if hit_count > 0:
                    matches.append((m, hit_count))

            # Sort by match count descending, recorded_at descending
            matches.sort(key=lambda item: (item[1], item[0].recorded_at), reverse=True)
            return [item[0] for item in matches[:top_k]]

    def traverse_concept_graph(
        self,
        repo_id: str,
        concepts: List[str],
        max_hops: int = 2,
    ) -> List[MemoryRecord]:
        """Traverse concept-memory relationships bounded by max_hops."""
        with self._lock:
            reachable_concepts: Set[str] = set()
            for c in concepts:
                c_low = c.lower().strip()
                reachable_concepts.add(c_low)
                # Hop 1
                aliases = self._concept_aliases.get(c_low, set())
                reachable_concepts.update(aliases)
                # Hop 2 (bounded)
                if max_hops >= 2:
                    for a in list(aliases):
                        reachable_concepts.update(self._concept_aliases.get(a, set()))

            candidate_ids: Set[str] = set()
            for concept in reachable_concepts:
                mem_ids = self._concept_to_memories.get(concept, set())
                candidate_ids.update(mem_ids)

            results: List[MemoryRecord] = []
            for mid in candidate_ids:
                mem = self._memories.get(mid)
                if mem and mem.repository_id == repo_id:
                    if mem.status not in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
                        results.append(mem)

            return results

    def get_memories_referencing_file(
        self,
        repo_id: str,
        file_path: str,
    ) -> List[MemoryRecord]:
        """Find all active memories supported by evidence from a specific file."""
        with self._lock:
            clean_path = file_path.replace("\\", "/").strip()
            matched: List[MemoryRecord] = []

            for m in list(self._memories.values()):
                if m.repository_id != repo_id:
                    continue
                if m.status in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
                    continue

                for ev in m.evidence_records:
                    if ev.file_path == clean_path:
                        matched.append(m)
                        break

            return matched

    def update_memory_status(self, memory_id: str, status: MemoryStatus) -> None:
        """Update the lifecycle status of a specific memory version."""
        with self._lock:
            mem = self._memories.get(memory_id)
            if mem is not None:
                mem.status = status

    def save_handoff(self, handoff: AgentHandoffRecord) -> AgentHandoffRecord:
        """Store an agent handoff node and link to repository and referenced memories."""
        with self._lock:
            self._handoffs[handoff.id] = handoff
            return handoff

    def get_handoff(self, handoff_id: str, repo_id: Optional[str] = None) -> Optional[AgentHandoffRecord]:
        """Fetch a specific handoff record, optionally verifying repository scope."""
        with self._lock:
            h = self._handoffs.get(handoff_id)
            if h is None:
                return None
            if repo_id is not None and h.repository_id != repo_id:
                return None
            return h

    def list_handoffs(self, repo_id: str, status: Optional[HandoffStatus] = None) -> List[AgentHandoffRecord]:
        """List handoffs for a repository, optionally filtered by status."""
        with self._lock:
            results = [h for h in self._handoffs.values() if h.repository_id == repo_id]
            if status is not None:
                results = [h for h in results if h.status == status]
            return sorted(results, key=lambda h: h.created_at, reverse=True)

    def update_handoff_status(self, handoff_id: str, status: HandoffStatus) -> None:
        """Update the status of an agent handoff."""
        with self._lock:
            h = self._handoffs.get(handoff_id)
            if h is not None:
                h.status = status
                h.updated_at = _utc_now_iso()

    def clear(self, repo_id: Optional[str] = None) -> None:
        """Clear graph data (for isolated test teardowns)."""
        with self._lock:
            if repo_id is None:
                self._repositories.clear()
                self._memories.clear()
                self._evidence.clear()
                self._handoffs.clear()
                self._memory_to_evidence.clear()
                self._supersedes_edges.clear()
                self._contradicts_edges.clear()
                self._concept_to_memories.clear()
            else:
                to_del = [mid for mid, m in self._memories.items() if m.repository_id == repo_id]
                for mid in to_del:
                    del self._memories[mid]
                    self._memory_to_evidence.pop(mid, None)
                    self._contradicts_edges.pop(mid, None)
                to_del_h = [hid for hid, h in self._handoffs.items() if h.repository_id == repo_id]
                for hid in to_del_h:
                    del self._handoffs[hid]
                self._repositories.pop(repo_id, None)
