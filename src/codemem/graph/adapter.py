"""
FalkorDB Cypher graph storage adapter implementing GraphStoreProtocol.

Provides authoritative graph persistence for CodeMem using native openCypher
queries over the official FalkorDB client.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from codemem.core.models import (
    EvidenceRecord,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
)
from codemem.graph.protocol import GraphStoreProtocol

logger = logging.getLogger(__name__)


def _extract_node_props(node_obj: Any) -> Dict[str, Any]:
    """Extract properties dictionary from a FalkorDB Node or raw dict."""
    if isinstance(node_obj, dict):
        return node_obj
    if hasattr(node_obj, "properties") and isinstance(node_obj.properties, dict):
        return dict(node_obj.properties)
    return {}


def _row_to_memory_record(
    mem_props: Dict[str, Any],
    evidence_props_list: List[Dict[str, Any]],
) -> MemoryRecord:
    """Reconstruct typed MemoryRecord from Cypher node properties."""
    ev_records: List[EvidenceRecord] = []
    for ev_props in evidence_props_list:
        if ev_props and ev_props.get("id"):
            ev_records.append(
                EvidenceRecord(
                    id=ev_props["id"],
                    repository_id=ev_props.get("repository_id", ""),
                    file_path=ev_props.get("file_path", ""),
                    kind=ev_props.get("kind", "source_span"),
                    symbol=ev_props.get("symbol"),
                    line_start=ev_props.get("line_start"),
                    line_end=ev_props.get("line_end"),
                    excerpt=ev_props.get("excerpt"),
                    content_hash=ev_props.get("content_hash"),
                    test_command=ev_props.get("test_command"),
                    test_passed=ev_props.get("test_passed"),
                    observed_at=ev_props.get("observed_at", ""),
                )
            )

    raw_tags = mem_props.get("tags", [])
    if isinstance(raw_tags, str):
        try:
            tags = json.loads(raw_tags)
        except Exception:
            tags = [raw_tags] if raw_tags else []
    elif isinstance(raw_tags, list):
        tags = raw_tags
    else:
        tags = []

    return MemoryRecord(
        id=mem_props["id"],
        logical_fact_id=mem_props["logical_fact_id"],
        repository_id=mem_props["repository_id"],
        workspace_id=mem_props.get("workspace_id", "default"),
        claim=mem_props["claim"],
        subject=mem_props.get("subject"),
        predicate=mem_props.get("predicate"),
        object=mem_props.get("object"),
        memory_type=mem_props.get("memory_type", "architecture"),
        status=mem_props.get("status", "OBSERVED"),
        confidence_score=float(mem_props.get("confidence_score", 0.8)),
        tags=tags,
        source_agent_id=mem_props.get("source_agent_id", "unknown-agent"),
        source_session_id=mem_props.get("source_session_id", "default-session"),
        source_commit=mem_props.get("source_commit"),
        supersedes_id=mem_props.get("supersedes_id"),
        superseded_by_id=mem_props.get("superseded_by_id"),
        recorded_at=mem_props.get("recorded_at", datetime.now(timezone.utc).isoformat()),
        evidence_records=ev_records,
    )


class FalkorDBAdapter:
    """
    Authoritative FalkorDB graph store adapter executing parameterized Cypher queries.
    Satisfies GraphStoreProtocol.
    """

    DEFAULT_CONCEPT_ALIASES: Dict[str, Set[str]] = {
        "login": {"auth", "authentication", "token", "jwt", "session"},
        "auth": {"login", "authentication", "token", "jwt", "credential"},
        "database": {"falkordb", "redis", "postgres", "sql", "storage"},
        "api": {"endpoint", "route", "http", "contract"},
    }

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: Optional[str] = None,
        graph_id: str = "codemem",
        graph_client: Optional[Any] = None,
        lazy_connect: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.graph_id = graph_id
        self._graph = graph_client

        if self._graph is None and not lazy_connect:
            self._connect()

    def _connect(self) -> None:
        """Establish connection to FalkorDB instance and initialize schema indices."""
        try:
            from falkordb import FalkorDB

            db = FalkorDB(host=self.host, port=self.port, password=self.password)
            self._graph = db.select_graph(self.graph_id)
            self._init_schema_indices()
            self._seed_concept_aliases()
        except Exception as exc:
            logger.warning("FalkorDB connection failed at %s:%s - %s", self.host, self.port, exc)
            raise ConnectionError(
                f"Failed to connect to FalkorDB at {self.host}:{self.port}. "
                "Ensure docker-compose is running or set GRAPH_BACKEND=memory."
            ) from exc

    def _init_schema_indices(self) -> None:
        """Create schema indices in FalkorDB to accelerate graph traversal and lookups."""
        indices = [
            "CREATE INDEX FOR (r:Repository) ON (r.id)",
            "CREATE INDEX FOR (m:Memory) ON (m.id)",
            "CREATE INDEX FOR (m:Memory) ON (m.logical_fact_id)",
            "CREATE INDEX FOR (e:Evidence) ON (e.id)",
            "CREATE INDEX FOR (e:Evidence) ON (e.file_path)",
            "CREATE INDEX FOR (c:Concept) ON (c.name)",
        ]
        for idx_query in indices:
            try:
                self.query(idx_query)
            except Exception:
                pass  # Indices may already exist

    def _seed_concept_aliases(self) -> None:
        """Seed bidirectional concept alias relationships in FalkorDB."""
        for c1, aliases in self.DEFAULT_CONCEPT_ALIASES.items():
            for c2 in aliases:
                try:
                    self.query(
                        """
                        MERGE (a:Concept {name: $c1})
                        MERGE (b:Concept {name: $c2})
                        MERGE (a)-[:RELATES_TO]->(b)
                        """,
                        {"c1": c1, "c2": c2},
                    )
                except Exception:
                    pass

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a parameterized Cypher query against FalkorDB."""
        if self._graph is None:
            self._connect()
        return self._graph.query(cypher, params or {})

    def health_check(self) -> bool:
        """Return True if FalkorDB connection is active and healthy."""
        try:
            res = self.query("RETURN 1")
            return res is not None
        except Exception:
            return False

    # --------------------------------------------------------------------------
    # GraphStoreProtocol Implementation
    # --------------------------------------------------------------------------

    def upsert_repository(self, repo: RepositoryInfo) -> RepositoryInfo:
        """Create or update a repository scope node."""
        cypher = """
        MERGE (r:Repository {id: $id})
        SET r.root_path = $root_path,
            r.head_commit = $head_commit,
            r.remote_url = $remote_url,
            r.git_available = $git_available,
            r.updated_at = $updated_at
        RETURN r
        """
        params = {
            "id": repo.repository_id,
            "root_path": repo.root_path,
            "head_commit": repo.head_commit,
            "remote_url": repo.remote_url,
            "git_available": repo.git_available,
            "updated_at": repo.updated_at,
        }
        self.query(cypher, params)
        return repo

    def get_repository(self, repo_id: str) -> Optional[RepositoryInfo]:
        """Fetch repository metadata by ID."""
        cypher = "MATCH (r:Repository {id: $id}) RETURN r"
        res = self.query(cypher, {"id": repo_id})
        if not res or not res.result_set:
            return None

        props = _extract_node_props(res.result_set[0][0])
        return RepositoryInfo(
            repository_id=props.get("id", repo_id),
            root_path=props.get("root_path", ""),
            head_commit=props.get("head_commit"),
            remote_url=props.get("remote_url"),
            git_available=props.get("git_available", False),
            created_at=props.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=props.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )

    def save_memory(
        self,
        memory: MemoryRecord,
        evidence_records: List[EvidenceRecord],
    ) -> MemoryRecord:
        """Store a memory node, its evidence nodes, and establish graph edges."""
        # Attach evidence records to in-memory model
        memory.evidence_records = list(evidence_records)

        # 1. Upsert Memory node and link to Repository, Agent, Session
        mem_cypher = """
        MERGE (m:Memory {id: $id})
        SET m.logical_fact_id = $logical_fact_id,
            m.repository_id = $repository_id,
            m.workspace_id = $workspace_id,
            m.claim = $claim,
            m.subject = $subject,
            m.predicate = $predicate,
            m.object = $object,
            m.memory_type = $memory_type,
            m.status = $status,
            m.confidence_score = $confidence_score,
            m.tags = $tags,
            m.source_agent_id = $source_agent_id,
            m.source_session_id = $source_session_id,
            m.source_commit = $source_commit,
            m.supersedes_id = $supersedes_id,
            m.superseded_by_id = $superseded_by_id,
            m.recorded_at = $recorded_at
        WITH m
        MATCH (r:Repository {id: $repository_id})
        MERGE (m)-[:ABOUT_REPO]->(r)
        WITH m
        MERGE (a:Agent {id: $source_agent_id})
        MERGE (s:Session {id: $source_session_id})
        MERGE (m)-[:DISCOVERED_BY]->(a)
        MERGE (m)-[:RECORDED_IN]->(s)
        """
        mem_params = {
            "id": memory.id,
            "logical_fact_id": memory.logical_fact_id,
            "repository_id": memory.repository_id,
            "workspace_id": memory.workspace_id,
            "claim": memory.claim,
            "subject": memory.subject,
            "predicate": memory.predicate,
            "object": memory.object,
            "memory_type": memory.memory_type.value if hasattr(memory.memory_type, "value") else str(memory.memory_type),
            "status": memory.status.value if hasattr(memory.status, "value") else str(memory.status),
            "confidence_score": memory.confidence_score,
            "tags": list(memory.tags),
            "source_agent_id": memory.source_agent_id,
            "source_session_id": memory.source_session_id,
            "source_commit": memory.source_commit,
            "supersedes_id": memory.supersedes_id,
            "superseded_by_id": memory.superseded_by_id,
            "recorded_at": memory.recorded_at,
        }
        self.query(mem_cypher, mem_params)

        # 2. Attach Evidence nodes
        for ev in evidence_records:
            ev_cypher = """
            MATCH (m:Memory {id: $memory_id})
            MERGE (e:Evidence {id: $id})
            SET e.repository_id = $repository_id,
                e.file_path = $file_path,
                e.kind = $kind,
                e.symbol = $symbol,
                e.line_start = $line_start,
                e.line_end = $line_end,
                e.excerpt = $excerpt,
                e.content_hash = $content_hash,
                e.test_command = $test_command,
                e.test_passed = $test_passed,
                e.observed_at = $observed_at
            MERGE (m)-[:SUPPORTED_BY]->(e)
            """
            ev_params = {
                "memory_id": memory.id,
                "id": ev.id,
                "repository_id": ev.repository_id,
                "file_path": ev.file_path,
                "kind": ev.kind.value if hasattr(ev.kind, "value") else str(ev.kind),
                "symbol": ev.symbol,
                "line_start": ev.line_start,
                "line_end": ev.line_end,
                "excerpt": ev.excerpt,
                "content_hash": ev.content_hash,
                "test_command": ev.test_command,
                "test_passed": ev.test_passed,
                "observed_at": ev.observed_at,
            }
            self.query(ev_cypher, ev_params)

        # 3. Attach Concept tags
        for tag in memory.tags:
            tag_clean = tag.lower().strip()
            if tag_clean:
                tag_cypher = """
                MATCH (m:Memory {id: $memory_id})
                MERGE (c:Concept {name: $concept_name})
                MERGE (m)-[:TAGGED_WITH]->(c)
                """
                self.query(tag_cypher, {"memory_id": memory.id, "concept_name": tag_clean})

        return memory

    def get_memory(self, memory_id: str, repo_id: Optional[str] = None) -> Optional[MemoryRecord]:
        """Retrieve a specific memory version, optionally verifying repo scope."""
        cypher = """
        MATCH (m:Memory {id: $memory_id})
        WHERE ($repo_id IS NULL OR m.repository_id = $repo_id)
        OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(e:Evidence)
        RETURN m, collect(e) as evidence_list
        """
        res = self.query(cypher, {"memory_id": memory_id, "repo_id": repo_id})
        if not res or not res.result_set:
            return None

        row = res.result_set[0]
        mem_props = _extract_node_props(row[0])
        ev_list = [_extract_node_props(e) for e in (row[1] or []) if e is not None]
        return _row_to_memory_record(mem_props, ev_list)

    def get_memories_by_logical_fact(
        self,
        logical_fact_id: str,
        repo_id: str,
    ) -> List[MemoryRecord]:
        """Retrieve all versions of a logical fact ordered chronologically."""
        cypher = """
        MATCH (m:Memory {logical_fact_id: $logical_fact_id, repository_id: $repo_id})
        OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(e:Evidence)
        RETURN m, collect(e) as evidence_list
        ORDER BY m.recorded_at DESC
        """
        res = self.query(cypher, {"logical_fact_id": logical_fact_id, "repo_id": repo_id})
        if not res or not res.result_set:
            return []

        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem_props = _extract_node_props(row[0])
            ev_list = [_extract_node_props(e) for e in (row[1] or []) if e is not None]
            results.append(_row_to_memory_record(mem_props, ev_list))
        return results

    def supersede_memory(
        self,
        old_memory_id: str,
        new_memory: MemoryRecord,
        reason: str,
    ) -> None:
        """Establish SUPERSEDES edge from new to old memory and transition old status."""
        cypher = """
        MATCH (old_m:Memory {id: $old_id})
        MATCH (new_m:Memory {id: $new_id})
        SET old_m.status = 'SUPERSEDED',
            old_m.superseded_by_id = new_m.id,
            new_m.supersedes_id = old_m.id
        MERGE (new_m)-[r:SUPERSEDES]->(old_m)
        SET r.reason = $reason,
            r.recorded_at = $recorded_at
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        params = {
            "old_id": old_memory_id,
            "new_id": new_memory.id,
            "reason": reason,
            "recorded_at": now_iso,
        }
        self.query(cypher, params)

    def get_all_memories(
        self,
        repo_id: str,
        include_superseded: bool = False,
    ) -> List[MemoryRecord]:
        """Retrieve all memories belonging to a repository scope."""
        cypher = """
        MATCH (m:Memory {repository_id: $repo_id})
        WHERE ($include_superseded = true OR m.status <> 'SUPERSEDED')
        OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(e:Evidence)
        RETURN m, collect(e) as evidence_list
        ORDER BY m.recorded_at DESC
        """
        res = self.query(cypher, {"repo_id": repo_id, "include_superseded": include_superseded})
        if not res or not res.result_set:
            return []

        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem_props = _extract_node_props(row[0])
            ev_list = [_extract_node_props(e) for e in (row[1] or []) if e is not None]
            results.append(_row_to_memory_record(mem_props, ev_list))
        return results

    def query_memories_by_terms(
        self,
        repo_id: str,
        terms: List[str],
        include_stale: bool = False,
        top_k: int = 5,
    ) -> List[MemoryRecord]:
        """Retrieve active memories matching search terms within repository scope."""
        all_candidates = self.get_all_memories(repo_id, include_superseded=False)
        lowered_terms = [t.lower() for t in terms if len(t) > 1]
        matches: List[Tuple[MemoryRecord, int]] = []

        for m in all_candidates:
            if m.status in (MemoryStatus.SUPERSEDED, MemoryStatus.CONTRADICTED):
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
        """Traverse concept-memory relationships in FalkorDB bounded by max_hops."""
        clean_concepts = [c.lower().strip() for c in concepts if c.strip()]
        if not clean_concepts:
            return []

        cypher = f"""
        MATCH (c:Concept)
        WHERE toLower(c.name) IN $concepts
        MATCH (c)-[:RELATES_TO*0..{max_hops}]-(expanded:Concept)<-[:TAGGED_WITH]-(m:Memory {{repository_id: $repo_id}})
        WHERE m.status <> 'SUPERSEDED' AND m.status <> 'CONTRADICTED'
        OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(e:Evidence)
        RETURN DISTINCT m, collect(e) as evidence_list
        """
        res = self.query(cypher, {"concepts": clean_concepts, "repo_id": repo_id})
        if not res or not res.result_set:
            return []

        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem_props = _extract_node_props(row[0])
            ev_list = [_extract_node_props(e) for e in (row[1] or []) if e is not None]
            results.append(_row_to_memory_record(mem_props, ev_list))
        return results

    def get_memories_referencing_file(
        self,
        repo_id: str,
        file_path: str,
    ) -> List[MemoryRecord]:
        """Find all active memories supported by evidence from a specific file."""
        clean_path = file_path.replace("\\", "/").strip()
        cypher = """
        MATCH (m:Memory {repository_id: $repo_id})-[:SUPPORTED_BY]->(e:Evidence {file_path: $file_path})
        WHERE m.status <> 'SUPERSEDED' AND m.status <> 'CONTRADICTED'
        OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(all_e:Evidence)
        RETURN DISTINCT m, collect(all_e) as evidence_list
        """
        res = self.query(cypher, {"repo_id": repo_id, "file_path": clean_path})
        if not res or not res.result_set:
            return []

        results: List[MemoryRecord] = []
        for row in res.result_set:
            mem_props = _extract_node_props(row[0])
            ev_list = [_extract_node_props(e) for e in (row[1] or []) if e is not None]
            results.append(_row_to_memory_record(mem_props, ev_list))
        return results

    def update_memory_status(self, memory_id: str, status: MemoryStatus) -> None:
        """Update the lifecycle status of a specific memory version."""
        cypher = """
        MATCH (m:Memory {id: $memory_id})
        SET m.status = $status
        """
        status_val = status.value if hasattr(status, "value") else str(status)
        self.query(cypher, {"memory_id": memory_id, "status": status_val})

    def clear(self, repo_id: Optional[str] = None) -> None:
        """Clear graph data (for isolated test teardowns)."""
        if repo_id is None:
            self.query("MATCH (n) DETACH DELETE n")
        else:
            self.query(
                """
                MATCH (m:Memory {repository_id: $repo_id})
                OPTIONAL MATCH (m)-[:SUPPORTED_BY]->(e:Evidence)
                DETACH DELETE m, e
                WITH 1 as dummy
                MATCH (r:Repository {id: $repo_id})
                DETACH DELETE r
                """,
                {"repo_id": repo_id},
            )
