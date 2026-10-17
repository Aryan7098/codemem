"""Core MemoryService domain implementation for CodeMem."""

from __future__ import annotations

import os
import re
from typing import List, Optional, Set

from codemem.core.models import (
    ChangeDetectionResponse,
    EvidenceInput,
    EvidenceRecord,
    MemoryExplanation,
    MemoryHistoryResponse,
    MemoryInput,
    MemoryRecallResponse,
    MemoryRecord,
    MemoryStatus,
    RepositoryInfo,
    ScoredMemoryRecord,
)
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.graph.protocol import GraphStoreProtocol
from codemem.repository.git import GitAdapter


class MemoryService:
    """Core memory engine managing validation, graph storage, evidence, and retrieval."""

    def __init__(self, store: Optional[GraphStoreProtocol] = None, min_confidence_threshold: float = 0.45) -> None:
        self.store: GraphStoreProtocol = store if store is not None else InMemoryGraphStore()
        self.min_confidence_threshold = min_confidence_threshold

    def register_repository(self, path: str) -> RepositoryInfo:
        """Resolve repository identity via 3-tier policy and register scope in the graph."""
        repo_id, canon_path, head_commit, remote_url, git_avail = GitAdapter.resolve_repository_identity(path)

        repo_info = RepositoryInfo(
            repository_id=repo_id,
            root_path=canon_path,
            head_commit=head_commit,
            remote_url=remote_url,
            git_available=git_avail,
        )
        return self.store.upsert_repository(repo_info)

    def remember(self, memory_input: MemoryInput) -> MemoryRecord:
        """
        Store a new codebase claim, anchor its evidence, resolve status,
        and commit nodes & edges to the graph.
        """
        repo = self.store.get_repository(memory_input.repository_id)
        if repo is None:
            raise ValueError(f"Repository not registered in graph: {memory_input.repository_id}")

        evidence_records: List[EvidenceRecord] = []
        has_verified_test = False
        has_anchored_evidence = False

        for ev_in in memory_input.evidence:
            full_path = os.path.join(repo.root_path, ev_in.file_path)

            # Anchor Verification (Trap-safe ADR-007)
            computed_hash = ev_in.content_hash
            if os.path.isfile(full_path):
                file_hash = GitAdapter.compute_file_sha256(full_path)
                if not computed_hash:
                    computed_hash = file_hash

                # AST check if symbol provided
                if ev_in.symbol:
                    symbol_valid = GitAdapter.verify_ast_symbol(full_path, ev_in.symbol)
                    if symbol_valid:
                        has_anchored_evidence = True
                else:
                    has_anchored_evidence = True

            if ev_in.test_command and ev_in.test_passed is True:
                has_verified_test = True

            ev_rec = EvidenceRecord(
                repository_id=memory_input.repository_id,
                file_path=ev_in.file_path,
                kind=ev_in.kind,
                symbol=ev_in.symbol,
                line_start=ev_in.line_start,
                line_end=ev_in.line_end,
                excerpt=ev_in.excerpt,
                content_hash=computed_hash,
                commit_hash=ev_in.commit_hash or repo.head_commit,
                test_command=ev_in.test_command,
                test_passed=ev_in.test_passed,
            )
            evidence_records.append(ev_rec)

        # Status Resolution state machine
        if has_verified_test:
            computed_status = MemoryStatus.VERIFIED
            confidence = max(memory_input.confidence_score, 0.95)
        elif has_anchored_evidence:
            computed_status = MemoryStatus.OBSERVED
            confidence = memory_input.confidence_score
        else:
            computed_status = MemoryStatus.INFERRED
            confidence = min(memory_input.confidence_score, 0.65)

        # Idempotency & Deduplication Check (Workflow B / P0)
        existing_memories = self.store.get_all_memories(memory_input.repository_id, include_superseded=False)
        norm_incoming = self._normalize_claim(memory_input.claim)

        duplicate_candidate: Optional[MemoryRecord] = None
        conflict_candidate: Optional[MemoryRecord] = None

        for em in existing_memories:
            # Check 1: Exact normalized claim match
            if self._normalize_claim(em.claim) == norm_incoming:
                duplicate_candidate = em
                break

            # Check 2: Exact subject + predicate + object match
            if (
                memory_input.subject
                and memory_input.predicate
                and em.subject
                and em.predicate
                and em.subject.strip().lower() == memory_input.subject.strip().lower()
                and em.predicate.strip().lower() == memory_input.predicate.strip().lower()
            ):
                if (em.object or "").strip().lower() == (memory_input.object or "").strip().lower():
                    duplicate_candidate = em
                    break
                else:
                    # Same subject and predicate, but conflicting object claim
                    conflict_candidate = em

        if duplicate_candidate is not None:
            # Idempotent write: update evidence if new evidence was supplied
            if evidence_records:
                existing_hashes = {ev.content_hash for ev in duplicate_candidate.evidence_records if ev.content_hash}
                new_ev_to_add = [ev for ev in evidence_records if not ev.content_hash or ev.content_hash not in existing_hashes]
                if new_ev_to_add:
                    combined_ev = duplicate_candidate.evidence_records + new_ev_to_add
                    duplicate_candidate.evidence_records = combined_ev
                    self.store.save_memory(duplicate_candidate, combined_ev)

            # If incoming fact has higher verification status, upgrade it
            if computed_status == MemoryStatus.VERIFIED and duplicate_candidate.status != MemoryStatus.VERIFIED:
                duplicate_candidate.status = MemoryStatus.VERIFIED
                duplicate_candidate.confidence_score = max(duplicate_candidate.confidence_score, confidence)
                self.store.update_memory_status(duplicate_candidate.id, MemoryStatus.VERIFIED)

            return duplicate_candidate

        memory_record = MemoryRecord(
            repository_id=memory_input.repository_id,
            workspace_id=memory_input.workspace_id,
            claim=memory_input.claim,
            subject=memory_input.subject,
            predicate=memory_input.predicate,
            object=memory_input.object,
            memory_type=memory_input.memory_type,
            status=computed_status,
            confidence_score=confidence,
            source_agent_id=memory_input.source_agent_id,
            source_session_id=memory_input.source_session_id,
            source_commit=repo.head_commit,
            contradicts_id=conflict_candidate.id if conflict_candidate else None,
            valid_from=memory_input.valid_from,
            valid_to=memory_input.valid_to,
            tags=memory_input.tags,
            evidence_records=evidence_records,
        )

        return self.store.save_memory(memory_record, evidence_records)

    @staticmethod
    def _normalize_claim(text: str) -> str:
        """Normalize claim by lowercasing, stripping punctuation, and collapsing whitespace."""
        clean = re.sub(r"[^\w\s]", "", text.lower())
        return " ".join(clean.split())

    def recall(
        self,
        repo_id: str,
        query: str,
        top_k: int = 5,
        include_stale: bool = False,
    ) -> MemoryRecallResponse:
        """
        Recall memories using hybrid graph traversal, keyword search,
        evidence-aware reranking, and conservative abstention.
        """
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise ValueError(f"Repository not registered in graph: {repo_id}")

        # Extract search terms
        raw_terms = re.findall(r"\w+", query.lower())
        terms = [t for t in raw_terms if len(t) > 1 and t not in {"where", "what", "is", "the", "are", "how", "does"}]

        # Stage 1A: Keyword/path search
        candidates_map: dict[str, MemoryRecord] = {}
        stage1_matches = self.store.query_memories_by_terms(repo_id, terms, include_stale=include_stale, top_k=top_k * 2)
        for m in stage1_matches:
            candidates_map[m.id] = m

        # Stage 1B: Concept graph traversal (bounded <= 2 hops)
        concept_matches = self.store.traverse_concept_graph(repo_id, terms, max_hops=2)
        for m in concept_matches:
            if m.status != MemoryStatus.STALE or include_stale:
                candidates_map[m.id] = m

        candidates = list(candidates_map.values())

        if not candidates:
            return MemoryRecallResponse(
                repository_id=repo_id,
                query=query,
                results=[],
                abstained=True,
                abstain_reason="No verified or current evidence found for this query in the repository graph.",
                total_candidates_examined=0,
            )

        scored_records: List[ScoredMemoryRecord] = []
        for mem in candidates:
            # 1. Match score (text & tag overlap)
            content_text = f"{mem.claim} {mem.subject or ''} {mem.predicate or ''} {mem.object or ''} {' '.join(mem.tags)}".lower()
            overlap_count = sum(1 for t in terms if t in content_text)
            match_score = min(1.0, 0.4 + (0.2 * overlap_count)) if overlap_count > 0 else 0.5

            # 2. Status weight
            if mem.status == MemoryStatus.VERIFIED:
                status_weight = 1.0
            elif mem.status == MemoryStatus.OBSERVED:
                status_weight = 0.85
            elif mem.status == MemoryStatus.INFERRED:
                status_weight = 0.60
            elif mem.status == MemoryStatus.STALE:
                status_weight = 0.20
            else:
                status_weight = 0.0

            # 3. Freshness multiplier
            freshness_multiplier = 1.0
            staleness_warning = None
            if mem.status == MemoryStatus.STALE:
                freshness_multiplier = 0.5
                staleness_warning = "Memory evidence references changed or unverified code files."

            final_score = round(match_score * status_weight * freshness_multiplier, 3)

            scored_records.append(
                ScoredMemoryRecord(
                    memory=mem,
                    final_score=final_score,
                    match_score=round(match_score, 3),
                    status_weight=status_weight,
                    freshness_multiplier=freshness_multiplier,
                    staleness_warning=staleness_warning,
                )
            )

        scored_records.sort(key=lambda x: x.final_score, reverse=True)

        # Abstention gate (PRD LongMemEval requirement)
        if not scored_records or scored_records[0].final_score < self.min_confidence_threshold:
            return MemoryRecallResponse(
                repository_id=repo_id,
                query=query,
                results=[],
                abstained=True,
                abstain_reason="Insufficient verified evidence in graph to answer reliably.",
                total_candidates_examined=len(candidates),
            )

        return MemoryRecallResponse(
            repository_id=repo_id,
            query=query,
            results=scored_records[:top_k],
            abstained=False,
            abstain_reason=None,
            total_candidates_examined=len(candidates),
        )

    def update(
        self,
        memory_id: str,
        new_claim: str,
        reason: str,
        repo_id: str,
        new_evidence: Optional[List[EvidenceInput]] = None,
    ) -> MemoryRecord:
        """
        Supersede an existing memory version with a new revision without erasing history.
        """
        old_mem = self.store.get_memory(memory_id, repo_id=repo_id)
        if old_mem is None:
            raw_mem = self.store.get_memory(memory_id, repo_id=None)
            if raw_mem is not None and raw_mem.repository_id != repo_id:
                raise PermissionError(f"Scope violation: memory '{memory_id}' belongs to repository '{raw_mem.repository_id}', not '{repo_id}'")
            raise ValueError(f"Memory not found in repository {repo_id}: {memory_id}")

        evidence_records: List[EvidenceRecord] = []
        if new_evidence:
            for ev_in in new_evidence:
                ev_rec = EvidenceRecord(
                    repository_id=repo_id,
                    file_path=ev_in.file_path,
                    kind=ev_in.kind,
                    symbol=ev_in.symbol,
                    line_start=ev_in.line_start,
                    line_end=ev_in.line_end,
                    excerpt=ev_in.excerpt,
                    content_hash=ev_in.content_hash,
                    test_command=ev_in.test_command,
                    test_passed=ev_in.test_passed,
                )
                evidence_records.append(ev_rec)

        new_record = MemoryRecord(
            repository_id=repo_id,
            logical_fact_id=old_mem.logical_fact_id,
            workspace_id=old_mem.workspace_id,
            claim=new_claim,
            subject=old_mem.subject,
            predicate=old_mem.predicate,
            object=old_mem.object,
            memory_type=old_mem.memory_type,
            status=MemoryStatus.OBSERVED if evidence_records else MemoryStatus.INFERRED,
            confidence_score=old_mem.confidence_score,
            tags=old_mem.tags,
            evidence_records=evidence_records,
        )

        saved_new = self.store.save_memory(new_record, evidence_records)
        self.store.supersede_memory(old_memory_id=memory_id, new_memory=saved_new, reason=reason)
        return saved_new

    def history(self, identifier: str, repo_id: str) -> MemoryHistoryResponse:
        """Retrieve the ordered version chain and change reasons for a fact."""
        # Resolve logical_fact_id if given a memory_id
        logical_fact_id = identifier
        if identifier.startswith("mem_"):
            mem = self.store.get_memory(identifier, repo_id=repo_id)
            if mem is not None:
                logical_fact_id = mem.logical_fact_id

        versions = self.store.get_memories_by_logical_fact(logical_fact_id, repo_id)
        if not versions:
            raw_mem = self.store.get_memory(identifier, repo_id=None)
            if raw_mem is not None and raw_mem.repository_id != repo_id:
                raise PermissionError(f"Scope violation: memory '{identifier}' belongs to repository '{raw_mem.repository_id}', not '{repo_id}'")
            raise ValueError(f"No history found for identifier: {identifier}")

        current_ver = next((v.id for v in versions if v.status != MemoryStatus.SUPERSEDED), versions[0].id)

        return MemoryHistoryResponse(
            logical_fact_id=logical_fact_id,
            repository_id=repo_id,
            current_version_id=current_ver,
            versions=versions,
            change_reasons=[],
        )

    def explain(self, memory_id: str, repo_id: str) -> MemoryExplanation:
        """Provide detailed provenance, source agent, timestamps, and evidence trail."""
        mem = self.store.get_memory(memory_id, repo_id=repo_id)
        if mem is None:
            raw_mem = self.store.get_memory(memory_id, repo_id=None)
            if raw_mem is not None and raw_mem.repository_id != repo_id:
                raise PermissionError(f"Scope violation: memory '{memory_id}' belongs to repository '{raw_mem.repository_id}', not '{repo_id}'")
            raise ValueError(f"Memory not found: {memory_id}")

        repo = self.store.get_repository(repo_id)
        changed_files: List[str] = []

        if repo:
            for ev in mem.evidence_records:
                full_path = os.path.join(repo.root_path, ev.file_path)
                if not os.path.isfile(full_path):
                    changed_files.append(ev.file_path)
                else:
                    curr_hash = GitAdapter.compute_file_sha256(full_path)
                    if ev.content_hash and curr_hash != ev.content_hash:
                        changed_files.append(ev.file_path)

        return MemoryExplanation(
            memory=mem,
            evidence_records=mem.evidence_records,
            provenance={
                "source_agent_id": mem.source_agent_id,
                "source_session_id": mem.source_session_id,
                "recorded_at": mem.recorded_at,
                "source_commit": mem.source_commit,
            },
            freshness_status="STALE" if changed_files else mem.status.value,
            changed_evidence_files=changed_files,
        )

    def check_changes(self, repo_id: str) -> ChangeDetectionResponse:
        """
        Compare current repository file hashes against recorded evidence hashes
        and flag stale memories requiring revalidation.
        """
        repo = self.store.get_repository(repo_id)
        if repo is None:
            raise ValueError(f"Repository not found: {repo_id}")

        affected: List[MemoryRecord] = []
        changed_files_set: Set[str] = set()

        # Check all active memories
        all_active = self.store.get_all_memories(repo_id, include_superseded=False)

        for mem in all_active:
            is_stale = False
            for ev in mem.evidence_records:
                full_path = os.path.join(repo.root_path, ev.file_path)
                if not os.path.isfile(full_path):
                    is_stale = True
                    changed_files_set.add(ev.file_path)
                else:
                    curr_hash = GitAdapter.compute_file_sha256(full_path)
                    if ev.content_hash and curr_hash != ev.content_hash:
                        is_stale = True
                        changed_files_set.add(ev.file_path)

            if is_stale:
                self.store.update_memory_status(mem.id, MemoryStatus.STALE)
                mem.status = MemoryStatus.STALE
                affected.append(mem)

        recommendations = [
            f"Revalidate memory {m.id} ('{m.claim[:40]}...'): evidence files changed."
            for m in affected
        ]

        return ChangeDetectionResponse(
            repository_id=repo_id,
            stale_count=len(affected),
            affected_memories=affected,
            changed_files=sorted(list(changed_files_set)),
            revalidation_recommendations=recommendations,
        )
