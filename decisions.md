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

---

### ADR-017: Multi-Version MCP Compatibility (FastMCP v1 and MCPServer v2)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** Owner B implemented the MCP transport server using `mcp` SDK. In `mcp` 2.x, `FastMCP` was migrated to `mcp.server.mcpserver.MCPServer`, while `mcp` 1.x exports `mcp.server.fastmcp.FastMCP`. Since `pyproject.toml` pins `mcp>=1.0.0`, environments may resolve either 1.x or 2.x.
- **Decision:**
  1. Use resilient import fallback:
     ```python
     try:
         from mcp.server.fastmcp import FastMCP
     except ImportError:
         from mcp.server.mcpserver import MCPServer as FastMCP
     ```
  2. Maintain identical `@mcp.tool(name=...)` decorator registration and method bindings for all 7 contract methods (`register_repository`, `remember`, `recall`, `update`, `history`, `explain`, `check_changes`).
- **Tradeoffs & Consequences:** Guarantees forward and backward compatibility across different virtual environment installations of the MCP SDK without breaking the public interface or tool decorator behavior.

---

### ADR-018: CLI State Persistence via Local JSON Storage (`.codemem/store.json`)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** CLI commands execute as separate, short-lived process invocations. While `FalkorDBAdapter` connects to an external persistent database daemon, development and offline workflows run on `InMemoryGraphStore`. Without persistence, memories stored via `codemem remember` would be lost before `codemem recall` or `codemem update` could be called.
- **Decision:**
  1. Implement transparent file-backed serialization in `src/codemem/cli/main.py` targeting `.codemem/store.json` (or customizable via `CODEMEM_STORE_PATH` env var).
  2. Store serialized repository nodes, memory records, evidence records, and supersedes edge graphs between CLI invocations.
  3. Automatically hydrate and commit store mutations on state-altering commands (`init`, `remember`, `update`, `check-changes`).
- **Tradeoffs & Consequences:** Allows full multi-command CLI workflows and automated test assertions against `InMemoryGraphStore` without requiring an active Redis/FalkorDB container running in background.

---

### ADR-019: CLI and MCP Error Contract & Structured Output Mapping (`CodeMemErrorDetail`)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** In accordance with `docs/api-contract.md`, both CLI and MCP transports must return machine-readable structured error models matching exact error codes (`REPO_NOT_FOUND`, `SCOPE_VIOLATION`, `MEMORY_NOT_FOUND`, `FACT_NOT_FOUND`, `INVALID_EVIDENCE_PATH`, `INVALID_QUERY`, `INVALID_DIRECTORY`).
- **Decision:**
  1. In MCP tools (`src/codemem/mcp/server.py`), catch exceptions and return `CodeMemErrorDetail(...).model_dump(mode="json")`.
  2. In CLI commands (`src/codemem/cli/main.py`), format error outputs via `_emit_error` with optional `--json` support emitting `CodeMemErrorDetail` to stderr and exiting with status code 1.
- **Tradeoffs & Consequences:** Transports remain thin adapters strictly observing the error code specification from `api-contract.md`, ensuring identical error handling across human CLI and AI agent MCP tool interactions.

---

### ADR-020: Unified Cross-Transport Persistence & Reproducible Multi-Agent Demo
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** To ensure judge reproducibility across both CLI and external MCP coding assistants without data gaps:
  1. If `CodeMemMCPServer` only ran with an ephemeral in-process store, memories recorded via CLI would be invisible to MCP agents, and vice versa.
  2. Multi-session reasoning and handoffs must run across true OS process boundaries to guarantee that memory is not merely passing through global RAM state.
- **Decision:**
  1. Bind `CodeMemMCPServer` to the identical persistent state mechanism (`_get_state_file_path`), automatically flushing mutations to `.codemem/store.json` on `register_repository`, `remember`, `update`, and `check_changes`.
  2. Implement `codemem status` for real-time memory metrics and breakdown by verification status.
  3. Implement `codemem mcp` CLI launcher command for one-command stdio server launch by judges and coding agents.
  4. Implement `examples/demo.py` supporting `--mode all`, `--mode cli`, `--mode mcp`, and `--mode interop`, explicitly verifying cross-session handoff thinking, non-destructive supersession, and conservative abstention.
- **Tradeoffs & Consequences:** Guarantees 100% interoperability between CLI and MCP transports with zero memory divergence, fully reproducible on any judge's machine with or without Docker.

---

### ADR-021: Production FalkorDBAdapter Implementation (Cypher Query Engine)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Sections 1, 6, 13, and 18 establish FalkorDB as the authoritative primary graph database for persistent agent memory, relationships, and multi-hop queries. While `InMemoryGraphStore` enabled early contract verification and offline testing, the system requires a production-grade `FalkorDBAdapter` implementing `GraphStoreProtocol` using parameterized openCypher queries over the official `falkordb` Python driver.
- **Decision:**
  1. Implement `FalkorDBAdapter` in `src/codemem/graph/adapter.py` satisfying `GraphStoreProtocol`:
     - Nodes: `:Repository`, `:Memory`, `:Evidence`, `:Concept`, `:Agent`, `:Session`.
     - Relationships: `(:Memory)-[:ABOUT_REPO]->(:Repository)`, `(:Memory)-[:SUPPORTED_BY]->(:Evidence)`, `(:Memory)-[:SUPERSEDES]->(:Memory)`, `(:Memory)-[:RELATES_TO]->(:Concept)`, `(:Memory)-[:TAGGED_WITH]->(:Concept)`.
  2. Implement parameterized openCypher queries across all protocol methods (`upsert_repository`, `save_memory`, `get_memory`, `get_memories_by_logical_fact`, `supersede_memory`, `query_memories_by_terms`, `traverse_concept_graph`, `get_memories_referencing_file`, `update_memory_status`, `clear`, `health_check`).
  3. Bounded graph traversal: Use Cypher bounded variable-length paths (`MATCH (start:Concept)-[:RELATES_TO*0..2]-(c:Concept)<-[:RELATES_TO]-(m:Memory)`) for concept expansion within depth limits.
  4. Resilient connection architecture: Support `lazy_connect=True` and dependency-injected `graph_client` for zero-friction mock unit testing and graceful fallback when Redis/FalkorDB is offline.
  5. Schema index initialization: Create indices on `Memory(memory_id)`, `Memory(logical_fact_id)`, `Repository(repository_id)`, and `Evidence(file_path)` upon connection.
- **Tradeoffs & Consequences:** Enables high-performance native graph traversals and live Docker FalkorDB persistence without breaking offline unit testing or introducing Docker hard dependencies into CI suites.

---

### ADR-022: Idempotent Deduplication, Evidence Merging, and Contradiction Tracking
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Milestone 2 and Workflow B require the memory engine to reject duplicate fact submissions gracefully and detect semantic conflicts without creating duplicate graph nodes. Agents frequently repeat observations across sessions or submit contradictory claims (e.g. changing authentication strategies).
- **Decision:**
  1. **Normalized Claim & SPO Matching:** In `MemoryService.remember()`, normalize claims (case folding, punctuation stripping, whitespace normalization). Detect duplicates via:
     - Exact normalized claim equivalence.
     - Exact `subject` + `predicate` + `object` equivalence.
  2. **Idempotent Merge & Verification Promotion:** When a duplicate claim is detected:
     - Return the existing `MemoryRecord` without creating duplicate graph nodes.
     - If new evidence anchors are provided, merge them non-destructively by content hash.
     - If the incoming submission contains verified test results (`test_passed=True`), upgrade status from `INFERRED`/`OBSERVED` to `VERIFIED`.
  3. **Contradiction Detection (`contradicts_id`):**
     - When an incoming claim matches an existing record's `subject` + `predicate` but has a conflicting `object`, mark `contradicts_id` linking the new memory to the existing one.
     - In `FalkorDBAdapter`, establish native `(:Memory)-[:CONTRADICTS]->(:Memory)` graph edge.
     - In `InMemoryGraphStore`, track `_contradicts_edges` and persist across restarts.
- **Tradeoffs & Consequences:** Prevents graph bloat from repeated agent observations while making contradictory claims discoverable in provenance graphs without silent overwrites.

---

### ADR-023: Strict Multi-Repository Scope Isolation & Scope Violation Protection
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Section 6 & 13 enforce multi-repository scoping. In multi-agent environments, agents operating on Repo B must never view, mutate, supersede, or query history for memories belonging to Repo A. Cross-repo scope violations must be contained and reported with standardized error codes.
- **Decision:**
  1. **Core Service Level Enforcement:** In `MemoryService` (`update`, `history`, `explain`), inspect `store.get_memory(..., repo_id=None)` if the memory is absent within the requested scope. If the memory exists under a different `repository_id`, raise `PermissionError("Scope violation: memory belongs to...")`.
  2. **Graph Store Partitioning:**
     - In `FalkorDBAdapter`, all query patterns (`MATCH (m:Memory {repository_id: $repo_id})`) strictly parameterize by repository ID.
     - In `InMemoryGraphStore`, all lookups, concept traversals, and file reference queries strictly filter by `repository_id`.
  3. **Transport Error Mapping (`SCOPE_VIOLATION`):**
     - CLI commands (`update`, `history`, `explain`) catch `PermissionError` and emit `SCOPE_VIOLATION` with exit code 1.
     - MCP tools catch `PermissionError` and return `CodeMemErrorDetail(error_code="SCOPE_VIOLATION", ...)`.
- **Tradeoffs & Consequences:** Guarantees zero cross-repository data leaks and provides strict security boundaries across shared team environments and multi-repo agent workflows.

---

### ADR-024: Cross-Process Persistence Architecture Across Restarts
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** To satisfy the "Persistence Across Restart" criterion across both production and offline evaluation environments:
  1. In production, FalkorDB runs as a containerized service with Docker volume mounts (`codemem_falkordb_data` -> `/data`).
  2. In local CLI and MCP development/testing without Docker, state must survive process termination and restarts across different shell sessions.
- **Decision:**
  1. Implement complete JSON-backed graph serialization in `src/codemem/cli/main.py` (`.codemem/store.json`):
     - Serializes all `RepositoryInfo`, `MemoryRecord` (including embedded `EvidenceRecord` list), `supersedes_edges`, and `contradicts_edges`.
     - Automatically hydrates on command invocation and flushes state on all mutating operations (`init`, `remember`, `update`, `check_changes`).
  2. In `CodeMemMCPServer`, automatically bind to `.codemem/store.json` using `_flush_store()` so CLI and MCP processes share identical persisted disk state.
  3. Validate through unit and integration tests (`test_persistence_across_restart` and Track 1 of `examples/demo.py`) that dropping in-memory stores and re-instantiating fresh services from disk restores 100% of memories, supersedes chains, and contradictions.
- **Tradeoffs & Consequences:** Enables reliable cross-restart persistence in all deployment modes (Docker FalkorDB or local offline JSON store) with zero test flakiness.

---

### ADR-025: Evaluation Benchmark Harness & LongMemEval Grounding (`codemem eval`)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Section 12 & 13 mandate an automated, reproducible evaluation suite covering the 5 core LongMemEval capabilities plus change awareness and scope isolation, reporting category-level metrics (precision, recall, F1, pass rate) and comparative metrics (memory-enabled vs memory-disabled).
- **Decision:**
  1. **Dataset & Scenarios (`src/codemem/evaluation/dataset.py`):** Define canonical test cases covering:
     - Extraction & AST Anchoring (`EXT-01`, `EXT-02`)
     - Cross-Session Persistence (`PER-01`)
     - Cross-Agent Provenance & Handoffs (`CAG-01`)
     - Temporal Reasoning & Revision History (`TMP-01`)
     - Non-Destructive Knowledge Updates (`KUP-01`)
     - Conservative Abstention on Ungrounded/Weak Queries (`ABS-01`, `ABS-02`)
     - Source Mutation Staleness Detection (`CHG-01`)
     - Multi-Repo Scope Leak Prevention (`SCP-01`)
  2. **Benchmark Runner (`src/codemem/evaluation/runner.py`):**
     - Executes isolated test runs in temporary repositories.
     - Computes category-level precision, recall, F1, and pass rates.
     - Computes comparative trials comparing file inspection counts, execution latencies, and knowledge preservation across sessions.
  3. **CLI Integration (`codemem eval`):**
     - Emits rich terminal tables summarizing category metrics and comparative speedup/inspection reduction.
     - Supports `--json` for machine-readable CI output and `--output <file>` for persisting benchmark reports.
- **Tradeoffs & Consequences:** Provides judges and contributors with a single-command reproducibility check (`codemem eval`) that evaluates true memory infrastructure quality with 100% offline determinism.

---

### ADR-026: Explicit Memory Revalidation & Evidence Freshening (`revalidate`)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Workflow E & Section 9 specify that when memories are flagged as `STALE` due to underlying source code changes, agents must be able to explicitly revalidate them (`codemem revalidate <id>` / MCP `revalidate_memory`). Previously, stale facts could only be superseded via full revision updates (`codemem update`), missing the lightweight operational path where the code change is verified and the existing fact remains valid.
- **Decision:**
  1. **Core Service Method (`MemoryService.revalidate`):**
     - Checks repository scope; raises `PermissionError` on scope violations.
     - Inspects all anchored `EvidenceRecord` items against current file paths.
     - Recomputes file SHA-256 hashes and verifies AST symbol integrity.
     - If passing test results are provided (`test_passed=True`), upgrades status to `VERIFIED` with high confidence (0.95).
     - If AST and file anchors are intact without tests, transitions to `OBSERVED` with 0.80 confidence.
     - If file anchors are missing or AST symbols are broken, remains `STALE`.
     - Atomically updates status in the graph store (`update_memory_status`) and persists refreshed evidence hashes.
  2. **Transports:**
     - CLI: Expose `codemem revalidate <id> [--test-cmd ...] [--test-passed] [--json]`.
     - MCP: Expose tool `@mcp.tool(name="revalidate_memory")`.
- **Tradeoffs & Consequences:** Closes PRD Workflow E and provides coding agents with an explicit, evidence-backed mechanism to verify and refresh cached memories without having to create redundant superseding versions.

---

### ADR-027: First-Class Agent Handoffs & Multi-Agent Task Continuity (`handoff`)
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** PRD Workflow H specifies that when coding agents finish a task or pass context to a subsequent agent, they need a structured handoff record. Without first-class handoffs, critical operational knowledge (completed work, open risks, known traps, and relevant memory IDs) is lost between agents in multi-agent workflows.
- **Decision:**
  1. **Data Model (`AgentHandoffRecord` & `HandoffInput`):**
     - Fields: `id`/`handoff_id`, `repository_id`, `originating_agent_id`, `receiving_agent_id`, `task_objective`, `completed_work`, `open_risks`, `relevant_memory_ids`, `status` (`PENDING`, `IN_PROGRESS`, `COMPLETED`), `created_at`, `updated_at`.
     - Supports dual-access via `id` and computed `handoff_id`.
  2. **Storage Adapters:**
     - `InMemoryGraphStore`: Tracks `_handoffs: Dict[str, AgentHandoffRecord]`, filters by `status` and `repo_id`, persists across runs to `.codemem/store.json`.
     - `FalkorDBAdapter`: Creates `:Handoff` nodes with parameterized Cypher, linking `(:Handoff)-[:FOR_REPO]->(:Repository)`, `(:Handoff)-[:CREATED_BY]->(:Agent)`, `(:Handoff)-[:ASSIGNED_TO]->(:Agent)`, and `(:Handoff)-[:REFERENCES_MEMORY]->(:Memory)`.
  3. **Core Service (`MemoryService`):**
     - `record_handoff`: Verifies repository scope and persists new handoff in `PENDING` status.
     - `get_handoff`: Retrieves handoff with strict repository scope isolation.
     - `list_handoffs`: Returns sorted list of handoffs optionally filtered by `status`.
     - `complete_handoff`: Transitions status to `COMPLETED`, records receiving agent ID and timestamp.
  4. **Transports:**
     - CLI: Exposes `codemem handoff [record | list | complete]`.
     - MCP: Exposes `@mcp.tool` for `record_handoff`, `list_handoffs`, and `complete_handoff`.
- **Tradeoffs & Consequences:** Fulfills PRD Workflow H and ensures seamless task transitions and risk handoffs across distinct agent personas (e.g. Architect -> Coder -> Reviewer) with full evidence traceability.

---

### ADR-028: Thread-Safe In-Memory Store & Multi-Agent Concurrency Guard
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** Multiple coding agents running concurrently in parallel worker threads or asynchronous workflows access the shared memory layer simultaneously. Unsynchronized dictionary operations in `InMemoryGraphStore` during concurrent read/write contention can cause race conditions, missing graph edges, or `RuntimeError: dictionary changed size during iteration`.
- **Decision:**
  1. **Reentrant Lock Protection:**
     - Initialize `self._lock = threading.RLock()` in `InMemoryGraphStore.__init__()`.
     - Wrap all graph mutations and index lookups (`save_memory`, `get_memory`, `query_memories_by_terms`, `traverse_concept_graph`, `supersede_memory`, `save_handoff`, `list_handoffs`, `update_handoff_status`, `clear`) with `with self._lock:`.
     - Create defensive copies (`list(self._memories.values())`) when iterating over dictionary elements to eliminate mutation race conditions.
  2. **Comprehensive Concurrency Verification Suite (`tests/unit/test_concurrency.py`):**
     - Verify concurrent multi-agent writes across thread pools without loss.
     - Verify concurrent read and write contention (`recall` vs `remember`) without deadlocks or iteration errors.
     - Verify concurrent handoff transitions and status completions.
     - Verify concurrent deduplication and status upgrading under identical concurrent submissions.
- **Tradeoffs & Consequences:** Negligible synchronization overhead (<0.01ms per operation) while providing rock-solid stability for multi-agent teams executing across parallel worker threads.

---

### ADR-029: Hybrid Semantic Vector Search & FalkorDB Vector Index Specification
- **Date:** 2026-10-17
- **Status:** Accepted
- **Context:** Lexical search and 2-hop concept graph traversal excel at exact keyword and explicit relation queries, but fail when a query uses zero overlapping surface words (e.g., "Where do we check credentials?" vs "TokenValidator verifies bearer tokens"). P1 mandates semantic vector search with FalkorDB vector index integration while maintaining zero external API key requirements.
- **Decision:**
  1. Implement `EmbeddingProvider` protocol and `DeterministicEmbeddingProvider` in `src/codemem/core/embeddings.py` producing normalized 64-dimensional vectors. Latent semantic cluster activations (auth, validation, storage, api, cache) map synonym concepts to shared dimensions without external network calls.
  2. Implement `FalkorDBVectorIndexSpec` generating FalkorDB Cypher DDL (`CREATE VECTOR INDEX FOR (m:Memory) ON (m.embedding)`) and parameterized vector queries (`CALL db.idx.vector.queryNodes('Memory', 'embedding', ...)`).
  3. Extend `GraphStoreProtocol`, `InMemoryGraphStore`, and `FalkorDBAdapter` with `vector_search(...)` returning scored `(MemoryRecord, similarity)` tuples.
  4. Augment `MemoryService.recall()` with Stage 1C (Semantic Vector Search) alongside Stage 1A (Lexical) and Stage 1B (2-hop Concept Graph), fusing lexical and vector similarities into a hybrid `match_score`.
- **Tradeoffs & Consequences:** Provides robust zero-overlap semantic recall, keeps test runs 100% deterministic and offline, and aligns directly with FalkorDB native vector indexing.

---

### ADR-030: Automated Codebase Scanning & Knowledge Graph Bootstrapping (`scan`)
- **Date:** 2026-10-18
- **Status:** Accepted
- **Context:** When CodeMem is initialized in a new or foreign repository, the knowledge graph starts empty until agents manually invoke `remember`. To provide immediate zero-touch value to developers and agents, CodeMem requires an automatic bootstrap scanner that discovers manifests, dependencies, test conventions, and top-level architectural symbols, seeding a starter knowledge graph.
- **Decision:**
  1. **Scanner Implementation (`src/codemem/repository/scanner.py`):**
     - Manifest Inspection: Parses `pyproject.toml` (using built-in `tomllib` or regex), `requirements.txt`, and `package.json`, emitting `MemoryType.DEPENDENCY` and project metadata facts.
     - Test Suite Detection: Discovers `tests/` directories, test runner conventions (`pytest`, `jest`), and registers `MemoryType.CONVENTION` memories.
     - AST Symbol Extraction: Parses Python source files using standard library `ast`, identifying prominent classes and functions with exact file line numbers and docstrings, emitting anchored `MemoryType.ARCHITECTURE` memories.
  2. **Domain & Transports:**
     - `MemoryService.scan_repository(path, max_files)`: Orchestrates scanning, repository registration, and atomic memory creation.
     - CLI: Exposes `codemem scan [--path .] [--backend memory|falkordb] [--max-files 150] [--json]`.
     - MCP: Exposes `@mcp.tool(name="scan_repository")` allowing AI agents to auto-bootstrap memory for arbitrary codebases.
- **Tradeoffs & Consequences:** Allows judges, developers, and agents to attach CodeMem to any repository and immediately run `recall` on existing code structure with zero manual data entry.















