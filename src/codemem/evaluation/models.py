"""Data models for CodeMem evaluation benchmark and LongMemEval categories."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EvalCategory(str, Enum):
    """Core evaluation categories grounded in LongMemEval and RepoMem research."""

    EXTRACTION = "extraction"
    PERSISTENCE = "persistence"
    CROSS_AGENT = "cross_agent"
    TEMPORAL_REASONING = "temporal_reasoning"
    KNOWLEDGE_UPDATE = "knowledge_update"
    ABSTENTION = "abstention"
    CHANGE_AWARENESS = "change_awareness"
    SCOPE_ISOLATION = "scope_isolation"


class TestCase(BaseModel):
    """A single executable evaluation scenario."""

    id: str
    category: EvalCategory
    name: str
    description: str
    query: Optional[str] = None
    expected_abstained: Optional[bool] = None
    expected_top_claim_contains: Optional[str] = None
    expected_stale_count: Optional[int] = None
    expected_version_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CaseResult(BaseModel):
    """Result of running a single evaluation test case."""

    case_id: str
    category: EvalCategory
    passed: bool
    details: str
    latency_ms: float = 0.0


class CategoryResult(BaseModel):
    """Aggregated evaluation metrics for a single category."""

    category: EvalCategory
    total_tests: int
    passed_tests: int
    pass_rate: float
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None


class ComparativeMetric(BaseModel):
    """Comparison between agent performance with Memory Enabled vs Memory Disabled."""

    scenario: str
    task_description: str
    memory_disabled_inspections: int
    memory_enabled_inspections: int
    reduction_pct: float
    memory_disabled_latency_ms: float
    memory_enabled_latency_ms: float
    speedup_multiplier: float
    knowledge_preserved: bool


class BenchmarkReport(BaseModel):
    """Comprehensive evaluation report covering all required PRD Section 13 metrics."""

    timestamp: str
    total_scenarios: int
    total_passed: int
    overall_pass_rate: float
    category_results: List[CategoryResult] = Field(default_factory=list)
    comparative_results: List[ComparativeMetric] = Field(default_factory=list)
    summary: str
