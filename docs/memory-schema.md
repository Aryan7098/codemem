# CodeMem: Memory Graph Schema & Lifecycle Specification

This document defines the authoritative graph schema, node properties, relationship types, and status state machine for the CodeMem shared memory engine.

---

## 1. Graph Data Model Overview

The CodeMem graph layer is repository-scoped and version-preserving. Old versions of knowledge are never silently mutated or deleted; changes create new `(:Memory)` nodes linked by explicit version edges.

```
       (:Agent)
          ▲
          │ [:DISCOVERED_BY]
          │
      (:Memory) ──[:ABOUT_REPO]──► (:Repository)
       │    │
       │    ├─[:RECORDED_IN]─────► (:Session)
       │    ├─[:SUPPORTED_BY]────► (:Evidence)
       │    └─[:SUPERSEDES]──────► (:Memory [prior version])
       │
       └─[:RELATES_TO]───────────► (:Concept)
```

---

## 2. Graph Nodes & Property Specifications

### `(:Repository)`
Represents an isolated codebase boundary.
- `id` (string, primary key): 16-char composite hash or explicit identifier.
- `root_path` (string): Canonical local file path.
- `head_commit` (string, optional): Current Git HEAD commit hash when registered.
- `remote_url` (string, optional): Normalized Git origin remote URL.
- `git_available` (boolean): Flag indicating whether Git freshness tracking is active.
- `created_at` (ISO8601 string): Initial registration timestamp.
- `updated_at` (ISO8601 string): Last sync timestamp.

### `(:Memory)`
A single immutable version of a repository fact.
- `id` (string, primary key): UUID uniquely identifying this specific version (`mem_<uuid>`).
- `logical_fact_id` (string): Shared identifier across all revisions of the same conceptual fact (`fact_<uuid>`).
- `repository_id` (string, indexed): Enforces isolation boundary.
- `workspace_id` (string): Isolation tenant (default: `"default"`).
- `subject` (string): Entity or component (e.g., `"AuthMiddleware"`).
- `predicate` (string): Relationship or attribute (e.g., `"validates"`).
- `object` (string): Target or specification (e.g., `"JWT tokens"`).
- `claim` (string): Normalized plain-language statement.
- `memory_type` (enum): `architecture`, `dependency`, `api_contract`, `decision`, `convention`, `bug`, `handoff`.
- `status` (enum): Controlled status (see Section 4).
- `confidence_score` (float, 0.0 to 1.0): Calibrated confidence score.
- `source_session_id` (string): Originating session.
- `source_agent_id` (string): Originating agent.
- `recorded_at` (ISO8601 string): Transaction time (when recorded in DB).
- `valid_from` (ISO8601 string, optional): Valid time start.
- `valid_to` (ISO8601 string, optional): Valid time end.
- `source_commit` (string, optional): Git commit where evidence was observed.
- `tags` (list of strings): Keyword tags for alias expansion.

### `(:Evidence)`
Anchors supporting a memory claim.
- `id` (string, primary key): UUID (`evi_<uuid>`).
- `repository_id` (string): Enforces scope.
- `kind` (enum): `source_span`, `file_symbol`, `commit`, `test_result`, `command_output`.
- `file_path` (string, relative to repo root): E.g., `"src/security/jwt.py"`.
- `symbol` (string, optional): E.g., `"TokenValidator.verify_token"`.
- `line_start` (integer, optional): Starting line reference.
- `line_end` (integer, optional): Ending line reference.
- `content_hash` (string, SHA-256): Hash of the exact code snippet/anchor.
- `commit_hash` (string, optional): Commit hash when evidence was extracted.
- `excerpt` (string, max 1000 chars): Exact text snippet.
- `test_command` (string, optional): Reproducible test command (e.g., `pytest tests/test_auth.py`).
- `test_passed` (boolean, optional): Test execution status.
- `observed_at` (ISO8601 string): Extraction timestamp.

### `(:Agent)` & `(:Session)`
Audit trail and provenance tracking.
- `Agent.id`: Unique agent name/identifier (e.g., `"agent-investigator-01"`).
- `Session.id`: Unique session run identifier (e.g., `"sess_20261016_001"`).

---

## 3. Relationship Types

- `(:Memory)-[:ABOUT_REPO]->(:Repository)`: Restricts memory to repository scope.
- `(:Memory)-[:SUPPORTED_BY]->(:Evidence)`: Traces claim to inspectable code ground-truth.
- `(:Memory)-[:DISCOVERED_BY]->(:Agent)`: Attribution to originating agent.
- `(:Memory)-[:RECORDED_IN]->(:Session)`: Attribution to originating session.
- `(:Memory)-[:SUPERSEDES {reason: string, at: ISO8601}]->(:Memory)`: Connects new revision to replaced revision.
- `(:Memory)-[:CONTRADICTS {reason: string, at: ISO8601}]->(:Memory)`: Records conflicting evidence without destructive overwrite.
- `(:Memory)-[:RELATES_TO]->(:Concept)`: Semantic alias connection for multi-hop graph retrieval.

---

## 4. Status Lifecycle & Transition State Machine

```
      [New Claim Extracted]
                 │
                 ├───────────────────────┬────────────────────────┐
                 ▼                       ▼                        ▼
        (Has Test Result)       (Has AST Anchor)         (Unanchored Claim)
                 │                       │                        │
                 ▼                       ▼                        ▼
            VERIFIED                 OBSERVED                  INFERRED
                 │                       │                        │
                 └───────────┬───────────┴────────────────────────┘
                             │
                             ├──────────────► [Code Modified / Hash Mismatch]
                             │                       │
                             │                       ▼
                             │                     STALE
                             │                       │
                             ├──────────────► [Newer Version Created]
                             │                       │
                             │                       ▼
                             │                   SUPERSEDED
                             │
                             └──────────────► [Contradictory Evidence]
                                                     │
                                                     ▼
                                                CONTRADICTED
```

### Controlled Status Definitions:
1. **`OBSERVED`**: The claim has inspectable anchors in the codebase (the referenced file exists, the AST symbol exists, and snippet content hash matches).
2. **`VERIFIED`**: The claim is supported by a passing test result or deterministic verification command execution.
3. **`INFERRED`**: The claim was proposed by an agent without verifiable AST or test anchors.
4. **`STALE`**: The underlying code file or commit hash has changed since evidence was captured. Needs revalidation.
5. **`SUPERSEDED`**: A newer revision of this fact has been accepted. The old fact is retained for historical traceability but excluded from default recall.
6. **`CONTRADICTED`**: Credible conflicting evidence exists. Preserved for inspection.

---

## 5. Implementation Guardrails for Graph Operations

1. **Anti-Line-Volatility Guardrail:**
   Evidence records must never rely solely on volatile line numbers. The ground truth anchor is always the **`content_hash` (SHA-256 of snippet) + AST symbol path (`ClassName.method_name`)**.
2. **Bounded Traversal Depth:**
   Cypher queries traversing concept-memory relations must hardcode maximum depth to 2 hops (`[:RELATES_TO*1..2]`) and enforce `WHERE m.repository_id = $repo_id` to prevent Cartesian graph explosions.
3. **No Whole-Program Static Proving:**
   Verification is restricted to inspectable anchors (file existence, AST symbol match, snippet hash match, test exit code 0) rather than complex symbolic theorem execution.

