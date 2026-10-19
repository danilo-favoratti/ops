"""Invalid evaluation inputs fail durably before any agent invocation."""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from evals.runner import run_evaluation
from opsiu.settings import AgentSettings, RuleSettings, Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name in {"AGENTRIG_RUN_LIVE", "EVAL_LIVE_GATE"}:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="unused"\ndb_path="unused.duckdb"\n'
        'runs_dir="runs"\nexternal_dir="unused-external"\n'
    )
    return tmp_path


@pytest.mark.parametrize(
    "invalid", ["margin", "unknown_finding", "malformed_labels", "custom_gate"]
)
def test_invalid_evaluation_setup_is_aborted_before_calls(
    workspace: Path, invalid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings()
    labels = None
    if invalid == "margin":
        settings = settings.model_copy(
            update={"rules": RuleSettings(quota_margin_percent=Decimal("0"))}
        )
    elif invalid == "custom_gate":
        monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
        settings = settings.model_copy(
            update={"agents": AgentSettings(live_env_var="EVAL_LIVE_GATE")}
        )
    else:
        labels = workspace / "invalid.jsonl"
        labels.write_text(
            json.dumps(
                {
                    "finding_key": "0" * 64,
                    "expected_recommendation": (
                        "manter" if invalid == "unknown_finding" else "aprovar"
                    ),
                    "notes": "Referência sintética inexistente.",
                }
            )
            + "\n"
        )
    result = run_evaluation(settings, labels)
    assert result.status == "aborted"
    assert result.attempted == 0 and result.succeeded == 0
    assert result.agreement is None
    manifest = json.loads((result.run_dir / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["counters"].get("agent_calls", 0) == 0
    assert (result.run_dir / "report.md").is_file()
    assert not settings.paths.db_path.exists()
    assert not settings.paths.data_dir.exists()
    assert not settings.paths.external_dir.exists()
