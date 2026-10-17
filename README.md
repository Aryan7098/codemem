# CodeMem: Local-First Shared Memory for Coding Agents

**Team: Brave and the Bold**  
*WeMakeDevs × FalkorDB Hackathon (Track 2)*

A local-first, evidence-backed, change-aware shared memory layer for coding agents powered by FalkorDB.

## Features
- **FalkorDB Authoritative Graph:** Graph-backed persistence of claims, entities, evidence, and version histories.
- **Evidence-Backed Trust:** Links facts to exact AST symbols, file hashes, and test results (`VERIFIED`, `OBSERVED`, `INFERRED`).
- **Temporal & Change-Aware:** Tracks code freshness, flags `STALE` memories when referenced files change, and supersedes facts without erasing history.
- **Conservative Abstention:** Abstains when evidence is insufficient or contradictory rather than hallucinating answers.
- **CLI & MCP Server:** Unified core service interface accessible directly by developer tools and LLM coding agents.
