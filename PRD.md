# PRD: Local-First Shared Memory for Coding Agents

**Project type:** Hackathon project --- WeMakeDevs × FalkorDB, Track 2\
**Status:** Product requirements and implementation contract\
**Primary interface:** CLI + MCP server; no frontend\
**Primary database:** FalkorDB\
**Implementation target:** A judge can clone the repository, run it
locally, connect agents to it, and reproduce the demo.

------------------------------------------------------------------------

## 1. Executive summary

Build a **local-first, evidence-backed, change-aware shared memory layer
for coding agents**. It lets different agents and separate sessions
remember useful facts about a codebase, retrieve those facts when
needed, update them as the code evolves, and inspect the evidence and
history behind each claim.

The central innovation is not a new general-purpose coding agent,
chat-history summarizer, or graph visualization. It is a dependable
memory infrastructure layer: facts have repository scope, provenance,
timestamps, version history, verification state, and links to code
evidence. Agents access it through a shared API and MCP tools. FalkorDB
is the authoritative graph store for memory and relationships.

The project must demonstrate that: 1. Agent A records a useful codebase
fact. 2. The application/session ends and is restarted. 3. Agent B, in a
different session, retrieves the same fact from shared persistent
memory. 4. The answer includes evidence and freshness/verification
status. 5. When the relevant code changes, the old fact is flagged for
revalidation rather than silently trusted. 6. A corrected fact
supersedes the old version without erasing history. 7. When evidence is
insufficient, the system clearly abstains instead of inventing an
answer.

The system should be easy to run on a judge's machine and usable against
a repository the judge chooses.

## 2. Problem statement

Coding agents repeatedly inspect the same codebase, rediscover
architecture, repeat previous mistakes, and lose decisions between
sessions. Separate agents may also hold inconsistent local context. A
memory system that simply stores conversation summaries can become
stale, untraceable, or confidently wrong after source code changes.

We need shared memory that is: - **Persistent:** survives process and
machine/application restarts when the database volume is retained. -
**Shared:** accessible to every authorized agent that needs it, without
requiring direct agent-to-agent communication. - **Scoped:** tied to the
correct repository and workspace/tenant. - **Evidence-backed:** each
codebase claim points to files, symbols, commits, tool output, or tests
that support it. - **Time-aware:** distinguishes when a fact was true
from when the system learned or recorded it. - **Update-aware:** keeps
history and marks potentially affected facts stale when code changes. -
**Conservative:** surfaces uncertainty and abstains when evidence does
not support a reliable answer. - **Portable:** works locally, with a
repository selected by path; does not depend on a hosted UI or a single
developer's machine.

## 3. Goals

### P0 --- Required for a credible submission

1.  Use FalkorDB as the primary source of truth for the memory graph.
2.  Persist structured memories across independent sessions and
    application restarts.
3.  Allow multiple agents to read and write the same repository-scoped
    memory through a shared service.
4.  Provide a CLI and an MCP server with a stable, documented tool
    contract.
5.  Store evidence/provenance for memory claims.
6.  Preserve previous versions when facts are updated or contradicted.
7.  Track source commit and relevant file/symbol references; detect
    source changes and flag potentially stale memories.
8.  Retrieve relevant memories with scope, freshness, and evidence
    metadata.
9.  Abstain or clearly qualify answers when evidence is missing,
    conflicting, stale, or insufficient.
10. Include automated tests and a reproducible evaluation harness.
11. Run locally from documented commands, preferably with Docker Compose
    for FalkorDB.
12. Include two simple demo agents proving cross-session and cross-agent
    sharing.

### P1 --- Strong additions if time permits

-   MCP integration with more than one compatible agent/client.
-   Semantic retrieval in addition to exact lexical/path/symbol
    retrieval.
-   Concurrent read/write tests and idempotent writes.
-   Memory handoff records showing what one agent leaves for another.
-   A deterministic offline demo mode.
-   A report comparing answers with memory enabled vs. disabled.
-   A repository-change command that explicitly revalidates affected
    memories.

### Non-goals

-   Building a web dashboard or frontend.
-   Building a fully autonomous software engineer.
-   Automatically modifying a user's codebase based solely on memory.
-   Storing every source line or every conversation token as a graph
    node.
-   Building a generic long-term personal assistant memory product.
-   Using FalkorDB merely as a visualization layer.
-   Wrapping Graphiti as the main memory implementation. The memory
    schema, lifecycle, versioning, provenance, retrieval policy, and
    FalkorDB operations must be project-owned new work.
-   Claiming a fact is verified simply because an LLM produced it.
-   Requiring a paid hosted LLM or hosted database for the core
    deterministic demo.

## 4. Users and primary use cases

### Primary user

A developer or AI coding agent working on a repository over multiple
tasks and sessions.

### Use cases

1.  **Remember architecture:** Store that a particular module owns
    authentication, supported by exact source references.
2.  **Resume work:** A new session retrieves relevant architecture and
    prior decisions without re-reading the whole repository.
3.  **Share between agents:** One agent discovers a fact; another agent
    retrieves it through the same service.
4.  **Correct knowledge:** A newer discovery or code change contradicts
    an old fact; the system records a new version and preserves the old
    one.
5.  **Avoid stale advice:** A file or symbol changes; related memories
    become candidates for revalidation and are not silently treated as
    current.
6.  **Explain a claim:** A user or agent asks why the system believes
    something; it returns supporting evidence and provenance.
7.  **Admit uncertainty:** No sufficient evidence exists; the system
    says so rather than fabricating a repository fact.
8.  **Isolate projects:** A query for Repository A must not return
    Repository B's private facts.

## 5. Product principles

-   **Evidence before confidence:** claims should be traceable to
    inspectable evidence.
-   **History, not destructive overwrite:** preserve old versions and
    explain what superseded them.
-   **Scope before retrieval:** repository/workspace boundaries are
    applied before ranking or context assembly.
-   **Freshness is explicit:** do not confuse "stored" with "currently
    true."
-   **Shared service, selective context:** all authorized agents can
    access the service, but only relevant memories should be injected
    into a task.
-   **FalkorDB is central:** graph relations and graph queries must
    materially implement memory behavior.
-   **Simple, inspectable behavior beats opaque magic:** deterministic
    retrieval and demo paths should work without an LLM.
-   **Safe by default:** memory is advisory context, not authorization
    to execute commands or change files.

## 6. Proposed architecture

``` text
CLI / Agent A / Agent B / External MCP Client
                    |
             MCP Server + Core API
                    |
      Scope / Validation / Retrieval Policy
                    |
      Memory Service + Evidence/Change Logic
                    |
        FalkorDB (authoritative memory graph)
                    |
       Git + repository files + test/tool evidence
```

### Components

1.  **CLI (`codemem`)**
    -   Initialize or register a repository.
    -   Save and recall memories.
    -   Inspect evidence and version history.
    -   Check/revalidate memories after code changes.
    -   Run the evaluation suite.
    -   Print structured, readable output; no frontend.
2.  **Core memory service**
    -   Own schema validation, IDs, repository scoping, idempotency,
        status transitions, conflict handling, temporal metadata, and
        retrieval policy.
    -   Keep business logic independent of CLI/MCP transports.
3.  **FalkorDB adapter**
    -   Own all graph reads/writes and parameterized graph queries.
    -   Create/update graph entities and relations.
    -   Ensure writes are idempotent where possible and do not destroy
        history.
    -   Include initialization/schema/index setup if supported by the
        chosen FalkorDB version.
    -   Add integration tests against the actual database, not only
        mocks.
4.  **MCP server**
    -   Expose memory operations to compatible agents using the
        official/maintained Python MCP SDK (choose and pin a compatible
        implementation).
    -   Return structured data including status, scope, evidence, and
        memory IDs.
    -   Keep the same core service/API used by the CLI; do not duplicate
        business logic.
5.  **Repository and Git adapter**
    -   Identify a repository by canonical root path and a stable
        repository ID.
    -   Read current commit and file hashes.
    -   Validate file paths remain within the registered repository.
    -   Compare saved evidence hashes/commits with current code to
        identify potentially stale memories.
    -   Never execute arbitrary repository code merely to create memory.
6.  **Evaluation harness**
    -   Provide seeded scenarios, expected outcomes, test reports, and
        reproducible commands.
    -   Measure accuracy by category instead of reporting only one
        aggregate score.

### Recommended implementation choices

-   Python for service, CLI, MCP integration, and tests.
-   FalkorDB as the graph database.
-   Docker Compose for local FalkorDB startup and persistent database
    volume.
-   `pytest` for unit and integration tests.
-   Git CLI or a well-maintained Git library for commit and file
    metadata.
-   Optional LLM provider behind a small interface for extraction/answer
    synthesis.
-   Optional embeddings only if they materially improve retrieval;
    lexical/path/symbol retrieval must work without embeddings.
-   Pin versions and document tested versions. Do not assume a library
    API without verifying it against the installed version.

The exact packages may be adjusted by the implementing agent, but
architecture, contracts, and acceptance criteria in this PRD must remain
intact.

## 7. Memory model and graph schema

Use a versioned structured schema. Do not store an unstructured
paragraph as the only representation.

### Memory fact fields

Each memory version should support:

-   `memory_id`: stable unique identifier for this version.
-   `logical_fact_id`: identifier connecting versions of the same
    conceptual fact.
-   `repository_id`: repository scope.
-   `tenant_id` or `workspace_id`: isolation scope; a local single-user
    deployment may use a documented default workspace.
-   `subject`: entity/component the fact is about.
-   `predicate`: relation or attribute.
-   `object` or `claim`: concise normalized fact.
-   `memory_type`: e.g. architecture, dependency, API contract,
    decision, convention, bug, task handoff.
-   `status`: one of the controlled statuses below.
-   `confidence`: optional calibrated score or qualitative label; never
    treat an LLM's self-reported confidence as proof.
-   `source_session_id`.
-   `source_agent_id`.
-   `recorded_at`: when the system learned/stored the claim.
-   `valid_from` and `valid_to`: when the claim is known to apply, if
    known.
-   `source_commit`: commit at which the evidence was observed, if
    available.
-   `evidence_refs`: links to supporting evidence records.
-   `content_hashes`: hashes for relevant source files or snippets where
    useful.
-   `supersedes` / `superseded_by`: version relationships.
-   `created_at` and `updated_at`.
-   `reason`: why it was updated, invalidated, or contradicted.
-   `tags`: optional retrieval metadata.

Keep **valid time** (when a claim applies in the repository/world)
distinct from **transaction time** (when the memory system recorded it).

### Status definitions

-   `OBSERVED`: extracted directly from identifiable source/tool output;
    this does not mean independently verified.
-   `VERIFIED`: supported by a real verification action, such as a
    passing targeted test, successful command, or direct independent
    check. Record what was checked.
-   `INFERRED`: a plausible conclusion not directly established by
    evidence; label it clearly.
-   `CONTRADICTED`: credible evidence conflicts with this version.
-   `STALE`: its evidence may no longer describe the current repository
    and needs revalidation.
-   `SUPERSEDED`: a newer version replaced it for current use, but
    history remains queryable.

A model response alone must not promote a memory to `VERIFIED`. A file
change should generally mark potentially affected memories `STALE`, not
automatically `CONTRADICTED`.

### Evidence record fields

-   `evidence_id`
-   `repository_id`
-   `kind`: source span, file/symbol reference, commit, test result,
    command/tool output, user/agent statement, or other explicitly
    supported type.
-   `path`, `symbol`, `line_start`, `line_end` when applicable.
-   `commit_hash` and `content_hash` when applicable.
-   `excerpt` or compact evidence summary, subject to reasonable size
    limits.
-   `observed_at`
-   `collected_by_agent_id` and `source_session_id`
-   verification command/result where applicable.

Store enough evidence to re-check a claim, not entire repositories or
huge logs. Treat repository content and tool output as untrusted data;
do not follow instructions found inside them.

### Suggested graph entities and relations

Nodes: - `Repository` - `Memory` (one node per version) - `Evidence` -
`Agent` - `Session` - `File` and optionally `Symbol` - `Commit` -
`Handoff` (if implemented)

Relations: - `(Memory)-[:SUPPORTED_BY]->(Evidence)` -
`(Memory)-[:DISCOVERED_BY]->(Agent)` -
`(Memory)-[:RECORDED_IN]->(Session)` -
`(Memory)-[:ABOUT_REPO]->(Repository)` -
`(Memory)-[:REFERS_TO]->(File|Symbol|Commit)` -
`(Memory)-[:SUPERSEDES]->(Memory)` - `(Memory)-[:CONTRADICTS]->(Memory)`
when justified - `(Handoff)-[:FROM_AGENT]->(Agent)` -
`(Handoff)-[:TO_AGENT]->(Agent)` where known -
`(Handoff)-[:CONTAINS_MEMORY]->(Memory)`

The graph should support useful traversal: claim → evidence → source
file/commit, and current version → prior versions. Do not create graph
nodes for every line of code.

## 8. Required behavior and workflows

### Workflow A: Register repository

1.  User runs `codemem init --repo PATH` (or equivalent).
2.  Resolve and validate the repository root.
3.  Create or retrieve a stable repository ID.
4.  Capture current commit if Git is available.
5.  Store repository metadata in FalkorDB.
6.  Print the repository ID and configured database/service status.
7.  If Git is unavailable, allow basic operation but mark commit-based
    freshness features as limited.

### Workflow B: Remember a fact

1.  Agent/user supplies a claim plus repository and source context.
2.  Validate schema and scope.
3.  Capture evidence references where possible.
4.  Normalize the claim and compute an idempotency key where
    appropriate.
5.  Determine whether it is a duplicate, a new fact, or a possible
    conflict.
6.  Write the fact, evidence, provenance, and relations to FalkorDB.
7.  Preserve existing versions. Do not silently overwrite a conflicting
    fact.
8.  Return the memory ID, status, evidence summary, and any
    conflict/staleness warnings.

A memory may be stored as `INFERRED` or with missing evidence when
explicitly permitted, but the missing evidence must be visible and the
fact must not be presented as verified.

### Workflow C: Recall memory

1.  Require a repository/workspace scope for repository-specific recall.
2.  Apply scope and access filters before ranking results.
3.  Resolve exact identifiers, paths, and symbols first when present.
4.  Retrieve candidates using graph relationships and lexical search;
    semantic search is optional.
5.  Exclude superseded versions from the default current answer unless
    history is requested.
6.  Evaluate status, age, source commit, evidence hashes, and conflicts.
7.  Return relevant facts with evidence refs, current status, recorded
    time, source agent/session, and warnings.
8.  If evidence is insufficient, conflicting, or stale, return
    uncertainty/abstention rather than inventing a confident answer.
9.  Do not return unrelated memories simply because they share generic
    words.

### Workflow D: Update or correct a fact

1.  Create a new version linked to the old logical fact.
2.  Record the reason and supporting evidence for the change.
3.  Mark old version `SUPERSEDED` when the newer version is justified
    for current use.
4.  If the new evidence only creates uncertainty, mark the old fact
    `CONTRADICTED` or `STALE` as appropriate and preserve both for
    review.
5.  Never delete historical versions as the normal update path.
6.  Ensure current recall returns the best-supported current version and
    history shows the full chain.

### Workflow E: Code changes and freshness

1.  Compare saved evidence commit/hash with current repository state.
2.  Identify memories whose referenced files/symbols/evidence may have
    changed.
3.  Mark those memories `STALE` or as candidates for revalidation.
4.  Explain which path/hash/commit changed.
5.  Revalidate only with an actual check or fresh evidence.
6.  If a memory cannot be revalidated, keep its history but do not
    silently present it as current verified knowledge.

Do not assume every change invalidates every memory. Scope invalidation
to evidence dependencies where feasible.

### Workflow F: Cross-session and cross-agent sharing

1.  Agent A writes to the shared service, which commits the memory to
    FalkorDB.
2.  Agent A can stop; the memory remains in durable storage.
3.  A new process/session starts, identifies the same repository and
    workspace, and queries the service.
4.  Agent B retrieves the relevant fact and evidence without reading
    Agent A's local process memory.
5.  Updates made by Agent B become visible to Agent A on the next query.
6.  No direct agent-to-agent messaging is required for the shared state.
7.  All agents must use the same service contract and scope rules.
8.  Optional notifications/events may be added, but FalkorDB-backed
    state remains authoritative.
9.  Do not inject every stored memory into every agent prompt. Retrieve
    relevant, scoped memories for the current task.

### Workflow G: Explain provenance

Given a memory ID or claim, return: - current and historical versions, -
supporting evidence, - source file/symbol/commit where applicable, -
source agent/session, - recorded/valid timestamps, - verification action
and result if status is `VERIFIED`, - conflict/staleness/supersession
warnings.

### Workflow H: Handoff between agents

If implemented, a handoff should be a durable, structured record with: -
originating and receiving agent IDs when known, - repository/task
scope, - summary of completed work, - open questions and risks, -
relevant memory IDs, - evidence references, - created time.

Handoff records are a convenience on top of shared memory, not a
replacement for it.

## 9. API and MCP contract

Use one core service implementation for CLI, MCP, and tests. Tool names
and field names must be documented and stable.

### Required operations

#### `remember_memory`

Input: - `repository_id` - `claim` or structured `subject` / `predicate`
/ `object` - `memory_type` - `source_agent_id` - `source_session_id` -
`evidence_refs` or evidence payload - optional status/valid-time
metadata

Output: - `memory_id` - `logical_fact_id` - accepted status - evidence
summary - duplicate/conflict/stale warnings

#### `recall_memory`

Input: - `repository_id` - `query` - optional `agent_id`, `session_id`,
`top_k`, `include_stale`, `memory_types`

Output: - ranked memory records - evidence and provenance -
status/freshness warnings - `abstained` boolean and reason when
applicable

#### `update_memory`

Input: - target memory/logical fact ID - new claim or structured fact -
reason - new evidence - source agent/session

Output: - new version ID - supersession/conflict relations - status and
warnings

#### `memory_history`

Input: - memory ID or logical fact ID - repository scope

Output: - ordered versions, changes, reasons, timestamps, status
transitions, evidence.

#### `explain_memory`

Input: - memory ID or claim plus repository scope

Output: - provenance and evidence trail in a human-readable structure.

#### `check_repository_changes`

Input: - repository ID or path - optional current commit

Output: - changed files/commit metadata - potentially affected
memories - statuses changed and revalidation recommendations.

### Recommended optional operations

-   `record_handoff`
-   `search_code_context`
-   `revalidate_memory`
-   `resolve_conflict`

### Contract rules

-   Validate all input and output structures.
-   Return actionable, non-secret error messages.
-   Use structured errors with stable codes.
-   Set reasonable maximums for query length, result count, and evidence
    excerpt size.
-   Make writes idempotent when possible.
-   Do not expose another repository/workspace's data through a query or
    history lookup.
-   Never let model-generated text bypass scope or validation.
-   Ensure the MCP server is usable by a compatible external client, not
    only by the demo agent.

## 10. CLI requirements

Suggested command surface (exact spelling may be adjusted, but
equivalent capability is required):

``` bash
codemem init --repo .
codemem remember --repo . --claim "Authentication middleware validates JWTs"
codemem recall --repo . "Where is authentication handled?"
codemem explain --repo . <memory-id>
codemem history --repo . <memory-id-or-query>
codemem check-changes --repo .
codemem eval
codemem status
```

Requirements: - Include `--help` and useful validation errors. - Use
exit codes appropriate for success, validation errors, database
unavailable, and failed tests. - Output readable text by default and
machine-readable JSON where useful. - Never claim a memory was saved or
a check passed before the operation succeeds. - Commands must work from
a fresh clone after documented setup.

## 11. Demo agents

Build two intentionally small agents, not two elaborate coding systems.

### Agent A --- Repository investigator

-   Inspect a small sample repository or user-selected repository.
-   Find a real, defensible fact (e.g. route registration, auth
    middleware, service dependency).
-   Record the fact and evidence through the shared memory service.
-   Print the saved memory ID and source details.

### Agent B --- Fresh-session task agent

-   Start in a separate process/session.
-   Query the memory service for a task/question about the same
    repository.
-   Use the retrieved fact only if its scope/status/evidence is
    suitable.
-   Print the answer, supporting paths/symbols/commit, and uncertainty
    warnings.
-   Demonstrate that it did not rely on Agent A's in-memory variables or
    private local transcript.

### Change scenario

-   Modify or switch the relevant source file/commit.
-   Run change detection.
-   Show the fact becoming `STALE` or being revalidated based on actual
    evidence.
-   Store a corrected version and show history.
-   Ask a question with no sufficient evidence and show abstention.

Demo agents may use deterministic scripted logic in offline mode. Be
explicit that offline mode demonstrates the memory infrastructure and
does not simulate full LLM intelligence.

## 12. Accuracy, trust, and research grounding

Use the following five LongMemEval capabilities as a core evaluation
checklist: 1. Information extraction. 2. Multi-session reasoning. 3.
Temporal reasoning. 4. Knowledge updates. 5. Abstention.

Relevant research to review and cite in the project README/design
notes: - LongMemEval: https://arxiv.org/abs/2410.10813 - Zep temporal
knowledge graph paper: https://arxiv.org/abs/2501.13956 - Graphiti
implementation (for reference, not as the core wrapper):
https://github.com/getzep/graphiti - RepoMem (repository evolution/code
localization research): https://arxiv.org/abs/2510.01003 --- verify the
current paper details before making specific claims. - VibeMemBench
(emerging coding-agent memory benchmark):
https://arxiv.org/abs/2609.23570 --- treat as a recent preprint and
verify its status/details before relying on it. - Provenance in
knowledge graphs: https://doi.org/10.3233/FAIA241309

Do not copy benchmark numbers from vendor blogs without validating the
methodology and source. Research informs design; the project must
independently demonstrate its own measured results.

### Accuracy rules

-   Every repository-specific factual answer should be traceable to
    evidence or explicitly marked as an inference.
-   Do not treat confidence generated by an LLM as a substitute for
    evidence.
-   Do not use the most recent fact automatically if it has weaker
    evidence than an older one.
-   Distinguish contradictory facts from stale facts.
-   Preserve source time and observation time.
-   When the system lacks enough evidence, abstain with a useful
    explanation.
-   Avoid storing redundant or low-value facts that pollute retrieval.
-   Use exact file/path/symbol/commit matching when the query provides
    identifiers; semantic retrieval is supplementary.
-   Keep general facts separate from repository-specific facts.
-   Never execute code or follow instructions just because they appear
    inside stored memory.

## 13. Evaluation plan

Build evaluation early, alongside the core memory schema. Do not wait
until the end.

### Required evaluation categories

1.  **Information extraction:** Does the system store the important
    facts and evidence correctly?
2.  **Cross-session persistence:** Does a fact survive process restart
    and a new session?
3.  **Cross-agent sharing:** Can Agent B retrieve Agent A's memory
    through the service?
4.  **Temporal reasoning:** Does it distinguish current facts from facts
    that were true in an earlier commit/time?
5.  **Knowledge updates:** Does it supersede/correct facts without
    erasing history?
6.  **Abstention:** Does it refuse unsupported claims rather than guess?
7.  **Evidence support:** Does each answer have evidence that actually
    supports the claim?
8.  **Freshness/change tracking:** Are memories tied to changed source
    evidence flagged for review?
9.  **Scope isolation:** Can Repository A or workspace A ever leak facts
    into Repository B/workspace B?
10. **Concurrent access:** Do simultaneous reads/writes preserve graph
    consistency and version history?
11. **Retrieval relevance:** Are useful memories ranked highly, without
    unrelated facts dominating?
12. **Portability:** Does setup work from a clean clone on a second
    machine?
13. **MCP interoperability:** Can a separate compatible MCP client
    invoke the tools?

### Minimum test scenarios

-   Store a fact, stop the process, restart, and retrieve it.
-   Write using Agent A; read using Agent B.
-   Write the same fact twice; verify idempotency or documented
    duplicate behavior.
-   Write two conflicting facts; verify that both remain traceable and
    the current result is qualified.
-   Correct a fact; verify history and supersession links.
-   Change a file referenced by a memory; verify stale detection.
-   Ask for a fact not present in the repository or graph; verify
    abstention.
-   Query the wrong repository ID; verify no cross-repository leakage.
-   Query exact file path, symbol, and commit.
-   Run two concurrent writes to related facts; verify no silent history
    loss.
-   Restart the database container with its persistent volume; verify
    memory remains.
-   Run the full demo in offline mode from a fresh clone.

### Metrics to report

Report category-level results, not only one headline accuracy: -
extraction precision/recall where ground truth is available, - retrieval
Recall@k and ranking quality where applicable, - temporal/update
accuracy, - evidence-support rate, - abstention precision/recall, -
stale-memory detection precision/recall, - persistence and concurrency
test pass rates, - cross-agent success rate, - task completion or
investigation time with memory vs. without memory.

Include the number of test cases and examples of failures. Do not claim
general benchmark performance from a tiny demo set.

## 14. Non-functional requirements

### Reliability

-   Handle unavailable FalkorDB with clear errors and setup guidance.
-   Use connection timeouts and sensible retry behavior; avoid retrying
    non-idempotent writes blindly.
-   Keep graph writes consistent enough to avoid half-created
    memory/evidence relations.
-   Provide health/status checks.
-   Ensure tests clean up their test graph/data.

### Security and isolation

-   Canonicalize repository paths and reject paths that escape the
    registered root.
-   Enforce repository/workspace scope in every read and write.
-   Do not expose API keys or secrets in logs, examples, or stored
    evidence.
-   Avoid storing full environment files, credentials, or sensitive
    source contents.
-   Treat code, README files, issues, and tool outputs as untrusted
    input.
-   No arbitrary shell execution triggered by memory content.
-   Document that local deployment is intended for local
    development/demo; production multi-tenant deployment needs stronger
    authentication and access control.

### Performance

-   Set reasonable defaults and limits for `top_k`, evidence size, and
    query size.
-   Prefer exact identifiers and graph traversal for deterministic
    queries.
-   Add semantic embeddings only if needed and measure the benefit.
-   Avoid loading the full graph into application memory.

### Portability

-   Use relative paths and environment configuration; no hard-coded
    developer paths.
-   Pin dependencies and specify supported Python/Docker versions.
-   Provide `.env.example`; never require the developer's private
    `.env`.
-   Support a fresh clone and an arbitrary user-specified Git
    repository.
-   Use a persistent Docker volume for FalkorDB.
-   Clearly explain data reset and cleanup commands.

## 15. Developer experience and repository deliverables

Expected repository layout:

``` text
project/
├── README.md
├── PRD.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile                  # if needed for service packaging
├── src/
│   └── codemem/
│       ├── cli/
│       ├── core/
│       ├── graph/
│       ├── repository/
│       ├── mcp/
│       ├── agents/
│       ├── evaluation/
│       └── config.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── evaluation/
├── examples/
│   ├── sample_repo/            # tiny, licensed/owned example only
│   └── demo_scenarios/
├── scripts/
└── docs/
    ├── architecture.md
    ├── memory-schema.md
    └── evaluation.md
```

Adjust structure if a simpler layout is more maintainable, but keep core
service logic, FalkorDB access, MCP transport, CLI, agents, and tests
separated.

### Required documentation

-   One-command/short-command local setup.
-   Supported versions and prerequisites.
-   Environment variable reference.
-   CLI examples.
-   MCP configuration example for a compatible client.
-   Memory schema and status semantics.
-   How persistence and cross-agent sharing work.
-   How to run the restart/change/abstention demo.
-   How to run tests and evaluation.
-   Known limitations and roadmap.
-   Troubleshooting FalkorDB startup/connection issues.
-   Exact distinction between deterministic offline mode and optional
    LLM mode.

### Configuration

-   `.env.example` must list all supported settings with safe
    placeholders.
-   Provide a documented default local FalkorDB connection.
-   LLM API keys must be optional for offline tests/demo.
-   Never commit real secrets.
-   Keep model/provider integration behind an interface so it can be
    replaced without changing memory logic.

## 16. Teammate ownership and task split

The project has two primary contributors. Use this ownership to enable
parallel development while keeping one integrated product.

### Owner A --- Project lead / user: memory engine and accuracy

**Owns:** - memory schema and graph data model, - FalkorDB adapter and
graph queries, - memory create/read/update/history lifecycle, -
evidence/provenance links, - temporal metadata and version history, -
conflict and supersession rules, - repository commit/file-hash freshness
checks, - conservative recall and abstention policy, - unit/integration
tests for core memory behavior, - accuracy evaluation design and result
interpretation.

**Deliverables:** - core memory service API, - FalkorDB schema/query
implementation, - schema documentation, - memory lifecycle tests, -
change-aware memory tests, - evaluation fixtures and accuracy report.

**Must not independently invent:** - an incompatible MCP/API contract, -
a second source of truth outside FalkorDB, - a schema that omits
repository/session/agent/evidence/time metadata.

### Owner B --- Teammate: agent integration and shared access

**Owns:** - MCP server and transport layer, - CLI wiring to the core
service, - two small demo agents and their session lifecycle, -
agent-facing `remember`, `recall`, `update`, `history`, `explain` tool
exposure, - automatic relevant-memory retrieval at task start, - MCP
client configuration/example, - end-to-end cross-session/cross-agent
demo scripts, - integration tests for MCP and agent sharing.

**Deliverables:** - working MCP server, - documented CLI commands or CLI
transport wiring as agreed, - two reproducible demo agents, -
cross-session restart demonstration, - MCP configuration example, -
integration tests and demo instructions.

**Must not independently invent:** - a separate memory store, -
duplicate memory business logic inside MCP handlers, - direct local-only
agent state as a substitute for shared FalkorDB state, - claims of
cross-agent sharing that are not demonstrated through the common
service.

### Shared ownership

Both contributors: - agree on schema/API before parallel
implementation, - review each other's interface changes, - integrate
through pull requests, - test from a clean clone, - verify Docker
Compose and persistence, - write README/demo/evaluation results, -
rehearse the judge walkthrough.

### Interface-first collaboration checklist

Before coding, create and commit: 1. `docs/memory-schema.md`: fields,
statuses, graph relations, version rules. 2. `docs/api-contract.md`:
method names, inputs, outputs, error codes. 3. Typed Python models or
JSON schemas used by both modules. 4. A mocked core-service interface so
the MCP side can be developed before the real graph layer is ready. 5.
One shared acceptance-test list.

Use feature branches and small pull requests. Avoid both people editing
the same core files at once. Agree on ownership of `pyproject.toml`,
Docker Compose, and shared models to prevent merge conflicts.

## 17. Suggested milestones

### Milestone 1 --- Contracts and skeleton

-   Confirm requirements and repo layout.
-   Define models, statuses, API signatures, errors.
-   Create CLI/service package skeleton.
-   Add Docker Compose and a FalkorDB health check.
-   Add a basic smoke test.

**Exit criterion:** both contributors can run the skeleton and agree on
the same contract.

### Milestone 2 --- Durable graph memory

-   Implement FalkorDB repository/graph operations.
-   Save and recall a structured memory with evidence.
-   Implement IDs and repository scoping.
-   Add persistence-across-restart test.

**Exit criterion:** a fact is stored and recovered after restarting the
application.

### Milestone 3 --- Shared API and MCP

-   Implement MCP tools over the core service.
-   Connect demo agents to the same service.
-   Implement cross-session and cross-agent reads/writes.

**Exit criterion:** Agent A writes; a new Agent B process reads the
fact.

### Milestone 4 --- Trust and change awareness

-   Add history, supersession, conflict handling, evidence explanation.
-   Add Git/file-hash change detection.
-   Add stale status and abstention behavior.

**Exit criterion:** changing evidence triggers revalidation, and
unsupported questions do not produce fabricated answers.

### Milestone 5 --- Evaluation and portability

-   Run unit, integration, isolation, concurrency, and evaluation tests.
-   Test on a fresh clone and an arbitrary sample repository.
-   Verify persistent Docker volume behavior.
-   Document offline and optional LLM modes.

**Exit criterion:** a judge can reproduce the full demo from the README.

### Milestone 6 --- Submission polish

-   Rehearse a concise, deterministic demo.
-   Prepare architecture diagram and measured evaluation summary.
-   Document limitations honestly.
-   Ensure no secret keys, local paths, or unlicensed sample code are
    committed.

## 18. Acceptance criteria / definition of done

The project is not complete until all P0 criteria are demonstrated:

-   [ ] FalkorDB stores the authoritative memory graph.
-   [ ] Memory facts include repository scope, source agent/session,
    timestamps, status, and evidence metadata.
-   [ ] A stored fact survives application restart.
-   [ ] Agent A can write a fact and Agent B can retrieve it from a
    separate process/session.
-   [ ] A new version can supersede an old one without deleting history.
-   [ ] The system exposes evidence and provenance for retrieved facts.
-   [ ] A source change marks relevant memories stale or requires
    revalidation.
-   [ ] A question without sufficient evidence results in explicit
    abstention/uncertainty.
-   [ ] Repository/workspace isolation tests pass.
-   [ ] MCP tools invoke the same core service as the CLI.
-   [ ] Automated tests cover persistence, sharing, updates, stale
    detection, evidence, and abstention.
-   [ ] Docker Compose starts FalkorDB with a persistent volume and a
    health check.
-   [ ] Setup works from a clean clone without hidden local
    dependencies.
-   [ ] Offline demo mode works without an LLM API key.
-   [ ] README gives exact commands and expected output for the
    end-to-end demo.
-   [ ] No feature is claimed to work unless it has a reproducible test
    or demo.

## 19. Judge demonstration script

Aim for a short, deterministic walkthrough:

1.  Start FalkorDB and check health.
2.  Register a sample or user-provided Git repository.
3.  Run Agent A to discover and store one evidence-backed architectural
    fact.
4.  Stop Agent A and start a fresh Agent B session.
5.  Ask a related question; show retrieval plus evidence and provenance.
6.  Restart the application or relevant process and repeat recall.
7.  Change the referenced file or switch to a new commit.
8.  Run change detection; show the affected memory becoming stale.
9.  Revalidate or add a corrected fact; show the version chain in
    history.
10. Ask a question the graph cannot answer; show explicit abstention.
11. Run the automated tests/evaluation command and show category-level
    results.

Do not depend on network access, an external model, or live APIs for the
core walkthrough. If an LLM is enabled, treat it as an optional
enhancement and retain a deterministic fallback.

## 20. Risks and mitigations

  -----------------------------------------------------------------------
  Risk                                Mitigation
  ----------------------------------- -----------------------------------
  It becomes a generic chat-summary   Store structured claims with code
  database                            evidence, graph relations, and
                                      repository scope.

  FalkorDB appears decorative         Make graph traversal, evidence
                                      links, version chains, and
                                      repository-scoped graph queries
                                      core operations.

  Stale facts mislead agents          Track commit/hash dependencies and
                                      mark affected memories for
                                      revalidation.

  False "verified" status             Require a recorded real
                                      verification action and result.

  Agents cannot integrate             Freeze shared schema and API
                                      contracts before parallel work.

  Demo works only on the developer's  Test clean clone, pinned
  machine                             dependencies, Docker volume, and
                                      configurable repo path.

  LLM/API key blocks judging          Make deterministic offline path
                                      fully testable.

  Too much scope for a hackathon      Prioritize P0; defer advanced
                                      embeddings, UI, autonomous code
                                      edits, and complex orchestration.

  Duplicate/contradictory facts       Use logical fact IDs, idempotency,
  pollute recall                      explicit conflict relations, and
                                      history.

  Cross-repository leakage            Enforce repository/workspace
                                      filters in the core service and
                                      test negative cases.

  Unsafe memory content triggers      Treat memories as untrusted
  actions                             context; never execute commands
                                      based solely on memory.
  -----------------------------------------------------------------------

## 21. Decisions the implementing agent must preserve

1.  No frontend; CLI + MCP are the primary interfaces.
2.  FalkorDB is the primary graph source of truth.
3.  Implement a project-owned memory layer; do not merely wrap Graphiti.
4.  Shared access is mediated through a common service, not private
    agent transcripts.
5.  Cross-session durability and cross-agent availability are core
    acceptance criteria, not stretch goals.
6.  Accuracy is a first-class feature: evidence, temporal metadata,
    updates, staleness, conflicts, and abstention.
7.  Keep the project small enough to run locally and demonstrate
    reliably.
8.  Demo agents should be simple; memory infrastructure is the primary
    innovation.
9.  Build evaluation and acceptance tests early.
10. Do not overclaim metrics or verification.
11. Make offline operation possible, with LLM support optional.
12. Keep repository data scoped and avoid storing secrets or excessive
    source text.

## 22. Final implementation instruction for the coding agent

Read this PRD completely before coding. First inspect the repository and
report its current state. Then propose a short implementation plan
mapped to the milestones and acceptance criteria. Identify any
package/API assumptions that need verification. Implement in small,
testable stages rather than generating the entire project in one
unverified pass.

Start with the shared schema/API contract and a working FalkorDB smoke
test. Do not build a frontend. Do not substitute an in-memory dictionary
or local JSON file for the authoritative graph store. Do not add
unnecessary infrastructure. After each milestone, run relevant tests and
report exactly what passed, failed, or remains incomplete. Never mark an
acceptance criterion complete without a reproducible test or demo.
