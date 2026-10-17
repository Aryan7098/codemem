# Architectural & Technical Decisions Log (`decisions.md`)

This document tracks all meaningful architectural, technical, and implementation decisions made in the codebase, along with the rationale, tradeoffs evaluated, and alternatives considered.

---

## Decision Log

### ADR-001: Package & Environment Management with `uv`
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** The environment is Windows with Python 3.14.5 and `uv` 0.11.25 installed. The Windows Store default `python.exe` alias was pointing to a stub rather than the active interpreter. Fast, deterministic dependency resolution and virtual environment creation are needed for both development and judge evaluation.
- **Decision:** Use `uv` as the primary package and project manager with `pyproject.toml` standards (PEP 517/518/621). Explicitly target the verified Python interpreter at `C:\Users\azure\AppData\Local\Python\bin\python.exe` when creating environments or executing tools.
- **Alternatives Considered:**
  - Standard `pip` + `venv`: Slower resolution, prone to path collision with the WindowsApps dummy stub.
  - `poetry`: Slower lockfile resolution, extra overhead compared to `uv`.
- **Tradeoffs & Consequences:** Fast installations and reproducible lockfiles, but requires documented fallback commands (`python -m pip install .`) in the README for judges who may not have `uv` pre-installed.

---

### ADR-002: Authoritative Graph Database Client (`falkordb` Python Library)
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** PRD Section 1 & 6 designate FalkorDB as the authoritative graph database for persistent memory, relationships, and queries.
- **Decision:** Use the official `falkordb` Python client library (`falkordb>=1.8.0`) communicating with FalkorDB over Redis protocol. Keep database interactions encapsulated within an isolated `FalkorDBAdapter` layer.
- **Alternatives Considered:**
  - Generic raw Redis client with manual `GRAPH.QUERY` calls: Fragile and lacks type safety/result mapping.
  - Wrapping Graphiti or external memory frameworks: Explicitly prohibited by PRD Section 3 (Non-goals: "Wrapping Graphiti as the main memory implementation... must be project-owned new work").
- **Tradeoffs & Consequences:** Direct access to native Cypher queries and FalkorDB graph structures. Requires clear connection abstraction to handle network timeouts and provide clear diagnostic errors when the database instance is unavailable.

---

### ADR-003: Core Architecture Separation (Transport vs. Core Service)
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** The memory layer must be accessible via both CLI (`codemem`) and MCP server without duplicating validation, scoping, or lifecycle logic (PRD Section 6 & 9).
- **Decision:** Strict separation of layers:
  1. **Transport Layer:** CLI (`codemem.cli`) and MCP Server (`codemem.mcp`) are thin adapters only.
  2. **Core Domain Layer:** `codemem.core` manages schemas, repository scoping, idempotency, temporal status transitions, and abstention policies.
  3. **Data/Integration Layer:** `codemem.graph` (FalkorDB Cypher adapter) and `codemem.repository` (Git & file hasher).
- **Alternatives Considered:**
  - Embedding domain logic directly inside CLI handlers or MCP tool functions: Leads to duplicate code and divergence between CLI and MCP behaviors.
- **Tradeoffs & Consequences:** Ensures identical behavior and guarantees zero discrepancy between CLI and MCP tools.

---

### ADR-004: FalkorDB Deployment & Dual-Backend Graph Architecture (Live FalkorDB + In-Memory Test Double)
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** Docker is currently not installed on the developer's Windows machine, but PRD Section 6, 13, and 18 mandate a persistent `docker-compose.yml` environment for judges and live testing.
- **Decision:** 
  1. Provide full `docker-compose.yml` configuration (with health checks and persistent volume mount for `falkordb/falkordb:latest`).
  2. Implement the authoritative `FalkorDBAdapter` using native Cypher queries against FalkorDB.
  3. Provide an in-memory graph test double (`InMemoryGraphStore`) implementing the identical `GraphStoreProtocol`.
  4. Use an environment configuration switch (`GRAPH_BACKEND=falkordb` or `GRAPH_BACKEND=memory`) so development, unit tests, and offline flows run immediately without requiring local Docker right now, while allowing seamless transition to live Docker FalkorDB once Docker Desktop is installed.
- **Alternatives Considered:**
  - Block development until Docker Desktop is installed: Unnecessary delay when core logic, models, CLI, and MCP servers can be developed and verified immediately.
  - Rely solely on in-memory storage: Violates PRD requirement that FalkorDB is the authoritative graph store.
- **Tradeoffs & Consequences:** Requires maintaining a protocol-compliant test double alongside the Cypher adapter, but enables rapid test execution, CI compatibility, and zero-downtime transition to live Docker.

---

### ADR-005: Team Ownership Split & Contract-First Integration
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** PRD Section 16 defines a two-person team. The user and AI assistant represent **Owner A** (Core Engine & Data Store), while a human teammate represents **Owner B** (Transports, CLI wiring, MCP Server, Demo Agents).
- **Decision:**
  1. We focus strictly on the Core Engine deliverables:
     - Memory data models & schema (`src/codemem/core/models.py`)
     - Memory service lifecycle, versioning, supersession & abstention rules (`src/codemem/core/service.py`)
     - FalkorDB graph adapter & Cypher query engine (`src/codemem/graph/adapter.py`)
     - Git metadata & file hash freshness checker (`src/codemem/repository/git.py`)
     - Evaluation harness & test suites (`src/codemem/evaluation/`)
  2. Deliver an **Interface-First Contract Package** for the teammate:
     - Clear documentation (`docs/memory-schema.md` and `docs/api-contract.md`)
     - Frozen typed Pydantic models for inputs and outputs
     - Mockable core service interface so the teammate can build and test MCP tools and CLI independently without waiting for the live database.
- **Alternatives Considered:**
  - Building both halves simultaneously without freezing contracts: Risks merge conflicts and breaking the teammate's integration branch.
- **Tradeoffs & Consequences:** Clear boundaries and zero blocker for parallel development, requiring disciplined contract stability before making changes to public service methods.

---

### ADR-006: Deterministic-First Local Execution & Direct Coding-Agent Memory
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** The system is built for coding agents (LLMs running in developer environments, IDEs, and CLI tools) to share memory directly across sessions without external web apps or mandatory paid APIs. PRD Section 3 & 12 enforce that deterministic retrieval and offline evaluation must work 100% reliably.
- **Decision:**
  1. Build extraction, symbol resolution, graph queries, freshness verification, and abstention using deterministic, codebase-native methods first (AST parsing, Git metadata, file hashing, Cypher queries).
  2. Implement an abstract `LLMProvider` interface with a `DeterministicProvider` as default, allowing optional pluggable model providers (Gemini, OpenAI, Ollama) without coupling core graph memory logic to external APIs.
  3. No web dashboards or hosted UIs; coding agents interact directly through the local Core API and MCP server.
- **Alternatives Considered:**
  - Mandatory LLM calls for fact extraction/summarization: Introduces flakiness, token costs, rate limits, and failure modes when API keys are absent.
- **Tradeoffs & Consequences:** Guarantees 100% reproducible tests and fast evaluation for hackathon judges, while remaining fully compatible with external LLM agents connecting over MCP.

---

### ADR-007: Ingestion & Trust Architecture ("Agents Propose, Memory Engine Manages Trust") and Trap Guardrails
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** Neither pure AST parsing (cannot understand semantic architecture) nor pure LLM extraction (hallucinates, cannot verify ground truth) is sufficient on its own.
- **Decision:** Adopt a hybrid 4-step pipeline:
  1. **AST Parser (Deterministic):** Automatically extracts ground-truth symbols, classes, methods, imports, and dependencies.
  2. **Calling Coding Agent:** Proposes high-level architectural claims (e.g., *"All write requests pass through RateLimiter"*).
  3. **Validation Engine:** Validates the claim's anchors (files exist, symbols exist, content hash matches) and assigns controlled status (`INFERRED` vs `OBSERVED` vs `VERIFIED`).
  4. **FalkorDB Graph Store:** Records versioned claim, evidence nodes, temporal provenance, and cross-session relationships.
- **Explicit Anti-Traps & Implementation Guardrails:**
  - **Trap 1: The Static Analysis Rabbit Hole:** Do NOT attempt full symbolic execution or whole-program theorem proving. Use **Anchor Verification** (verify file existence, AST symbol match, content SHA-256 match, test exit code 0). If any implementation risks drifting into a compiler/symbolic-execution project, alert the user immediately.
  - **Trap 2: Line-Number Fragility:** Never anchor evidence solely to volatile line numbers. Anchor evidence to **`content_hash` (SHA-256) + AST symbol identifier (`Class.method`)**.
  - **Trap 3: Full-Repo Ingestion Overhead:** Do NOT run synchronous whole-repo AST scans on single-fact writes. Perform **Targeted/On-Demand AST Validation** strictly on the files referenced in evidence.
- **Tradeoffs & Consequences:** Keeps the engine lightweight, fast, and model-agnostic while ensuring high trust and preventing hallucinated memories from entering the graph as verified facts.

---

### ADR-008: Staged Hybrid Retrieval Pipeline & Evidence-Aware Ranking
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** Recalling code memories must bridge high-level semantic questions (e.g., "how is login done?") with low-level graph representations (e.g., `(Concept:Login)-[:IMPLEMENTED_BY]->(Middleware:Auth)`) without returning obsolete facts or falling into vector drift.
- **Decision:** Implement a staged retrieval architecture:
  1. **Stage 1 (P0): Graph Traversal + Keyword/Alias Expansion (Deterministic):** Exact identifier/path matching and relationship traversal (e.g. Concept $\rightarrow$ Component $\rightarrow$ Evidence) in Cypher with deterministic alias mapping.
  2. **Stage 2 (P1): Local Offline Vector Similarity:** Supplementary dense embeddings via local CPU-friendly models (`fastembed` / `all-MiniLM-L6-v2`) only when exact matching yields weak candidates.
  3. **Stage 3: Evidence-Aware Reranking & Controlled Abstention:** Rank candidates using the formula:
     $$\text{FinalScore} = \text{MatchScore} \times \text{StatusWeight} \times \text{FreshnessMultiplier}$$
     where status weights are: `VERIFIED` = 1.0, `OBSERVED` = 0.85, `INFERRED` = 0.60, `STALE` = 0.20, and `SUPERSEDED` / `CONTRADICTED` = 0.0 (excluded from current recall). If the top candidate's `FinalScore` is below the abstention threshold, return `abstained=True`.
- **Explicit Anti-Traps & Implementation Guardrails:**
  - **Trap 1: Unbounded Graph Hop (Cypher Cartesian Explosion):** Hardcode Cypher relationship traversal depth to a maximum of 2 hops (`[:RELATES_TO*1..2]`) and enforce `WHERE m.repository_id = $repo_id` across every traversal step.
  - **Trap 2: Stale High-Cosine Trap:** Never rank purely on cosine text similarity. All vector candidates MUST pass through status and freshness multipliers.
  - **Trap 3: Vague Hallucination Trap:** Strictly enforce conservative abstention when evidence is insufficient or contradictory.
- **Tradeoffs & Consequences:** Delivers a fully offline, fast, deterministic baseline that complies with LongMemEval evaluation metrics while allowing semantic enrichment without external API dependencies.

---

### ADR-009: Hybrid Repository Identity Resolution & Fallback Policy
- **Date:** 2026-10-15
- **Status:** Accepted
- **Context:** PRD Section 6 & 14 mandate strict repository scoping. A Git remote URL alone cannot differentiate monorepo subprojects, and root commits can change during rebases or squashes. Target codebases must not be polluted by default, but explicit configuration should be respected.
- **Decision:** Implement a multi-tier fallback resolution policy for `repository_id`:
  1. **Tier 1 (Explicit Configuration):** If `.codemem/config.json` exists in the repository root, load `repository_id` directly from it.
  2. **Tier 2 (Normalized Git Composite Hash):** If in a Git repository:
     - Normalize Git Remote URL (strip SSH vs HTTPS prefixes, authentication tokens, `.git` extension).
     - Compute relative path from Git root to the target project directory (cleanly isolates monorepo subprojects).
     - Incorporate initial root commit hash if stable.
     - Formula: `SHA256(normalized_remote_url + ":" + relative_subpath + ":" + root_commit)[:16]`
  3. **Tier 3 (Local Path Fallback):** If not a Git repository or Git metadata is unavailable:
     - Canonicalize absolute path on the local filesystem (`os.path.realpath`).
     - Formula: `"local-" + SHA256(canonical_path)[:12]`
     - Mark repository state as `git_available=False` with commit-based freshness features explicitly flagged as degraded.
- **Tradeoffs & Consequences:** Completely non-invasive for clean target repos, immune to monorepo collisions, stable across multi-machine checkouts, and gracefully handles non-Git codebases.

---

### ADR-010: Elimination of Throwaway Mock Service in Favor of Real Service with InMemoryGraphStore
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** PRD Section 16 suggested creating a mocked core-service interface for parallel teammate development. However, building a separate mock service that returns fake hardcoded responses introduces throwaway code, duplicates interfaces, and requires the teammate to rewrite integration points later.
- **Decision:** Do NOT build a separate fake `MockMemoryService`. Instead, provide the production `MemoryService` directly, powered by `InMemoryGraphStore` as its storage engine. The teammate imports and interacts with the exact production `MemoryService` API immediately.
- **Alternatives Considered:**
  - Hardcoded Mock Service: Fake responses provide no real validation, create maintenance overhead, and diverge from actual domain logic.
- **Tradeoffs & Consequences:** Eliminates throwaway code and integration friction. When FalkorDB is connected, the teammate modifies zero lines of service invocation code.

---

### ADR-011: Packaging, Dependency Stack & Environment Scaffolding (Step 1)
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** Milestone 1 Step 1 requires a standardized Python package foundation supporting both developer environments (Windows with Python 3.14 + `uv`) and judge evaluation (any OS with Docker Compose / pip).
- **Decision:**
  1. Use modern `pyproject.toml` (PEP 621) with `hatchling` build backend and editable installs (`pip install -e ".[dev]"`).
  2. Pin core dependencies: `pydantic>=2.7.0` (strict schema validation), `pydantic-settings>=2.2.0` (typed `.env` loading), `falkordb>=1.8.0` (official graph client), `redis>=5.0.0`, `mcp>=1.0.0` (Model Context Protocol), `click>=8.1.0` (CLI entry point), `pytest>=8.0.0` (test runner).
  3. Provide `docker-compose.yml` for judges to boot FalkorDB (`falkordb/falkordb:latest`) with named persistent volume `codemem_falkordb_data` and healthcheck (`redis-cli ping`).
  4. Provide `.env.example` with safe defaults (`GRAPH_BACKEND=memory`, `DEFAULT_WORKSPACE_ID=default`, `MIN_CONFIDENCE_THRESHOLD=0.45`).
- **Tradeoffs & Consequences:** Pinned, verified dependencies guarantee zero environment surprises across Python versions, while persistent Docker storage guarantees memory graph survival across container restarts.

---

### ADR-012: Interface Contract & Schema Normalization (Step 2)
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** To ensure unblocked, parallel development between Owner A (Core Engine) and Owner B (MCP Server, CLI, Demo Agents), explicit input/output models, status state machines, and error contracts must be defined in advance.
- **Decision:**
  1. Author and freeze `docs/memory-schema.md` establishing graph node definitions (`Repository`, `Memory`, `Evidence`, `Agent`, `Session`, `Concept`), graph edges (`ABOUT_REPO`, `SUPPORTED_BY`, `DISCOVERED_BY`, `RECORDED_IN`, `SUPERSEDES`, `CONTRADICTS`), and the 6-status lifecycle (`OBSERVED`, `VERIFIED`, `INFERRED`, `STALE`, `SUPERSEDED`, `CONTRADICTED`).
  2. Author and freeze `docs/api-contract.md` detailing the 7 service methods (`register_repository`, `remember`, `recall`, `update`, `history`, `explain`, `check_changes`) and structured error codes (`REPO_NOT_FOUND`, `SCOPE_VIOLATION`, `INVALID_EVIDENCE_PATH`, etc.).
- **Tradeoffs & Consequences:** Locks interface boundaries early, preventing breaking changes between CLI, MCP server, and core engine.

---

### ADR-013: Domain Data Models, Strict Boundary Validation & Path Sanitization (Step 3)
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** Ingesting untrusted claims from coding agents and CLI transports can corrupt the memory graph or cause directory traversal vulnerabilities if raw dictionaries are used without boundary validation.
- **Decision:**
  1. Implement typed Pydantic v2 domain models in `src/codemem/core/models.py`.
  2. Enforce strict enums: `MemoryStatus` (`OBSERVED`, `VERIFIED`, `INFERRED`, `STALE`, `SUPERSEDED`, `CONTRADICTED`), `MemoryType`, and `EvidenceKind`.
  3. Implement boundary security validators: `validate_safe_relative_path` rejects directory traversal attempts (`..`), leading slashes, and Windows drive roots (`C:`), ensuring evidence files strictly remain within the target repository boundary.
  4. Enforce structural validation: `line_end >= line_start` and calibrated score boundaries (`0.0 <= score <= 1.0`).
- **Tradeoffs & Consequences:** Adds strict type-checking and automated serialization overhead (~sub-millisecond), but guarantees clean data entering FalkorDB and provides full IDE autocomplete for Owner B (teammate).

---

### ADR-014: GraphStoreProtocol Interface & InMemoryGraphStore Implementation (Step 4)
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** To decouple domain memory rules from graph storage engines and allow immediate offline execution before Docker/FalkorDB is initialized, an abstract protocol and in-memory test double are required.
- **Decision:**
  1. Define `GraphStoreProtocol` in `src/codemem/graph/protocol.py` specifying repository scoping, memory versioning, supersession linking, concept traversal, and file-reference lookups.
  2. Implement `InMemoryGraphStore` in `src/codemem/graph/in_memory.py` satisfying `GraphStoreProtocol`.
  3. Enforce strict repository scope isolation (`m.repository_id == repo_id`) and bounded concept traversal ($\le 2$ hops) to match the FalkorDB Cypher architecture.
- **Tradeoffs & Consequences:** Ensures both live FalkorDB adapter and in-memory store use the exact same typed interface, allowing `MemoryService` and teammate's MCP/CLI code to run seamlessly against either backend.

---

### ADR-015: Core MemoryService & GitAdapter Implementation (Step 5)
- **Date:** 2026-10-16
- **Status:** Accepted
- **Context:** Milestone 1 requires a complete, production-ready `MemoryService` delivering repository scoping, anchor verification, status resolution, hybrid retrieval, history tracking, and stale detection.
- **Decision:**
  1. Implement `GitAdapter` (`src/codemem/repository/git.py`):
     - Executes 3-tier repository ID resolution (Tier 1: config file, Tier 2: normalized Git remote + subpath + root commit, Tier 3: canonical path hash).
     - Performs targeted, on-demand AST parsing to verify symbol existence without whole-repo compilation overhead.
     - Computes SHA-256 hashes of files and code snippets.
  2. Implement `MemoryService` (`src/codemem/core/service.py`):
     - Resolves statuses (`VERIFIED` via tests, `OBSERVED` via AST/hash anchors, `INFERRED` via unanchored claims).
     - Implements evidence-aware reranking formula: $\text{FinalScore} = \text{MatchScore} \times \text{StatusWeight} \times \text{FreshnessMultiplier}$.
     - Implements LongMemEval conservative abstention: abstains with `abstained=True` if candidate score $< 0.45$ or graph has no evidence.
     - Implements `update`: creates new memory revision, links `[:SUPERSEDES]`, and updates previous version status to `SUPERSEDED`.
     - Implements `check_changes`: verifies current file hashes against recorded evidence and transitions affected memories to `STALE`.
- **Tradeoffs & Consequences:** All 7 core API methods are operational and covered by automated unit tests (19/19 passing). The teammate can import `MemoryService(store=InMemoryGraphStore())` and build MCP/CLI transports immediately.

---

### ADR-016: Remote Repository Synchronization & Milestone 1 Delivery
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** Milestone 1 core deliverables (schemas, contracts, in-memory graph store, Git adapter, and core service) are complete and tested. The teammate (Owner B) needs immediate access to start parallel development on CLI and MCP tools.
- **Decision:**
  1. Initialize Git repository with default branch `main`.
  2. Synchronize to public GitHub remote `https://github.com/Aryan7098/codemem.git`.
  3. Ensure `.gitignore` cleanly prevents environment files, virtual environment binaries, cache, and test output from leaking into remote history.
- **Tradeoffs & Consequences:** Provides an immutable, shared base commit for both contributors, enabling branch-based pull requests and reproducible clone testing.










