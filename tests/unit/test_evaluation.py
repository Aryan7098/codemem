"""Unit tests for CodeMem evaluation harness and benchmark runner."""

from __future__ import annotations

import json
from pathlib import Path
from click.testing import CliRunner

from codemem.cli.main import cli
from codemem.evaluation.dataset import get_standard_evaluation_cases
from codemem.evaluation.models import BenchmarkReport, EvalCategory
from codemem.evaluation.runner import BenchmarkRunner


def test_standard_evaluation_cases_dataset():
    """Verify evaluation dataset contains all required LongMemEval categories."""
    cases = get_standard_evaluation_cases()
    assert len(cases) >= 10
    categories = {c.category for c in cases}
    assert EvalCategory.EXTRACTION in categories
    assert EvalCategory.PERSISTENCE in categories
    assert EvalCategory.CROSS_AGENT in categories
    assert EvalCategory.TEMPORAL_REASONING in categories
    assert EvalCategory.KNOWLEDGE_UPDATE in categories
    assert EvalCategory.ABSTENTION in categories
    assert EvalCategory.CHANGE_AWARENESS in categories
    assert EvalCategory.SCOPE_ISOLATION in categories


def test_benchmark_runner_execution():
    """Verify BenchmarkRunner executes all test cases with 100% pass rate."""
    runner = BenchmarkRunner()
    report = runner.run_benchmark()

    assert isinstance(report, BenchmarkReport)
    assert report.total_scenarios == 10
    assert report.total_passed == 10
    assert report.overall_pass_rate == 100.0
    assert len(report.category_results) == 8
    assert len(report.comparative_results) == 3

    # Verify abstention precision and recall
    abs_cat = next(c for c in report.category_results if c.category == EvalCategory.ABSTENTION)
    assert abs_cat.passed_tests == 2
    assert abs_cat.precision == 1.0
    assert abs_cat.recall == 1.0


def test_cli_eval_command(tmp_path):
    """Verify 'codemem eval' CLI command with text and JSON outputs."""
    runner = CliRunner()

    # 1. Human-readable text output
    res_text = runner.invoke(cli, ["eval"])
    assert res_text.exit_code == 0
    assert "CODEMEM EVALUATION BENCHMARK" in res_text.output
    assert "10/10 (100.0%)" in res_text.output
    assert "COMPARATIVE PERFORMANCE" in res_text.output

    # 2. Machine-readable JSON output
    out_file = tmp_path / "eval_report.json"
    res_json = runner.invoke(cli, ["eval", "--json", "--output", str(out_file)])
    assert res_json.exit_code == 0
    assert out_file.is_file()

    data = json.loads(res_json.output)
    assert data["total_scenarios"] == 10
    assert data["total_passed"] == 10
    assert data["overall_pass_rate"] == 100.0
    assert len(data["category_results"]) == 8
    assert len(data["comparative_results"]) == 3
