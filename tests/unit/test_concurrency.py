"""Unit tests for Gap 3: Concurrent Multi-Agent Access and Thread-Safety."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
import pytest

from codemem.core.models import (
    EvidenceInput,
    HandoffInput,
    HandoffStatus,
    MemoryInput,
    MemoryStatus,
    RepositoryInfo,
)
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore


@pytest.fixture
def test_repo_env(tmp_path: Path):
    store = InMemoryGraphStore()
    service = MemoryService(store=store)

    # Create dummy source files for anchoring
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    auth_file = src_dir / "auth.py"
    auth_file.write_text("def authenticate_user(): pass\n", encoding="utf-8")
    db_file = src_dir / "db.py"
    db_file.write_text("def connect_database(): pass\n", encoding="utf-8")

    repo = RepositoryInfo(
        repository_id="concurrent-repo",
        root_path=str(tmp_path),
        head_commit="commit-concurrent-001",
        git_available=True,
    )
    store.upsert_repository(repo)
    return service, repo, tmp_path


def test_concurrent_multi_agent_writes(test_repo_env):
    """Verify multiple agents writing memories concurrently experience no race conditions or data loss."""
    service, repo, tmp_path = test_repo_env
    num_agents = 8
    writes_per_agent = 5
    total_expected = num_agents * writes_per_agent

    def agent_writer(agent_idx: int):
        results = []
        for i in range(writes_per_agent):
            inp = MemoryInput(
                repository_id=repo.repository_id,
                claim=f"Agent {agent_idx} observation {i}: auth token expiry configured",
                subject=f"auth-module-{agent_idx}",
                predicate="configures",
                object=f"token-expiry-{i}",
                tags=["auth", "token", f"agent-{agent_idx}"],
                source_agent_id=f"agent-{agent_idx}",
                evidence=[
                    EvidenceInput(
                        file_path="src/auth.py",
                        symbol="authenticate_user",
                    )
                ],
            )
            rec = service.remember(inp)
            results.append(rec)
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_agents) as executor:
        futures = [executor.submit(agent_writer, idx) for idx in range(num_agents)]
        all_results = []
        for fut in concurrent.futures.as_completed(futures):
            all_results.extend(fut.result())

    assert len(all_results) == total_expected
    all_stored = service.store.get_all_memories(repo.repository_id, include_superseded=True)
    assert len(all_stored) == total_expected


def test_concurrent_reads_and_writes_contention(test_repo_env):
    """Verify simultaneous read and write contention does not produce dictionary iteration errors or deadlocks."""
    service, repo, _ = test_repo_env

    # Pre-populate initial facts
    for i in range(10):
        service.remember(
            MemoryInput(
                repository_id=repo.repository_id,
                claim=f"Seed fact {i} database pool max connections",
                subject="db-pool",
                predicate="limits",
                object=f"connections-{i}",
                tags=["database", "pool"],
                source_agent_id="seed-agent",
            )
        )

    stop_event = False

    def reader_worker():
        queries = ["auth token", "database pool", "connections", "login"]
        reads = 0
        for _ in range(30):
            if stop_event:
                break
            for q in queries:
                resp = service.recall(query=q, repo_id=repo.repository_id)
                assert resp is not None
                reads += 1
        return reads

    def writer_worker(worker_id: int):
        writes = 0
        for i in range(15):
            service.remember(
                MemoryInput(
                    repository_id=repo.repository_id,
                    claim=f"Worker {worker_id} dynamic fact {i}",
                    subject=f"worker-{worker_id}",
                    predicate="executes",
                    object=f"task-{i}",
                    tags=["database", f"worker-{worker_id}"],
                    source_agent_id=f"worker-{worker_id}",
                )
            )
            writes += 1
        return writes

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        reader_futures = [executor.submit(reader_worker) for _ in range(5)]
        writer_futures = [executor.submit(writer_worker, wid) for wid in range(5)]

        total_reads = sum(f.result() for f in concurrent.futures.as_completed(reader_futures))
        total_writes = sum(f.result() for f in concurrent.futures.as_completed(writer_futures))

    assert total_reads > 0
    assert total_writes == 75


def test_concurrent_agent_handoffs(test_repo_env):
    """Verify multiple agents can concurrently create, list, and complete handoffs without conflict."""
    service, repo, _ = test_repo_env
    num_handoffs = 10

    def create_and_complete_handoff(idx: int):
        inp = HandoffInput(
            repository_id=repo.repository_id,
            originating_agent_id=f"planner-{idx}",
            receiving_agent_id=f"coder-{idx}",
            task_objective=f"Subtask objective {idx}",
            completed_work=f"Finished phase {idx}",
            open_risks=[f"Risk factor {idx}"],
            relevant_memory_ids=[f"mem-{idx}"],
        )
        handoff = service.record_handoff(inp)
        assert handoff.status == HandoffStatus.PENDING

        completed = service.complete_handoff(
            handoff_id=handoff.handoff_id,
            repo_id=repo.repository_id,
            receiving_agent_id=f"coder-{idx}-final",
        )
        assert completed.status == HandoffStatus.COMPLETED
        return completed.handoff_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(create_and_complete_handoff, i) for i in range(num_handoffs)]
        completed_ids = [fut.result() for fut in concurrent.futures.as_completed(futures)]

    assert len(completed_ids) == num_handoffs
    all_handoffs = service.list_handoffs(repo.repository_id)
    assert len(all_handoffs) == num_handoffs
    assert all(h.status == HandoffStatus.COMPLETED for h in all_handoffs)


def test_concurrent_deduplication(test_repo_env):
    """Verify multiple agents simultaneously remembering identical claims are safely deduplicated."""
    service, repo, _ = test_repo_env
    identical_claim = "PostgreSQL pool maximum timeout is configured to 30 seconds"

    def submit_identical(agent_idx: int):
        inp = MemoryInput(
            repository_id=repo.repository_id,
            claim=identical_claim,
            subject="postgres-pool",
            predicate="has-timeout",
            object="30s",
            tags=["database", "postgres"],
            source_agent_id=f"agent-{agent_idx}",
            confidence_score=0.75 + (agent_idx * 0.02),
        )
        return service.remember(inp)

    num_threads = 6
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(submit_identical, i) for i in range(num_threads)]
        results = [fut.result() for fut in concurrent.futures.as_completed(futures)]

    # All returned memories must refer to the exact same logical claim / fact
    distinct_ids = {r.id for r in results}
    assert len(distinct_ids) == 1

    active_memories = service.store.get_all_memories(repo.repository_id, include_superseded=False)
    assert len(active_memories) == 1
    assert active_memories[0].claim == identical_claim
