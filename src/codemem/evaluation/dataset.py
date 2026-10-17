"""Standardized benchmark scenarios for LongMemEval and RepoMem evaluation."""

from __future__ import annotations

from typing import List
from codemem.evaluation.models import EvalCategory, TestCase


def get_standard_evaluation_cases() -> List[TestCase]:
    """Return the canonical test suite of evaluation scenarios."""
    return [
        # 1. Extraction & Grounding
        TestCase(
            id="EXT-01",
            category=EvalCategory.EXTRACTION,
            name="AST & Hash Anchoring",
            description="Verify claim with valid Python symbol and file anchor is marked OBSERVED with content hash.",
            query="TokenValidator verifies tokens",
            expected_top_claim_contains="TokenValidator",
        ),
        TestCase(
            id="EXT-02",
            category=EvalCategory.EXTRACTION,
            name="Test Verification Promotion",
            description="Verify claim backed by passing test command is promoted to VERIFIED status.",
            query="TokenValidator passes test suite",
            expected_top_claim_contains="test suite",
        ),
        # 2. Cross-Session Persistence
        TestCase(
            id="PER-01",
            category=EvalCategory.PERSISTENCE,
            name="Restart Recovery",
            description="Verify facts survive full process and store termination and remain queryable.",
            query="Persistent database pooling setting",
            expected_top_claim_contains="pooling setting",
        ),
        # 3. Cross-Agent Sharing
        TestCase(
            id="CAG-01",
            category=EvalCategory.CROSS_AGENT,
            name="Multi-Agent Handoff",
            description="Verify Agent-B in independent session reads task left by Agent-A with full provenance.",
            query="HANDOFF: Refactor token validation",
            expected_top_claim_contains="Refactor token validation",
        ),
        # 4. Temporal Reasoning
        TestCase(
            id="TMP-01",
            category=EvalCategory.TEMPORAL_REASONING,
            name="Chronological Revision Chain",
            description="Verify history endpoint orders versions chronologically from root to current.",
            expected_version_count=2,
        ),
        # 5. Knowledge Updates
        TestCase(
            id="KUP-01",
            category=EvalCategory.KNOWLEDGE_UPDATE,
            name="Non-Destructive Supersession",
            description="Verify update creates new version and marks prior version SUPERSEDED without erasing history.",
            query="Cryptographic algorithm standard",
            expected_top_claim_contains="Ed25519",
        ),
        # 6. Conservative Abstention
        TestCase(
            id="ABS-01",
            category=EvalCategory.ABSTENTION,
            name="Ungrounded Query Rejection",
            description="Verify system abstains on queries with zero supporting evidence in graph.",
            query="Kubernetes ingress controller auto-scaler configuration",
            expected_abstained=True,
        ),
        TestCase(
            id="ABS-02",
            category=EvalCategory.ABSTENTION,
            name="Weak Match Abstention",
            description="Verify abstention threshold gate triggers when candidate scores are below confidence minimum.",
            query="Completely unrelated random terminology xyz123",
            expected_abstained=True,
        ),
        # 7. Change Awareness & Freshness
        TestCase(
            id="CHG-01",
            category=EvalCategory.CHANGE_AWARENESS,
            name="Source Mutation Staleness Detection",
            description="Verify check_changes flags memory as STALE when underlying source file hash changes.",
            expected_stale_count=1,
        ),
        # 8. Scope Isolation
        TestCase(
            id="SCP-01",
            category=EvalCategory.SCOPE_ISOLATION,
            name="Multi-Repo Leak Prevention",
            description="Verify queries and mutations across repository boundaries are strictly isolated.",
            query="Repo-A private architecture secret",
            expected_abstained=True,
        ),
    ]
