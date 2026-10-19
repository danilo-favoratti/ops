"""Offline AgentRig example: typed steps, one retry, and lifecycle events.

Adapted from the library's typed-sequence fundamentals example:
https://github.com/mtmoreira/agentrig/tree/main/examples/fundamentals/typed_sequence
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentrig.core import (
    AgentRigError,
    CancellationSource,
    EffectProfile,
    EventId,
    EventKind,
    Failure,
    FailureKind,
    InMemoryEventSink,
    RunContext,
    RunId,
)
from agentrig.workflow import (
    FunctionStep,
    RetryPolicy,
    Sequence,
    Step,
    StepDescriptor,
    Workflow,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawRequest:
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedRequest:
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClassifiedRequest:
    text: str = field(repr=False)
    category: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestSummary:
    message: str = field(repr=False)
    category: str
    character_count: int


@dataclass(frozen=True, slots=True)
class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 100.0


@dataclass(slots=True)
class SequentialIds:
    prefix: str
    next_value: int = 1

    def generate(self) -> RunId | EventId:
        value = f"{self.prefix}-{self.next_value}"
        self.next_value += 1
        if self.prefix == "run":
            return RunId(value)
        return EventId(value)


async def normalize(request: RawRequest, context: RunContext) -> NormalizedRequest:
    del context
    text = " ".join(request.text.split())
    if not text:
        raise AgentRigError(
            Failure(
                kind=FailureKind.INVALID_INPUT,
                message="request text must not be empty",
                code="example.request_empty",
            )
        )
    return NormalizedRequest(text=text)


async def render(request: ClassifiedRequest, context: RunContext) -> RequestSummary:
    del context
    return RequestSummary(
        message=f"{request.category}: {request.text}",
        category=request.category,
        character_count=len(request.text),
    )


@dataclass(slots=True)
class ScriptedClassifier:
    """Stand-in for a model step. Fails once, then classifies the text."""

    failures_before_success: int = 1
    calls: int = 0
    descriptor: StepDescriptor = field(init=False)

    def __post_init__(self) -> None:
        self.descriptor = StepDescriptor(
            step_id="request.classify",
            version="1",
            effect_profile=EffectProfile.IDEMPOTENT,
        )

    async def run(
        self,
        request: NormalizedRequest,
        context: RunContext,
    ) -> ClassifiedRequest:
        del context
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise AgentRigError(
                Failure(
                    kind=FailureKind.TRANSIENT_PROVIDER,
                    message="classifier temporarily unavailable",
                    code="example.classifier_busy",
                )
            )
        category = "question" if request.text.endswith("?") else "statement"
        return ClassifiedRequest(text=request.text, category=category)


def build_workflow(
    classifier: Step[NormalizedRequest, ClassifiedRequest],
) -> Workflow[RawRequest, RequestSummary]:
    normalize_step: Step[RawRequest, NormalizedRequest] = FunctionStep(
        descriptor=StepDescriptor(
            step_id="request.normalize",
            version="1",
            effect_profile=EffectProfile.READ_ONLY,
        ),
        function=normalize,
    )
    render_step: Step[ClassifiedRequest, RequestSummary] = FunctionStep(
        descriptor=StepDescriptor(
            step_id="request.render-summary",
            version="1",
            effect_profile=EffectProfile.READ_ONLY,
        ),
        function=render,
    )
    return Sequence(
        normalize_step,
        classifier,
        render_step,
        retry_policy=RetryPolicy(max_attempts=2),
    )


async def main() -> None:
    classifier = ScriptedClassifier()
    workflow = build_workflow(classifier)
    events = InMemoryEventSink()
    context = RunContext.create_root(
        clock=FixedClock(),
        id_generator=SequentialIds(prefix="run"),
        cancellation=CancellationSource().token,
        event_sink=events,
        event_id_generator=SequentialIds(prefix="event"),
        correlation={"example": "typed-sequence"},
    )
    summary = (
        await workflow.execute(RawRequest(text="  How do retries work?  "), context)
    ).unwrap()
    print(
        json.dumps(
            {
                "message": summary.message,
                "category": summary.category,
                "character_count": summary.character_count,
                "classifier_attempts": classifier.calls,
                "events": [event.kind.value for event in events.events],
            },
            indent=2,
        )
    )
    if EventKind.RETRY_SCHEDULED not in {event.kind for event in events.events}:
        raise SystemExit("expected a retry after the transient classifier failure")


if __name__ == "__main__":
    asyncio.run(main())
