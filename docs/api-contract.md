# CodeMem: Core Service API & Integration Contract

This document specifies the exact method signatures, input payloads, return models, and error codes for the CodeMem core service. Both the CLI (`codemem`) and MCP Server (`codemem.mcp`) invoke these methods directly.

---

## 0. Architectural Principles & Integration Guarantees

1. **Team Ownership Boundary:**
   - **Owner A (Core Engine):** Implements `MemoryService`, data validation, FalkorDB adapter, `InMemoryGraphStore`, and Git change-checking.
   - **Owner B (Teammate / Transports):** Implements the CLI (`codemem.cli`), MCP Server (`codemem.mcp`), and demo agents.
2. **Dual-Backend Parity:**
   - `MemoryService` supports both `falkordb` and `memory` backends via a unified interface. Your teammate can test CLI and MCP tools immediately against `InMemoryGraphStore` without needing Docker running.
3. **Trust & Ingestion Rule:**
   - *"Agents propose knowledge; the memory engine manages trust."* High-level architectural claims submitted by agents are verified against AST symbols and content hashes. Status transitions follow strict anchor rules:
     - `INFERRED`: Proposed by an agent without verifiable AST or test anchors.
     - `OBSERVED`: AST symbol and snippet SHA-256 verified in the repository.
     - `VERIFIED`: Supported by a passing test execution command.
4. **Retrieval & Evidence-Aware Ranking Formula:**
   - Recall uses hybrid graph traversal ($\le 2$ hops) and keyword matching, ranked by:
     $$\text{FinalScore} = \text{MatchScore} \times \text{StatusWeight} \times \text{FreshnessMultiplier}$$
     *(VERIFIED: 1.0, OBSERVED: 0.85, INFERRED: 0.60, STALE: 0.20, SUPERSEDED: 0.0)*.
   - If $\text{FinalScore} < 0.45$, the service abstains with `abstained = true`.
5. **3-Tier Repository Identity Resolution:**
   - Tier 1: `.codemem/config.json` if explicitly initialized.
   - Tier 2 (Git): `SHA256(normalized_remote_url + ":" + relative_subpath + ":" + root_commit)[:16]`.
   - Tier 3 (Local non-git fallback): `"local-" + SHA256(canonical_path)[:12]` (marks `git_available = false`).

---

## 1. Core Service Methods

### 1. `register_repository(path: str) -> RepositoryInfo`
Resolves, validates, and initializes repository scope using the 3-tier resolution policy.
- **Parameters:**
  - `path` (string, required): Absolute or relative filesystem path to the target repository root.
- **Returns (`RepositoryInfo`):**
  - `repository_id` (string): Stable 16-char repository hash.
  - `root_path` (string): Canonical resolved absolute directory path.
  - `head_commit` (string | null): Current Git HEAD commit.
  - `git_available` (boolean): `true` if valid git repository.
- **Error Codes:** `REPO_PATH_NOT_FOUND`, `INVALID_DIRECTORY`.

---

### 2. `remember(input: MemoryInput) -> MemoryRecord`
Stores a new codebase fact, anchors its evidence, and commits it to the graph.
- **Input (`MemoryInput`):**
  - `repository_id` (string, required)
  - `claim` (string, required): Clear natural language fact statement.
  - `subject` (string, optional): Component or entity name (e.g. `"AuthMiddleware"`).
  - `predicate` (string, optional): Relation (e.g. `"validates"`).
  - `object` (string, optional): Target (e.g. `"JWT tokens"`).
  - `memory_type` (enum, default `"architecture"`): `architecture`, `dependency`, `api_contract`, `decision`, `convention`, `bug`, `handoff`.
  - `source_agent_id` (string, default `"unknown-agent"`)
  - `source_session_id` (string, default `"default-session"`)
  - `evidence` (list of `EvidenceInput`, optional):
    - `file_path` (string, required): Relative path within repo.
    - `symbol` (string, optional): AST symbol (e.g. `"validate_token"`).
    - `line_start` (int, optional)
    - `line_end` (int, optional)
    - `excerpt` (string, optional): Code snippet.
    - `test_command` (string, optional): Verification test command.
  - `tags` (list of strings, optional): Conceptual tags.
- **Returns (`MemoryRecord`):**
  - Complete memory node object including `memory_id`, `logical_fact_id`, computed `status` (`OBSERVED`, `VERIFIED`, `INFERRED`), `confidence_score`, and attached `evidence_records`.
- **Error Codes:** `REPO_NOT_FOUND`, `INVALID_EVIDENCE_PATH`, `DATABASE_ERROR`.

---

### 3. `recall(repo_id: str, query: str, top_k: int = 5, include_stale: bool = False) -> MemoryRecallResponse`
Searches repository memories using hybrid graph traversal, keyword matching, and evidence-aware reranking.
- **Parameters:**
  - `repo_id` (string, required): Isolation scope.
  - `query` (string, required): Natural language search prompt.
  - `top_k` (int, default 5): Maximum candidate count.
  - `include_stale` (bool, default false): Include STALE memories with warnings.
- **Returns (`MemoryRecallResponse`):**
  - `results` (list of `ScoredMemoryRecord`): Ranked memories with attached evidence and freshness warnings.
  - `abstained` (boolean): `true` if evidence is insufficient, contradicted, or below `MIN_CONFIDENCE_THRESHOLD`.
  - `abstain_reason` (string | null): Explanation when abstaining (e.g., *"No verified evidence found in repository graph"*).
- **Error Codes:** `REPO_NOT_FOUND`, `INVALID_QUERY`.

---

### 4. `update(memory_id: str, new_claim: str, reason: str, repo_id: str, new_evidence: list = None) -> MemoryRecord`
Supersedes an existing memory with a new version without deleting history.
- **Parameters:**
  - `memory_id` (string, required): ID of the memory version to update.
  - `new_claim` (string, required): Corrected statement.
  - `reason` (string, required): Audit reason for change.
  - `repo_id` (string, required): Scope verification.
  - `new_evidence` (list of `EvidenceInput`, optional): Fresh code evidence.
- **Returns (`MemoryRecord`):**
  - New memory version linked to the prior version via `[:SUPERSEDES]` edge. The previous version status transitions to `SUPERSEDED`.
- **Error Codes:** `MEMORY_NOT_FOUND`, `SCOPE_VIOLATION`.

---

### 5. `history(identifier: str, repo_id: str) -> MemoryHistoryResponse`
Retrieves chronological version history and supersession links for a memory or logical fact.
- **Parameters:**
  - `identifier` (string, required): `memory_id` or `logical_fact_id`.
  - `repo_id` (string, required): Scope verification.
- **Returns (`MemoryHistoryResponse`):**
  - `logical_fact_id` (string)
  - `versions` (list of `MemoryRecord`): Chronologically ordered from newest to oldest.
  - `supersession_chain` (list of links with change reasons and timestamps).
- **Error Codes:** `FACT_NOT_FOUND`, `SCOPE_VIOLATION`.

---

### 6. `explain(memory_id: str, repo_id: str) -> MemoryExplanation`
Returns full inspectable provenance for a specific memory.
- **Returns (`MemoryExplanation`):**
  - `memory`: Target memory record.
  - `evidence_records`: Full list of attached evidence records (files, symbols, hashes, excerpts, test results).
  - `provenance`: Originating agent, session, transaction timestamp (`recorded_at`), and commit hash.
  - `freshness`: Current verification status, staleness alerts, and changed file paths.

---

### 7. `check_changes(repo_id: str) -> ChangeDetectionResponse`
Compares recorded evidence file hashes against current repository state and flags stale memories.
- **Returns (`ChangeDetectionResponse`):**
  - `stale_count` (int)
  - `affected_memories` (list of `MemoryRecord` transitioned to `STALE`)
  - `revalidation_recommendations` (list of actionable steps for agents)

---

## 2. Standard Structured Error Codes

All exceptions raised by the service or returned to transports follow the `CodeMemError` contract:
```json
{
  "error_code": "SCOPE_VIOLATION",
  "message": "Target memory belongs to repo_abc, but query specified repo_xyz.",
  "details": {}
}
```

| Error Code | HTTP/MCP Equivalent | Meaning |
|---|---|---|
| `REPO_NOT_FOUND` | 404 | Repository ID is not registered in graph |
| `SCOPE_VIOLATION` | 403 | Memory does not belong to specified repo scope |
| `MEMORY_NOT_FOUND` | 404 | Memory version ID does not exist |
| `FACT_NOT_FOUND` | 404 | Logical fact ID has no records |
| `INVALID_EVIDENCE_PATH` | 400 | File path escapes repository boundary or does not exist |
| `DATABASE_UNAVAILABLE` | 503 | FalkorDB connection failed and offline fallback disabled |
