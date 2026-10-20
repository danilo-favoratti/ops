"""R13: registered-activity leads requiring transaction-level verification."""

from collections.abc import Mapping
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.identifiers import is_valid_numeric_cnpj, valid_numeric_cnpj_sql
from opsiu.domain.policies import DRIVER_CNAES, GROCERY_CNAES
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.domain.rules.cnpj_common import require_cnpj_coverage


class R13FornecedorIncompativel:
    descriptor = RuleDescriptor(
        id="R13",
        version="3",
        severity="revisar",
        title="Atividade do fornecedor exige conferência da nota",
        fundamento=(
            "aquisição de gêneros alimentícios; locação de veículo automotor, "
            "prestada por pessoa jurídica especializada, que contemple o serviço "
            "de motorista;"
        ),
        needs=("despesas_current", "cnpj_empresas"),
        combine_document_rows=True,
    )
    sql = f"""
        WITH eligible_expenses AS (
            SELECT * FROM despesas_current
            WHERE numero_sub_cota IN (13, 120)
              AND {valid_numeric_cnpj_sql("cnpj_cpf_digits")}
        )
        SELECT d.id_documento, d.valor_liquido, d.numero_sub_cota, d.fornecedor,
            d.cnpj_cpf_digits AS cnpj, substr(d.cnpj_cpf_digits, 1, 8) AS cnpj_root,
            CAST(i.snapshot_id AS VARCHAR) AS snapshot_id, i.source_sha256,
            r.cnpj_root AS covered_root, c.razao_social, c.natureza_juridica,
            e.cnae_principal,
            CASE WHEN c.cnpj_root IS NULL THEN NULL ELSE struct_pack(
                source_file := c.source_file, source_sha256 := c.source_sha256,
                source_member := c.source_member,
                source_record_number := c.source_record_number
            ) END AS company_origin,
            CASE WHEN e.cnpj IS NULL THEN NULL ELSE struct_pack(
                source_file := e.source_file, source_sha256 := e.source_sha256,
                source_member := e.source_member,
                source_record_number := e.source_record_number
            ) END AS establishment_origin
        FROM eligible_expenses d
        LEFT JOIN cnpj_imports_current i ON TRUE
        LEFT JOIN cnpj_import_roots_current r
            ON r.cnpj_root = substr(d.cnpj_cpf_digits, 1, 8)
        LEFT JOIN cnpj_empresas_base_current c
            ON c.cnpj_root = substr(d.cnpj_cpf_digits, 1, 8)
        LEFT JOIN cnpj_estabelecimentos_current e ON e.cnpj = d.cnpj_cpf_digits
        ORDER BY d.id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        if not is_valid_numeric_cnpj(row["cnpj"]):
            return None
        require_cnpj_coverage(row)
        name = row["razao_social"]
        if name is not None and not isinstance(name, str):
            raise DomainError("R13 company legal name must be text")
        category, cnae = row["numero_sub_cota"], row["cnae_principal"]
        reasons: list[str] = []
        if category == 13 and cnae in GROCERY_CNAES:
            reasons.append("cnae_comercio_alimenticio")
        elif category == 120 and cnae in DRIVER_CNAES:
            reasons.append("cnae_locacao_com_motorista")
        if not reasons:
            return None
        document, amount = row["id_documento"], row["valor_liquido"]
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError("R13 requires an integer document ID and Decimal amount")
        return RuleResult(
            document,
            amount,
            {
                "cnpj": row["cnpj"],
                "raiz_cnpj": row["cnpj_root"],
                "numero_sub_cota": category,
                "fornecedor_na_despesa": row["fornecedor"],
                "razao_social": name,
                "natureza_juridica": row["natureza_juridica"],
                "cnae_principal": cnae,
                "motivos": reasons,
                "origem_empresa": row["company_origin"],
                "origem_estabelecimento": row["establishment_origin"],
                "snapshot_id": row["snapshot_id"],
                "snapshot_sha256": row["source_sha256"],
                "revisao_humana_obrigatoria": True,
                "indicio_apenas_cadastral": True,
                "conteudo_da_nota_comprovado": False,
                "cadastro_na_data_despesa": False,
                "limitacao": (
                    "A atividade cadastrada do fornecedor é uma pista para conferir "
                    "a nota ou o contrato; não comprova os produtos adquiridos, "
                    "a contratação de motorista nem uma despesa vedada. "
                    "A razão social, por si só, não gera achado."
                ),
            },
        )
