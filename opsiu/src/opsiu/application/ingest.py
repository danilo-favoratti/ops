"""Ingestion orchestration over typed sources and persistence ports."""

from opsiu.application.ports import PolicyRepository
from opsiu.application.runs import Run
from opsiu.domain.policies import ADICIONAIS, CAPS, COTAS_UF


def load_policies(run: Run, repository: PolicyRepository) -> None:
    """Load the published dated policy rows inside the caller's active step."""
    step_id = run.require_step()
    repository.upsert_policies(
        CAPS, COTAS_UF, ADICIONAIS, run_id=run.run_id, step_id=step_id
    )
    counts = {
        "policy_caps": len(CAPS),
        "policy_quotas": len(COTAS_UF),
        "policy_additions": len(ADICIONAIS),
    }
    run.counters.update(counts)
    run.emit("policies.loaded", {key: value for key, value in counts.items()})
