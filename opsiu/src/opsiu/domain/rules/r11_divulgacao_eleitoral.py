"""R11: publicity expenses in the pre-election window for matched candidates."""

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.policies import ELECTION_DATES
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.domain.text import normalized_name_sql

_ELECTIONS = ", ".join(
    f"({year}, DATE '{day.isoformat()}')" for year, day in ELECTION_DATES.items()
)


class R11DivulgacaoEleitoral:
    descriptor = RuleDescriptor(
        id="R11",
        version="2",
        title="Divulgação durante o período eleitoral vedado",
        severity="alta",
        fundamento=(
            "Nos 120 dias anteriores à data das eleições gerais e municipais, "
            "os deputados que forem candidatos não poderão utilizar recursos da "
            "cota para pagar divulgação da atividade parlamentar "
            "(Ato da Mesa 40/2012)."
        ),
        combine_document_rows=True,
        needs=("despesas_current", "tse_candidatos"),
    )
    sql = f"""
        WITH elections(year, election_date) AS (VALUES {_ELECTIONS}),
        expenses AS (
            SELECT d.*,
                {normalized_name_sql("d.nome_parlamentar")} AS normalized_name,
                CASE WHEN regexp_full_match(d.cpf, '[0-9]{{11}}')
                     AND d.cpf != repeat(substr(d.cpf, 1, 1), 11)
                     THEN d.cpf ELSE NULL END AS usable_cpf,
                e.year AS election_year, e.election_date
            FROM despesas_current d JOIN elections e
                ON d.data_emissao >= e.election_date - 120
               AND d.data_emissao < e.election_date
            WHERE d.numero_sub_cota = 5
        )
        SELECT d.id_documento, d.valor_liquido, d.data_emissao,
            d.numero_sub_cota, d.normalized_name, d.usable_cpf, d.sigla_uf,
            d.election_year, d.election_date,
            CAST(i.snapshot_id AS VARCHAR) AS snapshot_id, i.source_sha256,
            matched.identities, matched.match_method, matched.candidates
        FROM expenses d
        LEFT JOIN tse_imports_current i ON i.year = d.election_year
        LEFT JOIN LATERAL (
            SELECT count(DISTINCT coalesce(cpf, 'id:' || candidate_id)) AS identities,
                CASE min(priority) WHEN 0 THEN 'cpf' ELSE 'nome_uf' END AS match_method,
                list(struct_pack(
                    candidate_id := candidate_id,
                    cpf := cpf, nome_normalizado := nome_normalizado,
                    uf := uf, cargo := cargo, source_member := source_member,
                    source_record_number := source_record_number
                ) ORDER BY candidate_id) AS candidates
            FROM (
                SELECT c.*,
                    CASE WHEN d.usable_cpf = c.cpf THEN 0 ELSE 1 END AS priority
                FROM tse_candidatos_current c
                WHERE c.year = d.election_year
                  AND (d.usable_cpf = c.cpf OR (
                    (d.usable_cpf IS NULL OR c.cpf IS NULL)
                    AND d.normalized_name = c.nome_normalizado
                    AND d.sigla_uf = c.uf
                    AND NOT EXISTS (
                        SELECT 1 FROM tse_candidatos_current preferred
                        WHERE preferred.year = d.election_year
                          AND preferred.cpf = d.usable_cpf
                    )
                  ))
            ) candidates
        ) matched ON TRUE
        ORDER BY d.id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        if row["snapshot_id"] is None:
            raise DomainError(
                f"R11 requires a successful TSE import for {row['election_year']}"
            )
        if row["identities"] == 0:
            return None
        document, amount = row["id_documento"], row["valor_liquido"]
        election, issued = row["election_date"], row["data_emissao"]
        if (
            type(document) is not int
            or not isinstance(amount, Decimal)
            or not isinstance(election, date)
            or not isinstance(issued, date)
        ):
            raise DomainError("R11 requires an integer ID, Decimal amount, and dates")
        if not election - timedelta(days=120) <= issued < election:
            return None
        return RuleResult(
            document,
            amount,
            {
                "ano_eleicao": row["election_year"],
                "data_eleicao": election,
                "inicio_periodo_vedado": election - timedelta(days=120),
                "fim_periodo_exclusivo": election,
                "data_emissao": issued,
                "numero_sub_cota": row["numero_sub_cota"],
                "metodo_correspondencia": row["match_method"],
                "nome_parlamentar_normalizado": row["normalized_name"],
                "cpf_parlamentar": row["usable_cpf"],
                "uf_parlamentar": row["sigla_uf"],
                "snapshot_id": row["snapshot_id"],
                "snapshot_sha256": row["source_sha256"],
                "candidatos": row["candidates"],
                "correspondencia_ambigua": row["identities"] != 1,
            },
            severity="revisar" if row["identities"] != 1 else None,
        )
