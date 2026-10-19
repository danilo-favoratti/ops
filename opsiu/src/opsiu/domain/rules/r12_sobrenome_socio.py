"""R12: conservative shared-surname evidence, always requiring human review."""

from collections.abc import Mapping
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.domain.rules.cnpj_common import require_cnpj_coverage
from opsiu.domain.text import normalize_name, surname_tokens


class R12SobrenomeSocio:
    descriptor = RuleDescriptor(
        id="R12",
        version="3",
        severity="revisar",
        title="Sobrenome compartilhado com sócio pessoa física",
        fundamento=(
            "bens ou serviços adquiridos de empresa ou entidade da qual o deputado "
            "ou parente até o terceiro grau ou servidor da Câmara seja proprietário "
            "ou detentor de qualquer participação;"
        ),
        needs=("despesas_current", "cnpj_socios"),
        combine_document_rows=True,
        identify_missing_documents=True,
    )
    sql = """
        SELECT d.id_documento, d.valor_liquido, d.nome_parlamentar,
            d.record_sha256, f.source_year,
            d.cnpj_cpf_digits AS cnpj, substr(d.cnpj_cpf_digits, 1, 8) AS cnpj_root,
            CAST(i.snapshot_id AS VARCHAR) AS snapshot_id, i.source_sha256,
            r.cnpj_root AS covered_root, partners.socios
        FROM despesas_current d
        JOIN source_files f USING (source_file_id)
        LEFT JOIN cnpj_imports_current i ON TRUE
        LEFT JOIN cnpj_import_roots_current r
            ON r.cnpj_root = substr(d.cnpj_cpf_digits, 1, 8)
        LEFT JOIN LATERAL (
            SELECT list(struct_pack(
                nome := nome_socio, qualificacao := qualificacao,
                source_file := source_file, source_sha256 := source_sha256,
                source_member := source_member,
                source_record_number := source_record_number
            ) ORDER BY source_file, source_member, source_record_number) AS socios
            FROM cnpj_socios_current s
            WHERE s.cnpj_root = substr(d.cnpj_cpf_digits, 1, 8) AND s.tipo_socio = 2
        ) partners ON TRUE
        WHERE regexp_full_match(d.cnpj_cpf_digits, '[0-9]{14}')
          AND NOT (d.cpf IS NULL AND d.sigla_uf = 'NA')
        ORDER BY d.id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        require_cnpj_coverage(row)
        name = row["nome_parlamentar"]
        if not isinstance(name, str):
            raise DomainError("R12 requires the source deputy name")
        deputy_tokens = surname_tokens(name)
        partners = row["socios"]
        if not deputy_tokens or partners is None:
            return None
        if not isinstance(partners, list):
            raise DomainError("R12 partner candidates must be a list")
        matches: list[dict[str, object]] = []
        all_tokens: set[str] = set()
        for partner in partners:
            if not isinstance(partner, dict) or not isinstance(
                partner.get("nome"), str
            ):
                raise DomainError("R12 partner name is invalid")
            matched = sorted(deputy_tokens & surname_tokens(partner["nome"]))
            if matched:
                matches.append({**partner, "tokens_compartilhados": matched})
                all_tokens.update(matched)
        if not matches:
            return None
        document, amount = row["id_documento"], row["valor_liquido"]
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError("R12 requires an integer document ID and Decimal amount")
        return RuleResult(
            document,
            amount,
            {
                "cnpj": row["cnpj"],
                "raiz_cnpj": row["cnpj_root"],
                "nome_parlamentar_normalizado": normalize_name(name),
                "tokens_compartilhados": sorted(all_tokens),
                "socios": matches,
                "snapshot_id": row["snapshot_id"],
                "snapshot_sha256": row["source_sha256"],
                "revisao_humana_obrigatoria": True,
                "parentesco_comprovado": False,
                "cadastro_na_data_despesa": False,
            },
        )
