"""Opt-in synthetic recommendation evaluation; live calls require the literal gate."""

import json

import pytest

from evals.runner import run_evaluation
from opsiu.settings import Settings

pytestmark = pytest.mark.eval


def test_recommendation_evaluation() -> None:
    settings = Settings()
    result = run_evaluation(settings)
    assert result.status == "completed", result.run_dir
    assert result.dataset_size == 50
    assert result.attempted == min(result.selected, settings.agents.max_calls_per_run)
    assert result.succeeded == result.attempted and result.failed == 0
    assert result.coverage == result.succeeded / result.dataset_size
    assert result.agreement == (
        result.matches / result.attempted if result.attempted else None
    )
    data = json.loads((result.run_dir / "eval_results.json").read_text())
    assert len(data["cases"]) == result.attempted
    if result.mode == "scripted":
        assert all(case["actual_recommendation"] == "revisar" for case in data["cases"])
        assert result.matches == sum(
            case["expected_recommendation"] == "revisar" for case in data["cases"]
        )
    agreement = (
        f"{result.agreement:.2%}" if result.agreement is not None else "não calculada"
    )
    print(
        f"Concordância: {result.matches}/{result.attempted} ({agreement}); "
        f"cobertura: {result.succeeded}/{result.dataset_size} ({result.coverage:.2%})."
    )
    print(f"Avaliação: {result.run_dir / 'eval.md'}")
