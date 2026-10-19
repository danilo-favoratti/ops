"""Agent annotations preserve private audit data and share the existing ledger."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from agentrig.agents import AgentExecutionResult, AgentRuntimeUsage
from agentrig.core import Failure, FailureKind, JsonValue
from agentrig.testing import ScriptedAgentProgress
from typer.testing import CliRunner

from opsiu.adapters.agents.contracts import (
    ParecerRequest,
    ParecerRequestCodec,
    configure_parecer_agent,
)
from opsiu.adapters.agents.prompts import load_prompt
from opsiu.adapters.agents.runtimes import (
    SCRIPTED_CAPABILITIES,
    ScriptedAgentScenario,
    build_scripted_runtime,
)
from opsiu.adapters.agents.trace import TracedAgentInvoker
from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import FindingExportRecord
from opsiu.application.runs import Run
from opsiu.domain.findings import evidence_json
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.contract
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
INPUT_MARKER = "CONTEUDO_PRIVADO_DA_NOTA_SINTETICA"
OUTPUT_MARKER = "FUNDAMENTO_PRIVADO_DO_PARECER_SINTETICO"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    expense = json.loads(FIXTURE.read_text())["dados"][39]
    (tmp_path / "input/Ano-2099.json").write_text(json.dumps({"dados": [expense]}))
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    for arguments in (["ingest"], ["check", "--regras", "R08"]):
        result = CliRunner().invoke(app, arguments)
        assert result.exit_code == 0, result.exception or result.output
    return tmp_path


def request_for(record: FindingExportRecord) -> ParecerRequest:
    finding = record.finding
    return ParecerRequestCodec().decode(
        {
            "achado": {
                "finding_key": finding.finding_key,
                "regra": finding.rule_id,
                "versao_regra": finding.rule_version,
                "severidade": finding.severity,
                "valor_envolvido": str(finding.amount),
                "evidencia_json": evidence_json(finding.evidence),
                "source_sha256": finding.provenance.source.source_sha256,
                "record_sha256": finding.provenance.source.record_sha256,
            },
            "registro": {
                "id_documento": finding.id_documento,
                "numero_deputado_id": record.numero_deputado_id,
                "nome_parlamentar": record.nome_parlamentar,
                "sigla_uf": record.sigla_uf,
                "sigla_partido": record.sigla_partido,
                "ano": record.ano,
                "mes": record.mes,
                "fornecedor": record.fornecedor,
                "cnpj_cpf": record.cnpj_cpf_digits,
                "numero": record.numero,
                "data_emissao": (
                    record.data_emissao.isoformat() if record.data_emissao else None
                ),
                "valor_documento": str(record.valor_documento),
                "valor_glosa": str(record.valor_glosa),
                "valor_liquido": str(record.valor_liquido),
                "url_documento": record.url_documento,
            },
            "texto_regra": "R08: conferir pagamento a pessoa física.",
            "texto_nota": INPUT_MARKER,
        }
    )


def opinion(recommendation: str = "revisar") -> dict[str, JsonValue]:
    return {
        "fundamento": OUTPUT_MARKER,
        "confianca": "baixa",
        "evidencias_citadas": ["achado.evidencia_json"],
        "recomendacao": recommendation,
    }


def test_scripted_calls_keep_private_content_and_final_validation_status(
    workspace: Path,
) -> None:
    runtime = build_scripted_runtime(
        [
            ScriptedAgentScenario(
                result=AgentExecutionResult.succeeded(
                    opinion(),
                    usage=AgentRuntimeUsage(input_tokens=30, output_tokens=10),
                ),
                actions=(ScriptedAgentProgress(message=INPUT_MARKER),),
            ),
            ScriptedAgentScenario(
                result=AgentExecutionResult.succeeded(opinion("aprovado"))
            ),
            ScriptedAgentScenario(
                result=AgentExecutionResult.from_failure(
                    Failure(
                        kind=FailureKind.UNEXPECTED,
                        code="synthetic.failure",
                        message="Falha sintética de provedor.",
                    )
                )
            ),
        ]
    )
    prompt = load_prompt("explicar-achado")
    agent = configure_parecer_agent(
        runtime, SCRIPTED_CAPABILITIES, rule_id="R08", prompt=prompt
    )
    files = JsonlLedger(workspace / "runs")
    with connect_database(workspace / "db.duckdb") as connection:
        records = list(DuckDBFindingExportReader(connection).active_findings())
        assert len(records) == 1
        request = request_for(records[0])
        database = DuckDBLedger(connection)
        run = Run(
            ledger=CompositeLedger(files, database),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="trace-contract",
            arguments={},
            metadata=collect_metadata("test", workspace),
        )
        invoker = TracedAgentInvoker(connection, run, model="scripted")
        results = []
        with run:
            for name in ("valid", "invalid-output", "provider-failure"):
                with run.step(name):
                    results.append(
                        asyncio.run(
                            invoker.invoke(
                                agent,
                                request,
                                prompt=prompt,
                                finding_key=records[0].finding.finding_key,
                            )
                        )
                    )
        assert len(results) == 3
        assert [item.result.is_success for item in results] == [True, False, False]
        assert len({item.trace_id for item in results}) == 3
        assert all(str(call.context.run_id) == run.run_id for call in runtime.calls)
        events = database.get_events(run.run_id)
        assert events == files.get_events(run.run_id)
        assert any("provider" in event.kind for event in events)
        assert all(
            event.step_id is not None for event in events if "provider" in event.kind
        )
        ordinary_logs = "".join(
            (files.run_dir(run.run_id) / name).read_text()
            for name in ("events.jsonl", "log.jsonl", "manifest.json")
        )
        assert INPUT_MARKER not in ordinary_logs
        assert OUTPUT_MARKER not in ordinary_logs
        assert connection.execute("SELECT count(*) FROM pareceres").fetchone() == (0,)
        cursor = connection.execute("SELECT * FROM agent_traces")
        columns = [column[0] for column in cursor.description]
        traces = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        assert len(traces) == 3
        assert all(str(row["run_id"]) == run.run_id for row in traces)
        assert all(
            row["finding_key"] == records[0].finding.finding_key for row in traces
        )
        by_id = {str(row["agent_trace_id"]): row for row in traces}
        for index, item in enumerate(results):
            row = by_id[item.trace_id]
            assert row["status"] == item.result.status.value
            assert row["model"] == "scripted"
            assert row["agent_id"] == "explicar-achado-R08"
            assert row["prompt_version"] == prompt.version
            assert row["prompt_sha256"] == prompt.sha256
            assert row["instructions"] == prompt.text
            assert json.loads(row["input_json"])["texto_nota"] == INPUT_MARKER
            assert (
                str(row["step_id"])
                == runtime.calls[index].context.correlation["step_id"]
            )
            assert row["duration_ms"] >= 0
        success = by_id[results[0].trace_id]
        invalid = by_id[results[1].trace_id]
        provider_failure = by_id[results[2].trace_id]
        assert json.loads(success["output_json"]) == opinion()
        assert json.loads(success["usage_json"])["input_tokens"] == 30
        assert json.loads(success["usage_json"])["output_tokens"] == 10
        assert success["failure_json"] is None
        assert json.loads(invalid["output_json"]) == opinion("aprovado")
        assert invalid["failure_json"] is not None
        assert json.loads(provider_failure["output_json"]) is None
        assert json.loads(provider_failure["failure_json"])["code"] == (
            "synthetic.failure"
        )
        private = connection.execute(
            "SELECT kind, content, content_bytes, truncated FROM agent_trace_records"
        ).fetchall()
        assert {row[0] for row in private} == {"provider.request", "provider.response"}
        assert len(private) >= 6
        assert all(not row[3] for row in private)
        assert all(row[2] == len(row[1].encode("utf-8")) for row in private)
        content = "".join(row[1] for row in private)
        assert INPUT_MARKER in content
        assert OUTPUT_MARKER in content
        assert connection.execute("SELECT status FROM findings").fetchall() == [
            ("active",)
        ]
