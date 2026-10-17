"""FalkorDB graph store implementation of GraphStoreProtocol."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from falkordb import FalkorDB

from codemem.core.embeddings import cosine_similarity
from codemem.core.models import (
    AgentHandoffRecord,
    EvidenceRecord,
    HandoffStatus,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
)


class FalkorDBGraphStore:
    """Authoritative FalkorDB graph store adapter implementing GraphStoreProtocol."""

    _CONCEPT_ALIASES: Dict[str, Set[str]] = {
        "login": {"auth", "authentication", "token", "jwt", "session"},
        "auth": {"login", "authentication", "token", "jwt", "credential"},
        "database": {"falkordb", "redis", "postgres", "sql", "storage"},
        "api": {"endpoint", "route", "http", "contract"},
    }

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
        graph_name: Optional[str] = None,
        client: Optional[FalkorDB] = None,
    ) -> None:
        self.host = host or os.environ.get("FALKORDB_HOST", "localhost")
        self.port = int(port or os.environ.get("FALKORDB_PORT", "6379"))
        self.password = password or os.environ.get("FALKORDB_PASSWORD") or None
        self.graph_name = graph_name or os.environ.get("FALKORDB_GRAPH", "codemem")

        try:
            self.db = client if client is not None else FalkorDB(
                host=self.host,
                port=self.port,
                password=self.password,
            )
            self.graph = self.db.select_graph(self.graph_name)
            self._seed_concept_aliases()
        except Exception as exc:
            raise RuntimeError(
                f"DATABASE_UNAVAILABLE: Could not connect to FalkorDB at {self.host}:{self.port} ({exc})"
            ) from exc

    def _seed_concept_aliases(self) -> None:
        """Seed static 2-hop concept alias edges into FalkorDB."""
        for src, targets in self._CONCEPT_ALIASES.items():
            for dst in targets:
                cypher = (
                    "MERGE (c1:Concept {name: $src}) "
                    "MERGE (c2:Concept {name: $dst}) "
                    "MERGE (c1)-[:RELATES_TO]->(c2)"
                )
                self.graph.query(cypher, {"src": src, "dst": dst})

    def upsert_repository(self, repo: RepositoryInfo) -> RepositoryInfo:
        """Create or update a (:Repository) node in FalkorDB."""
        cypher = (
            "MERGE (r:Repository {id: $id}) "
            "SET r.root_path = $root_path, "
            "    r.head_commit = $head_commit, "
            "    r.remote_url = $remote_url, "
            "    r.git_available = $git_available, "
            "    r.updated_at = $updated_at, "
            "    r.payload_json = $payload_json "
            "RETURN r.payload_json"
        )
        self.graph.query(
            cypher,
            {
                "id": repo.repository_id,
                "root_path": repo.root_path,
                "head_commit": repo.head_commit or "",
                "remote_url": repo.remote_url or "",
                "git_available": repo.git_available,
                "updated_at": repo.updated_at,
                "payload_json": repo.model_dump_json(),
            },
        )
        return repo

    def get_repository(self, repo_id: str) -> Optional[RepositoryInfo]:
        """Fetch repository metadata by ID from FalkorDB."""
        res = self.graph.query(
            "MATCH (r:Repository {id: $id}) RETURN r.payload_json",
            {"id": repo_id},
        )
        if not res.result_set:
            return None
        return RepositoryInfo.model_validate_json(res.result_set[0][0])

    def save_memory(
        self,
        memory: MemoryRecord,
        evidence_records: List[EvidenceRecord],
    ) -> MemoryRecord:
        """Store (:Memory), (:Evidence), (:Concept) nodes and graph relationships."""
        memory.evidence_records = list(evidence_records)
        mem_json = memory.model_dump_json()

        cypher_mem = (
            "MATCH (r:Repository {id: $repo_id}) "
            "MERGE (m:Memory {id: $id}) "
            "SET m.logical_fact_id = $logical_fact_id, "
            "    m.repository_id = $repo_id, "
            "    m.claim = $claim, "
            "    m.subject = $subject, "
            "    m.predicate = $predicate, "
            "    m.object = $object, "
            "    m.memory_type = $memory_type, "
            "    m.status = $status, "
            "    m.confidence_score = $confidence_score, "
            "    m.recorded_at = $recorded_at, "
            "    m.payload_json = $payload_json "
            "MERGE (m)-[:ABOUT_REPO]->(r)"
        )
        self.graph.query(
            cypher_mem,
            {
                "id": memory.id,
                "logical_fact_id": memory.logical_fact_id,
                "repo_id": memory.repository_id,
                "claim": memory.claim,
                "subject": memory.subject or "",
                "predicate": memory.predicate or "",
                "object": memory.object or "",
                "memory_type": memory.memory_type.value,
                "status": memory.status.value,
                "confidence_score": memory.confidence_score,
                "recorded_at": memory.recorded_at,
                "payload_json": mem_json,
            },
        )

        for ev in evidence_records:
            cypher_ev = (
                "MATCH (m:Memory {id: $mem_id}) "
                "MERGE (e:Evidence {id: $ev_id}) "
                "SET e.repository_id = $repo_id, "
                "    e.file_path = $file_path, "
                "    e.symbol = $symbol, "
                "    e.content_hash = $content_hash, "
                "    e.payload_json = $payload_json "
                "MERGE (m)-[:SUPPORTED_BY]->(e)"
            )
            self.graph.query(
                cypher_ev,
                {
                    "mem_id": memory.id,
                    "ev_id": ev.id,
                    "repo_id": ev.repository_id,
                    "file_path": ev.file_path,
                    "symbol": ev.symbol or "",
                    "content_hash": ev.content_hash or "",
                    "payload_json": ev.model_dump_json(),
                },
            )

        for tag in memory.tags:
            tag_clean = tag.lower().strip()
            if tag_clean:
                self.graph.query(
                    "MATCH (m:Memory {id: $mem_id}) "
                    "MERGE (c:Concept {name: $tag}) "
                    "MERGE (m)-[:RELATES_TO]->(c)",
                    {"mem_id": memory.id, "tag": tag_clean},
                )

        return memory

    def get_memory(self, memory_id: str, repo_id: Optional[str] = None) -> Optional[MemoryRecord]:
        """Retrieve a specific memory version, optionally verifying repo scope."""
        res = self.graph.query(
            "MATCH (m:Memory {id: $id}) RETURN m.payload_json",
            {"id": memory_id},
        )
        if not res.result_set:
            return None
        mem = MemoryRecord.model_validate_json(res.result_set[0][0])
        if repo_id is not None and mem.repository_id != repo_id:
            return None
        return mem

    def get_memories_by_logical_fact(
        self,
        logical_fact_id: str,
        repo_id: str,
    ) -> List[MemoryRecord]:
        """Retrieve all versions of a logical fact ordered newest to oldest."""
        res = self.graph.query(
            "MATCH (m:Memory {logical_fact_id: $fact_id, repository_id: $repo_id}) "
            "RETURN m.payload_json ORDER BY m.recorded_at DESC",
            {"fact_id": logical_fact_id, "repo_id": repo_id},
        )
        return [MemoryRecord.model_validate_json(row[0]) for row in res.result_set]

    def supersede_memory(
        self,
        old_memory_id: str,
        new_memory: MemoryRecord,
        reason: str,
    ) -> None:
        """Create [:SUPERSEDES] edge and mark old memory as SUPERSEDED."""
        old_mem = self.get_memory(old_memory_id)
        if old_mem is None:
            return

        old_mem.status = MemoryStatus.SUPERSEDED
        old_mem.superseded_by_id = new_memory.id
        new_memory.supersedes_id = old_memory_id
        now_iso = datetime.now(timezone.utc).isoformat()

        self.graph.query(
            "MATCH (old:Memory {id: $old_id}), (new:Memory {id: $new_id}) "
            "SET old.status = 'SUPERSEDED', old.payload_json = $old_json, "
            "    new.payload_json = $new_json "
            "MERGE (new)-[:SUPERSEDES {reason: $reason, at: $at}]->(old)",
            {
                "old_id": old_memory_id,
                "new_id": new_memory.id,
                "old_json": old_mem.model_dump_json(),
                "new_json": new_memory.model_dump_json(),
                "reason": reason,
                "at": now_iso,
            },
        )

    def get_all_memories(
        self,
        repo_id: str,
        include_superseded: bool = False,
    ) -> List[MemoryRecord]:
        """Retrieve all memories belonging to a repository scope."""
        res = self.graph.query(
            "MATCH (m:Memory {repository_id: $repo_id}) RETURN m.payload_json",
            {"repo_id": repo_id},
        )
        records = [MemoryRecord.model_validate_json(row[0]) for row in res.result_set]
        if not include_superseded:
            records = [m for m in records if m.status != MemoryStatus.SUPERSEDED]
        return records

    def query_memories_by_terms(
        self,
        repo_id: str,
        terms: List[str],
        include_stale: bool = False,
        top_k: int = 5,
    ) -> List[MemoryRecord]:
        """Retrieve active memories matching search terms within repository scope."""
        lowered_terms = [t.lower() for t in terms if len(t) > 1]
        matches: List[Tuple[MemoryRecord, int]] = []

        for m in self.get_all_memories(repo_id, include_superseded=False):
            if m.status == MemoryStatus.CONTRADICTED:
                continue
            if m.status == MemoryStatus.STALE and not include_stale:
                continue

            searchable = f"{m.claim} {m.subject or ''} {m.predicate or ''} {m.object or ''} {' '.join(m.tags)}".lower()
            hit_count = sum(1 for term in lowered_terms if term in searchable)

            if hit_count == 0:
                for ev in m.evidence_records:
                    ev_text = f"{ev.file_path} {ev.symbol or ''} {ev.excerpt or ''}".lower()
                    if any(term in ev_text for term in lowered_terms):
                        hit_count += 1
                        break

            if hit_count > 0:
                matches.append((m, hit_count))

        matches.sort(key=lambda item: (item[1], item[0].recorded_at), reverse=True)
        return [item[0] for item in matches[:top_k]]

    def traverse_concept_graph(
        self,
        repo_id: str,
        concepts: List[str],
        max_hops: int = 2,
    ) -> List[MemoryRecord]:
        """Traverse bounded (<= 2 hops) (:Concept)-[:RELATES_TO*0..2]-(:Concept)<-[:RELATES_TO]-(:Memory) in FalkorDB."""
        clean_concepts = [c.lower().strip() for c in concepts if c.strip()]
        if not clean_concepts:
            return []

        hop_bound = min(max(1, max_hops), 2)
        cypher = (
            f"MATCH (c:Concept)-[:RELATES_TO*0..{hop_bound}]-(expanded:Concept)<-[:RELATES_TO]-(m:Memory) "
            "WHERE c.name IN $concepts AND m.repository_id = $repo_id "
            "RETURN DISTINCT m.payload_json"
        )
        res = self.graph.query(cypher, {"concepts": clean_concepts, "repo_id": repo_id})
        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem = MemoryRecord.model_validate_json(row[0])
            if mem.status not in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
                results.append(mem)
        return results

    def vector_search(
        self,
        repo_id: str,
        query_embedding: List[float],
        include_stale: bool = False,
        top_k: int = 5,
    ) -> List[Tuple[MemoryRecord, float]]:
        """Retrieve candidate memories ranked by vector similarity within repository scope."""
        if not query_embedding:
            return []

        scored: List[Tuple[MemoryRecord, float]] = []
        for m in self.get_all_memories(repo_id, include_superseded=False):
            if m.status == MemoryStatus.CONTRADICTED:
                continue
            if m.status == MemoryStatus.STALE and not include_stale:
                continue
            if not m.embedding:
                continue
            sim = cosine_similarity(query_embedding, m.embedding)
            if sim > 0.0:
                scored.append((m, round(sim, 4)))

        scored.sort(key=lambda item: (item[1], item[0].recorded_at), reverse=True)
        return scored[:top_k]

    def get_memories_referencing_file(
        self,
        repo_id: str,
        file_path: str,
    ) -> List[MemoryRecord]:
        """Find all active memories supported by evidence from a specific file."""
        clean_path = file_path.replace("\\", "/").strip()
        res = self.graph.query(
            "MATCH (m:Memory {repository_id: $repo_id})-[:SUPPORTED_BY]->(e:Evidence {file_path: $file_path}) "
            "RETURN DISTINCT m.payload_json",
            {"repo_id": repo_id, "file_path": clean_path},
        )
        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem = MemoryRecord.model_validate_json(row[0])
            if mem.status not in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
                results.append(mem)
        return results

    def update_memory_status(self, memory_id: str, status: MemoryStatus) -> None:
        """Update the lifecycle status of a specific memory version in FalkorDB."""
        mem = self.get_memory(memory_id)
        if mem is None:
            return
        mem.status = status
        self.graph.query(
            "MATCH (m:Memory {id: $id}) SET m.status = $status, m.payload_json = $json",
            {"id": memory_id, "status": status.value, "json": mem.model_dump_json()},
        )

    def save_handoff(self, handoff: AgentHandoffRecord) -> AgentHandoffRecord:
        """Store or update an agent handoff record in FalkorDB."""
        cypher = (
            "MERGE (h:Handoff {handoff_id: $handoff_id}) "
            "SET h.repository_id = $repository_id, "
            "    h.payload_json = $payload_json "
            "RETURN h.payload_json"
        )
        self.graph.query(
            cypher,
            {
                "handoff_id": handoff.handoff_id,
                "repository_id": handoff.repository_id,
                "payload_json": handoff.model_dump_json(),
            },
        )
        return handoff

    def get_handoff(self, handoff_id: str, repo_id: Optional[str] = None) -> Optional[AgentHandoffRecord]:
        """Fetch a specific handoff record by handoff_id."""
        res = self.graph.query(
            "MATCH (h:Handoff {handoff_id: $handoff_id}) RETURN h.payload_json",
            {"handoff_id": handoff_id},
        )
        if not res.result_set:
            return None
        rec = AgentHandoffRecord.model_validate_json(res.result_set[0][0])
        if repo_id is not None and rec.repository_id != repo_id:
            raise PermissionError(
                f"Scope violation: handoff '{handoff_id}' belongs to repository '{rec.repository_id}', not '{repo_id}'"
            )
        return rec

    def list_handoffs(self, repo_id: str, status: Optional[HandoffStatus] = None) -> List[AgentHandoffRecord]:
        """List handoffs for a repository, optionally filtered by status."""
        res = self.graph.query(
            "MATCH (h:Handoff {repository_id: $repo_id}) RETURN h.payload_json",
            {"repo_id": repo_id},
        )
        records = [AgentHandoffRecord.model_validate_json(row[0]) for row in res.result_set]
        if status is not None:
            records = [h for h in records if h.status == status]
        return sorted(records, key=lambda h: h.created_at, reverse=True)

    def update_handoff_status(self, handoff_id: str, status: HandoffStatus) -> None:
        """Update the status of an agent handoff."""
        rec = self.get_handoff(handoff_id)
        if rec is not None:
            rec.status = status
            rec.updated_at = datetime.now(timezone.utc).isoformat()
            self.save_handoff(rec)

    def clear(self, repo_id: Optional[str] = None) -> None:
        """Clear graph data for a specific repo or entire graph."""
        if repo_id is None:
            self.graph.query("MATCH (n) DETACH DELETE n")
            self._seed_concept_aliases()
        else:
            self.graph.query(
                "MATCH (n {repository_id: $repo_id}) DETACH DELETE n",
                {"repo_id": repo_id},
            )
            self.graph.query(
                "MATCH (r:Repository {id: $repo_id}) DETACH DELETE r",
                {"repo_id": repo_id},
            )
