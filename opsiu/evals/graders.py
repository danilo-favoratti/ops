"""Deterministic advisory grading with AgentRig's native grader contract."""

from dataclasses import dataclass

from agentrig.core.grading import (
    Grade,
    GradeClassification,
    GradeEvidence,
    GraderDescriptor,
    GradeStatus,
    GradingContext,
    ScoreRange,
)

_RECOMMENDATIONS = frozenset({"manter", "descartar", "revisar"})
_DESCRIPTOR = GraderDescriptor(
    grader_id="opsiu.recommendation_agreement", version="1", agentic=False
)
_RANGE = ScoreRange(minimum=0.0, maximum=1.0)


@dataclass(frozen=True)
class RecommendationComparison:
    """Keep the synthetic reference label separate from the actual emitted value."""

    expected: str
    actual: object


def grade_recommendation(expected: str, actual: object) -> Grade:
    """Compare exact labels; missing/invalid output never earns agreement.

    An invalid reference label is a dataset error, not a model disagreement.
    The caller must track valid-output coverage separately: a zero score can
    represent either a valid disagreement or an ungradable output. This grade
    evaluates recommendations only, never rule correctness or human approval.
    """
    if not isinstance(expected, str) or expected not in _RECOMMENDATIONS:
        raise ValueError("expected recommendation must be manter, descartar or revisar")
    valid_actual = isinstance(actual, str) and actual in _RECOMMENDATIONS
    agreement = valid_actual and actual == expected
    if not valid_actual:
        explanation = (
            "Saída ausente ou recomendação inválida; não houve concordância avaliável."
        )
    elif agreement:
        explanation = "A recomendação emitida coincide com o rótulo esperado."
    else:
        explanation = (
            f"A recomendação emitida ({actual}) difere do rótulo esperado ({expected})."
        )
    return Grade(
        grader=_DESCRIPTOR,
        metric="recommendation_agreement",
        status=GradeStatus.PASS if agreement else GradeStatus.FAILURE,
        classification=GradeClassification.SOFT,
        explanation=explanation,
        evidence=(
            GradeEvidence(field_path=("expected",)),
            GradeEvidence(field_path=("actual",)),
        ),
        score=1.0 if agreement else 0.0,
        score_range=_RANGE,
    )


class RecommendationAgreementGrader:
    """Implement AgentRig's asynchronous Grader[RecommendationComparison]."""

    @property
    def descriptor(self) -> GraderDescriptor:
        return _DESCRIPTOR

    async def grade(
        self, subject: RecommendationComparison, context: GradingContext
    ) -> Grade:
        if not isinstance(context, GradingContext):
            raise TypeError("recommendation grader context must be GradingContext")
        if not isinstance(subject, RecommendationComparison):
            raise TypeError(
                "recommendation grader subject must be RecommendationComparison"
            )
        context.run_context.cancellation.raise_if_cancelled()
        if context.run_context.deadline is not None:
            context.run_context.deadline.raise_if_expired(context.run_context.clock)
        return grade_recommendation(subject.expected, subject.actual)
