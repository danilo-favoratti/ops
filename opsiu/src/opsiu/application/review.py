"""Human review reads original evidence and appends each decision in its own run."""

from opsiu.application.ports import (
    JsonValue,
    ReviewDetail,
    ReviewFilters,
    ReviewItem,
    ReviewRepository,
    RunFactory,
)
from opsiu.domain.decisions import Decision, DecisionValue, parse_decision
from opsiu.domain.errors import DomainError


class ReviewService:
    def __init__(
        self,
        repository: ReviewRepository,
        run_factory: RunFactory,
        *,
        reviewer: str,
    ) -> None:
        self.repository = repository
        self.run_factory = run_factory
        self.reviewer = reviewer

    def list_pending(
        self, filters: ReviewFilters | None = None
    ) -> tuple[ReviewItem, ...]:
        return self.repository.list_pending(filters or ReviewFilters())

    def get_finding(self, finding_key: str) -> ReviewDetail:
        if not isinstance(finding_key, str) or not finding_key.strip():
            raise ValueError("A chave do achado deve conter texto não vazio.")
        detail = self.repository.get_finding(finding_key)
        if detail is None:
            raise LookupError("Achado não encontrado.")
        return detail

    def decide(
        self,
        finding_key: str,
        decision: str | DecisionValue,
        comment: str = "",
    ) -> Decision:
        arguments: dict[str, JsonValue] = {
            "finding_key": finding_key if isinstance(finding_key, str) else None,
            "decision": decision if isinstance(decision, str) else None,
            "reviewer": self.reviewer if isinstance(self.reviewer, str) else None,
        }
        saved: Decision | None = None
        failure: Exception | None = None
        with self.run_factory("review.decide", arguments) as run:
            run.counters["decisions_saved"] = 0
            with run.step("registrar_decisao"):
                try:
                    detail = self.get_finding(finding_key)
                    finding = detail.record.finding
                    if finding.status != "active":
                        raise DomainError(
                            "Achados substituídos não podem receber novas decisões."
                        )
                    selected = Decision(
                        decision_id=run.ids.new_id(),
                        finding_key=finding_key,
                        decision=parse_decision(decision),
                        reviewer=self.reviewer,
                        comment=comment,
                        decided_at=run.clock.now(),
                        run_id=run.run_id,
                        step_id=run.require_step(),
                    )
                    run.metadata["rule_versions"] = {
                        finding.rule_id: finding.rule_version
                    }
                    source = finding.provenance.source
                    run.metadata["source_file_hashes"] = {
                        source.source_file_id: source.source_sha256
                    }
                    self.repository.append_decision(selected)
                    run.counters["decisions_saved"] = 1
                    run.emit(
                        "decision.recorded",
                        {
                            "decision_id": selected.decision_id,
                            "finding_key": selected.finding_key,
                            "decision": selected.decision.value,
                            "reviewer": selected.reviewer,
                        },
                    )
                    saved = selected
                except Exception as error:
                    failure = error
                    raise
        # Step captures normal errors; callers must still learn that no decision
        # was returned, after both the step and run have been finalized.
        if failure is not None:
            raise failure
        if saved is None:
            raise RuntimeError("A execução não registrou uma decisão.")
        return saved
