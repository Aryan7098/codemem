# Code Execution & Flow Map (`code.md`)

This document details how execution travels across files, functions, and modules in the `codemem` codebase: what calls what, execution ordering, data flow pathways, and a ledger of currently modified components.

---

## 1. System Architecture & Component Map

```
┌────────────────────────────────────────────────────────┐
│                   Transports / Entrypoints             │
│   CLI: `src/codemem/cli/main.py`                       │
│   MCP: `src/codemem/mcp/server.py`                     │
└─────────────────────────┬──────────────────────────────┘
                          │ passes validated inputs
                          ▼
┌────────────────────────────────────────────────────────┐
│             Core Domain: `codemem.core`                │
│   `MemoryService` (`service.py`)                       │
│   - Enforces repository/workspace scope                │
│   - Validates memory schema (`models.py`)              │
│   - Evaluates staleness, conflicts & abstention        │
└──────────────┬──────────────────────────┬──────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────────────┐ ┌──────────────────────┐
│  Graph Store: `codemem.graph`│ │ Repo: `codemem.repo` │
│  `FalkorDBAdapter`           │ │ `GitAdapter`         │
│  - Parameterized Cypher      │ │ - Commit hashes      │
│  - Nodes: Repo, Memory,      │ │ - File content SHA   │
│    Evidence, Agent, Session  │ │ - Path boundaries    │
│  - Edges: SUPPORTED_BY, etc. │ └──────────────────────┘
└──────────────────────────────┘
```

---

## 2. Execution Traces & Call Chains

### Flow A: Register Repository (`codemem init --repo <path>`)
```
1. CLI / MCP Entrypoint:
   cli/main.py :: init_cmd(repo_path) OR mcp/server.py
   └─ calls repository/git.py :: GitAdapter.resolve_repo(repo_path)
      ├─ Canonicalizes absolute path & verifies path exists (os.path.realpath)
      ├─ 3-Tier Identity Resolution:
      │  ├─ Tier 1: Check .codemem/config.json if present
      │  ├─ Tier 2 (Git): SHA256(normalized_remote + ":" + relative_subpath + ":" + root_commit)[:16]
      │  └─ Tier 3 (Non-Git Fallback): "local-" + SHA256(canonical_path)[:12] (flags git_available=False)
      └─ Reads current HEAD git commit hash & branch (if git present)

2. Core Service Dispatch:
   core/service.py :: MemoryService.register_repository(repo_info)
   └─ calls graph/adapter.py :: FalkorDBAdapter.upsert_repository(repo_id, path, head_commit)
      └─ Executes Cypher:
         MERGE (r:Repository {id: $repo_id})
         SET r.root_path = $path, r.head_commit = $head_commit, r.updated_at = $now
         RETURN r

3. Output Rendering:
   Returns RepositoryInfo to CLI -> formatted as readable output / JSON.
```

---

### Flow B: Remember a Fact (`remember_memory`)
```
1. Ingestion:
   cli/commands.py OR mcp/server.py :: handle_remember(...)
   └─ Parses input into MemoryInput schema (core/models.py)

2. Core Service Processing:
   core/service.py :: MemoryService.remember(memory_input)
   ├─ Step 1: Scope Check
   │  Verifies `repository_id` exists in graph.
   ├─ Step 2: Evidence Attachment & Anchor Validation (Trap-Safe)
   │  repository/git.py :: GitAdapter.hash_evidence(evidence_ref)
   │  ├─ Computes SHA-256 of snippet and extracts AST symbol identifier (Class.method)
   │  ├─ Targeted AST check: parses ONLY the referenced evidence file on demand
   │  └─ Status resolver:
   │     ├─ Unanchored claim -> INFERRED
   │     ├─ Valid file + AST symbol match + hash match -> OBSERVED
   │     └─ Passing test command check recorded -> VERIFIED
   ├─ Step 3: Duplicate & Conflict Detection
   │  graph/adapter.py :: FalkorDBAdapter.find_active_facts_by_subject_predicate(...)
   │  If contradiction found: marks conflict warning; does NOT delete old version.
   ├─ Step 4: Graph Persistence
   │  graph/adapter.py :: FalkorDBAdapter.create_memory_version(...)
   │  Writes nodes: (:Memory), (:Evidence), (:Agent), (:Session)
   │  Writes relationships:
   │    (:Memory)-[:ABOUT_REPO]->(:Repository)
   │    (:Memory)-[:SUPPORTED_BY]->(:Evidence)
   │    (:Memory)-[:DISCOVERED_BY]->(:Agent)
   │    (:Memory)-[:RECORDED_IN]->(:Session)
   └─ Step 5: Return MemoryRecord with memory_id, logical_fact_id, and status.
```

---

### Flow C: Recall Memory (`recall_memory`)
```
1. Invocation:
   cli/commands.py OR mcp/server.py :: handle_recall(repo_id, query, top_k, include_stale)

2. Core Service Processing:
   core/service.py :: MemoryService.recall(...)
   ├─ Step 1: Pre-retrieval Scope Guard
   │  Enforces query is filtered strictly to `repo_id`. Unscoped queries are rejected.
   ├─ Step 2: Exact Identifier & Path Lookup (Stage 1A)
   │  Checks if query references exact file path, symbol, or commit; matches direct anchors.
   ├─ Step 3: Graph Traversal & Keyword Alias Match (Stage 1B - Depth <= 2)
   │  graph/adapter.py :: FalkorDBAdapter.traverse_concept_graph(repo_id, terms, max_depth=2)
   │  Traverses (Concept)-[:IMPLEMENTED_BY|RELATES_TO*1..2]->(Memory) with repo filter.
   ├─ Step 4: Supplementary Local Dense Vector Match (Stage 2 - P1 fallback)
   │  If Stage 1 candidates < top_k:
   │  core/retrieval/vector.py :: LocalEmbeddingSearcher.query(...)
   ├─ Step 5: Evidence-Aware Reranking & Staleness Filter
   │  Computes FinalScore = MatchScore * StatusWeight * FreshnessMultiplier
   │  (VERIFIED: 1.0, OBSERVED: 0.85, INFERRED: 0.60, STALE: 0.20, SUPERSEDED: 0.0)
   ├─ Step 6: Conservative Abstention Gate (PRD LongMemEval)
   │  If top FinalScore < MIN_CONFIDENCE_THRESHOLD or evidence missing:
   │  Returns MemoryRecallResponse(abstained=True, reason="Insufficient verified evidence").
   └─ Step 7: Return top-k ranked results with full evidence records and provenance warnings.
```

---

### Flow D: Update / Supersede Fact (`update_memory`)
```
1. Invocation:
   core/service.py :: MemoryService.update_memory(target_id, new_claim, reason, new_evidence)
   ├─ Resolves existing logical_fact_id.
   ├─ Creates new Memory node (version N+1).
   ├─ Links graph edge:
   │  (new_version:Memory)-[:SUPERSEDES {reason: $reason, at: $now}]->(old_version:Memory)
   ├─ Updates status of old_version to SUPERSEDED.
   └─ Preserves complete historical chain in graph.
```

---

### Flow E: Change Detection (`check_repository_changes`)
```
1. Invocation:
   core/service.py :: MemoryService.check_changes(repo_id)
   ├─ repository/git.py reads current git diff or file hashes since recorded commits.
   ├─ graph/adapter.py finds all (:Memory) linked to changed files/symbols.
   ├─ Updates (:Memory).status = "STALE" for affected nodes.
   └─ Returns affected memories and recommended revalidation actions.
```

---

## 3. Active File Ledger & Modification Tracker

| File Path | Responsibility | Status |
|-----------|----------------|--------|
| `PRD.md` | Requirements specification | Frozen specification |
| `decisions.md` | ADRs and architectural rationale | Active & Updated (ADR-001 to ADR-015) |
| `code.md` | Call graphs, execution paths, file roles | Active & Updated |
| `.gitignore` | Ignore cache, venv, secrets, FalkorDB data | Implemented (Step 1) |
| `docker-compose.yml` | Containerized FalkorDB + persistent volume + healthcheck | Implemented (Step 1) |
| `.env.example` | Configuration templates & safe defaults | Implemented (Step 1) |
| `pyproject.toml` | Package dependencies, entrypoints, and build metadata | Implemented & Installed (Step 1) |
| `src/codemem/__init__.py` | Package version and initialization | Implemented (Step 1) |
| `README.md` | Project overview and quickstart | Initialized (Step 1) |
| `docs/memory-schema.md` | Graph schema, nodes, edges & status state machine | Implemented (Step 2) |
| `docs/api-contract.md` | Service methods, request/response models & error codes | Implemented (Step 2) |
| `src/codemem/core/models.py` | Pydantic data schemas & boundary validators | Implemented (Step 3) |
| `src/codemem/core/__init__.py` | Core model exports | Implemented (Step 3) |
| `tests/unit/test_models.py` | Unit test suite for models (6/6 passing) | Implemented & Verified (Step 3) |
| `src/codemem/graph/protocol.py` | GraphStoreProtocol interface | Implemented (Step 4) |
| `src/codemem/graph/in_memory.py` | InMemoryGraphStore with bounded concept traversal | Implemented (Step 4) |
| `src/codemem/graph/__init__.py` | Graph package exports | Implemented (Step 4) |
| `tests/unit/test_in_memory_graph.py` | Unit tests for graph store (6/6 passing) | Implemented & Verified (Step 4) |
| `src/codemem/repository/git.py` | GitAdapter with 3-tier identity & AST inspection | Implemented (Step 5) |
| `src/codemem/repository/__init__.py` | Repository package exports | Implemented (Step 5) |
| `src/codemem/core/service.py` | Production MemoryService domain engine | Implemented (Step 5) |
| `tests/unit/test_service.py` | Unit tests for MemoryService (7/7 passing) | Implemented & Verified (Step 5) |

