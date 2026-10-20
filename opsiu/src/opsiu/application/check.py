"""Evaluate deterministic rules and publish only complete, successful results."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal

from opsiu.application.ports import CheckRepository
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import (
    Finding,
    Provenance,
    evidence_json,
    source_record_identity,
)
from opsiu.domain.rules.base import CoverageAwareRule, Rule, RuleResult


def check(
    run: Run,
    repository: CheckRepository,
    rules: tuple[Rule, ...],
    *,
    dry_run: bool = False,
) -> None:
    run.counters["rules_selected"] = len(rules)
    for name in (
        "rules_completed",
        "rules_skipped",
        "rules_suspended",
        "rules_failed",
        "candidates_evaluated",
        "candidates_without_coverage",
        "findings_detected",
        "findings_inserted",
        "findings_unchanged",
        "findings_superseded",
    ):
        run.counters.setdefault(name, 0)
    versions = run.metadata["rule_versions"]
    assert isinstance(versions, dict)
    for rule in rules:
        descriptor = rule.descriptor
        versions[descriptor.id] = descriptor.version
        with run.step(f"verificar_{descriptor.id}") as step:
            if descriptor.suspended_reason is not None:
                superseded = 0
                if not dry_run:
                    changes = repository.publish_findings(
                        descriptor.id,
                        descriptor.version,
                        (),
                        run_id=run.run_id,
                        step_id=run.require_step(),
                        finished_at=run.clock.now(),
                    )
                    superseded = changes.superseded
                    run.counters["findings_superseded"] += superseded
                run.emit(
                    "rule.suspended",
                    {
                        "rule_id": descriptor.id,
                        "rule_version": descriptor.version,
                        "reason": descriptor.suspended_reason,
                        "findings_superseded": superseded,
                        "dry_run": dry_run,
                    },
                )
                step.skip(descriptor.suspended_reason)
                run.counters["rules_suspended"] += 1
                run.counters["rules_skipped"] += 1
                continue
            missing = [
                name for name in descriptor.needs if not repository.has_relation(name)
            ]
            if missing:
                step.skip("Fontes necessárias ausentes: " + ", ".join(missing))
                run.counters["rules_skipped"] += 1
                continue
            hashes = run.metadata["source_file_hashes"]
            assert isinstance(hashes, dict)
            for current_source in repository.current_sources():
                hashes[current_source.source_file] = current_source.source_sha256
            findings: dict[str, Finding] = {}
            candidates = repository.candidates(rule.sql)
            evaluated = 0
            uncovered: Counter[str] = Counter()
            coverage_rule = rule if isinstance(rule, CoverageAwareRule) else None
            grouped: dict[tuple[int, int | None, str | None], list[RuleResult]] = {}
            try:
                for row in candidates:
                    evaluated += 1
                    if coverage_rule is not None:
                        reason = coverage_rule.coverage_reason(row)
                        if reason is not None:
                            uncovered[reason] += 1
                            continue
                    result = rule.evaluate(row)
                    if result is None:
                        continue
                    year: int | None = None
                    record_hash: str | None = None
                    if (
                        descriptor.identify_missing_documents
                        and result.id_documento <= 0
                    ):
                        raw_year, raw_hash = (
                            row.get("source_year"),
                            row.get("record_sha256"),
                        )
                        if type(raw_year) is not int or not isinstance(raw_hash, str):
                            raise DomainError(
                                "unidentified document requires source year "
                                "and record hash"
                            )
                        source_record_identity(raw_year, raw_hash)
                        year, record_hash = raw_year, raw_hash
                    grouped.setdefault(
                        (result.id_documento, year, record_hash), []
                    ).append(result)
                for (document, year, record_hash), results in grouped.items():
                    results.sort(
                        key=lambda item: evidence_json(
                            {"valor": item.amount, "evidencia": dict(item.evidence)}
                        )
                    )
                    result = results[0]
                    evidence = deepcopy(dict(result.evidence))
                    amount = result.amount
                    severity = result.severity or descriptor.severity
                    record_identity = (
                        source_record_identity(year, record_hash)
                        if year is not None and record_hash is not None
                        else ""
                    )
                    if descriptor.combine_document_rows or record_identity:
                        origins = repository.source_origins(
                            document, source_year=year, record_sha256=record_hash
                        )
                        if not origins:
                            raise DomainError("finding has no current source records")
                        source = origins[0]
                        if len(results) > 1 or len(origins) > 1:
                            evidence = {
                                "ocorrencias": [
                                    {
                                        "valor": item.amount,
                                        "evidencia": dict(item.evidence),
                                    }
                                    for item in results
                                ],
                                "registros_origem": [
                                    {
                                        "arquivo": origin.source_file,
                                        "arquivo_sha256": origin.source_sha256,
                                        "registro_sha256": origin.record_sha256,
                                    }
                                    for origin in origins
                                ],
                            }
                            if record_identity:
                                evidence["registros_origem"] = [
                                    {
                                        "ano_fonte": year,
                                        "registro_sha256": record_hash,
                                    }
                                ]
                            amount = sum(
                                (item.amount for item in results), Decimal("0.00")
                            )
                        if any(item.severity == "revisar" for item in results):
                            severity = "revisar"
                    else:
                        if len(results) != 1:
                            raise DomainError(
                                "a rule emitted duplicate finding identities"
                            )
                        source = repository.source_origin(document)
                    if record_identity:
                        evidence["identidade_registro"] = {
                            "ano_fonte": year,
                            "registro_sha256": record_hash,
                            "documento_sem_identificador": True,
                        }
                    finding = Finding(
                        descriptor.id,
                        descriptor.version,
                        result.id_documento,
                        severity,
                        amount,
                        evidence,
                        Provenance(
                            run.run_id,
                            run.require_step(),
                            source,
                            run.record.metadata.code_commit,
                            run.clock.now(),
                        ),
                        record_identity=record_identity,
                    )
                    evidence_json(finding.evidence)  # Validate even during dry runs.
                    if finding.finding_key in findings:
                        raise DomainError("a rule emitted duplicate finding identities")
                    findings[finding.finding_key] = finding
            finally:
                close = getattr(candidates, "close", None)
                if callable(close):
                    close()
            if uncovered:
                run.counters["candidates_evaluated"] += evaluated
                run.counters["candidates_without_coverage"] += sum(uncovered.values())
                run.emit(
                    "rule.coverage_missing",
                    {
                        "rule_id": descriptor.id,
                        "rule_version": descriptor.version,
                        "candidates": evaluated,
                        "unassessed": sum(uncovered.values()),
                        "reasons": dict(uncovered),
                        "dry_run": dry_run,
                    },
                )
                step.skip(
                    "Cobertura incompleta; achados anteriores preservados. "
                    + "; ".join(uncovered)
                )
                run.counters["rules_skipped"] += 1
                continue
            if not dry_run:
                changes = repository.publish_findings(
                    descriptor.id,
                    descriptor.version,
                    tuple(findings.values()),
                    run_id=run.run_id,
                    step_id=run.require_step(),
                    finished_at=run.clock.now(),
                )
                run.counters["findings_inserted"] += changes.inserted
                run.counters["findings_unchanged"] += changes.unchanged
                run.counters["findings_superseded"] += changes.superseded
            run.counters["candidates_evaluated"] += evaluated
            run.counters["findings_detected"] += len(findings)
            run.counters["rules_completed"] += 1
            run.emit(
                "rule.checked",
                {
                    "rule_id": descriptor.id,
                    "rule_version": descriptor.version,
                    "candidates": evaluated,
                    "findings": len(findings),
                    "dry_run": dry_run,
                },
            )
        if step.record.status == "failed":
            run.counters["rules_failed"] += 1
