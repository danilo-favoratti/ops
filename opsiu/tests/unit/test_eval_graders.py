"""Native AgentRig grades score actual outputs independently of reference labels."""

import asyncio
import json
from typing import cast

import pytest
from agentrig.core.cancellation import CancellationSource, RunCancelled
from agentrig.core.clock import SystemClock
from agentrig.core.context import RunContext
from agentrig.core.deadline import Deadline, DeadlineExceeded
from agentrig.core.grading import (
    Grade,
    GradeClassification,
    Grader,
    GradeStatus,
    GradingContext,
    ScoreRange,
)
from agentrig.core.identity import RunId, Uuid4IdGenerator

from evals.graders import (
    RecommendationAgreementGrader,
    RecommendationComparison,
    grade_recommendation,
)

pytestmark = pytest.mark.unit
LABELS = ("manter", "descartar", "revisar")


def context(
    cancellation: CancellationSource | None = None, *, expired: bool = False
) -> GradingContext:
    clock = SystemClock()
    source = cancellation if cancellation is not None else CancellationSource()
    return GradingContext(
        run_context=RunContext.create_root(
            clock=clock,
            id_generator=Uuid4IdGenerator(RunId),
            cancellation=source.token,
            deadline=Deadline.after(0, clock) if expired else None,
        )
    )


@pytest.mark.parametrize("expected", LABELS)
@pytest.mark.parametrize("actual", LABELS)
def test_agreement_uses_actual_output_for_every_label_pair(
    expected: str, actual: str
) -> None:
    grade = grade_recommendation(expected, actual)
    agrees = expected == actual
    assert isinstance(grade, Grade)
    assert grade.score == (1.0 if agrees else 0.0)
    assert grade.status is (GradeStatus.PASS if agrees else GradeStatus.FAILURE)
    assert grade.classification is GradeClassification.SOFT
    assert grade.metric == "recommendation_agreement"
    assert grade.score_range == ScoreRange(minimum=0, maximum=1)
    assert grade.grader.version == "1" and not grade.grader.agentic
    assert grade.usage is None


@pytest.mark.parametrize(
    "actual",
    [
        None,
        "",
        "manter ",
        " revisar",
        "REVISAR",
        "aprovar",
        True,
        1,
        [],
        {},
        {"recomendacao": "revisar"},
    ],
)
def test_malformed_actual_does_not_become_agreement(actual: object) -> None:
    grade = grade_recommendation("revisar", actual)
    assert grade.status is GradeStatus.FAILURE
    assert grade.score == 0
    assert "Saída ausente ou recomendação inválida" in grade.explanation


@pytest.mark.parametrize("expected", [None, "", "manter ", "REVISAR", "aprovar", 1, []])
def test_invalid_reference_is_rejected_instead_of_scored(expected: object) -> None:
    with pytest.raises(ValueError, match="expected recommendation"):
        grade_recommendation(cast(str, expected), "revisar")


def test_native_grade_serialization_retains_evidence_and_calibration() -> None:
    grade = grade_recommendation("descartar", "revisar")
    data = json.loads(grade.to_json())
    assert data["schema_version"] == 1
    assert data["grader"] == {
        "grader_id": "opsiu.recommendation_agreement",
        "version": "1",
        "agentic": False,
    }
    assert data["score_range"] == {"minimum": 0.0, "maximum": 1.0}
    assert data["evidence"] == [
        {"artifact_id": None, "field_path": ["expected"]},
        {"artifact_id": None, "field_path": ["actual"]},
    ]
    assert "revisar" in data["explanation"] and "descartar" in data["explanation"]


def test_scripted_revisar_baseline_is_not_perfect_for_mixed_labels() -> None:
    grades = [grade_recommendation(expected, "revisar") for expected in LABELS]
    assert sum(grade.score or 0.0 for grade in grades) / len(grades) == pytest.approx(
        1 / 3
    )
    assert sum(grade.status is GradeStatus.PASS for grade in grades) == 1


def test_async_implementation_satisfies_native_grader_protocol() -> None:
    grader: Grader[RecommendationComparison] = RecommendationAgreementGrader()
    assert isinstance(grader, Grader)
    result = asyncio.run(
        grader.grade(RecommendationComparison("manter", "revisar"), context())
    )
    assert result == grade_recommendation("manter", "revisar")
    assert result.grader == grader.descriptor


def test_native_grader_propagates_cancellation() -> None:
    source = CancellationSource()
    source.cancel("cancelled by test")
    with pytest.raises(RunCancelled, match="cancelled by test"):
        asyncio.run(
            RecommendationAgreementGrader().grade(
                RecommendationComparison("revisar", "revisar"), context(source)
            )
        )


def test_native_grader_propagates_deadline() -> None:
    with pytest.raises(DeadlineExceeded):
        asyncio.run(
            RecommendationAgreementGrader().grade(
                RecommendationComparison("revisar", "revisar"), context(expired=True)
            )
        )
