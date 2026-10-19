"""Rule documentation metadata must agree with the explicit code registry."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from opsiu.adapters.files.rule_docs import RuleDocumentError, load_rule_docs
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.domain.rules.registry import RULES

pytestmark = pytest.mark.contract


class SyntheticRule:
    descriptor = RuleDescriptor(
        id="R01",
        version="1.0.0",
        title="Regra sintética",
        severity="alta",
        fundamento="Fonte: limite sintético.",
    )
    sql = "SELECT 1"

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        return None


def document_text(**overrides: str) -> str:
    fields = {
        "id": "R01",
        "version": "1.0.0",
        "severidade": "alta",
        "fundamento": "Fonte: limite sintético.",
        **overrides,
    }
    header = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    )
    return f"---\n{header}\n---\n\n# Regra sintética\n\nVerificar o limite.\n"


def write_document(directory: Path, text: str, name: str = "R01-teste.md") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_registry_and_documents_correspond() -> None:
    directory = Path(__file__).resolve().parents[2] / ".llms" / "rules"

    documents = load_rule_docs(directory, RULES)

    assert set(documents) == {rule.descriptor.id for rule in RULES}


def test_empty_registry_accepts_missing_directory_and_readme(tmp_path: Path) -> None:
    assert load_rule_docs(tmp_path / "absent", ()) == {}
    (tmp_path / "README.md").write_text("# Format guide\n", encoding="utf-8")
    assert load_rule_docs(tmp_path, ()) == {}


def test_loads_unicode_body_and_matching_metadata(tmp_path: Path) -> None:
    path = write_document(tmp_path, document_text())

    documents = load_rule_docs(tmp_path, (SyntheticRule,))

    assert set(documents) == {"R01"}
    document = documents["R01"]
    assert document.id == "R01"
    assert document.version == "1.0.0"
    assert document.severity == "alta"
    assert document.fundamento == "Fonte: limite sintético."
    assert document.body == "# Regra sintética\n\nVerificar o limite."
    assert document.path == path


@pytest.mark.parametrize(
    "header",
    [
        "id: R01\nversion: 1.0.0\nseveridade: alta\n"
        "fundamento: Fonte: limite sintético.",
        "# Metadata comment\nid: 'R01'\n\nversion: '1.0.0'\n"
        "severidade: 'alta'\nfundamento: 'Fonte: limite sintético.'",
    ],
)
def test_supported_plain_and_single_quoted_scalars(tmp_path: Path, header: str) -> None:
    write_document(tmp_path, f"---\n{header}\n---\nTexto.\n")

    assert load_rule_docs(tmp_path, (SyntheticRule,))["R01"].body == "Texto."


def test_single_quote_escaping_and_quoted_hash_are_preserved(tmp_path: Path) -> None:
    class QuotedRule(SyntheticRule):
        descriptor = RuleDescriptor(
            id="R01",
            version="1.0.0",
            title="Regra sintética",
            severity="alta",
            fundamento="Fonte d'água #1.",
        )

    text = document_text().replace(
        'fundamento: "Fonte: limite sintético."', "fundamento: 'Fonte d''água #1.'"
    )
    write_document(tmp_path, text)

    assert (
        load_rule_docs(tmp_path, (QuotedRule,))["R01"].fundamento == "Fonte d'água #1."
    )


def test_missing_registered_document_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuleDocumentError, match="missing: R01"):
        load_rule_docs(tmp_path / "absent", (SyntheticRule,))


def test_unregistered_document_is_rejected(tmp_path: Path) -> None:
    write_document(tmp_path, document_text())

    with pytest.raises(RuleDocumentError, match="unknown: R01"):
        load_rule_docs(tmp_path, ())


@pytest.mark.parametrize("field", ["id", "version", "severidade", "fundamento"])
def test_all_metadata_fields_are_required(tmp_path: Path, field: str) -> None:
    text = "\n".join(
        line
        for line in document_text().splitlines()
        if not line.startswith(f"{field}:")
    )
    write_document(tmp_path, text)

    with pytest.raises(RuleDocumentError, match=f"missing metadata: {field}"):
        load_rule_docs(tmp_path, (SyntheticRule,))


@pytest.mark.parametrize(
    ("field", "value", "attribute"),
    [
        ("version", "2.0.0", "version"),
        ("severidade", "revisar", "severity"),
        ("fundamento", "Outra fonte.", "fundamento"),
    ],
)
def test_metadata_must_match_descriptor(
    tmp_path: Path, field: str, value: str, attribute: str
) -> None:
    write_document(tmp_path, document_text(**{field: value}))

    with pytest.raises(RuleDocumentError, match=f"{attribute} differs from registry"):
        load_rule_docs(tmp_path, (SyntheticRule,))


def test_document_id_must_match_filename(tmp_path: Path) -> None:
    write_document(tmp_path, document_text(), name="R02-outro.md")

    with pytest.raises(
        RuleDocumentError, match="filename and frontmatter rule id differ"
    ):
        load_rule_docs(tmp_path, (SyntheticRule,))


def test_duplicate_documents_are_rejected(tmp_path: Path) -> None:
    write_document(tmp_path, document_text(), name="R01-primeiro.md")
    write_document(tmp_path, document_text(), name="R01-segundo.md")

    with pytest.raises(RuleDocumentError, match="duplicate document for rule: R01"):
        load_rule_docs(tmp_path, (SyntheticRule,))


def test_duplicate_registered_ids_are_rejected(tmp_path: Path) -> None:
    write_document(tmp_path, document_text())

    with pytest.raises(RuleDocumentError, match="duplicate registered rule: R01"):
        load_rule_docs(tmp_path, (SyntheticRule, SyntheticRule))


@pytest.mark.parametrize(
    "text",
    [
        "# No header\n",
        "---\nid: R01\n",
        document_text().replace('version: "1.0.0"', "version:"),
        document_text(version="   "),
        document_text().replace('version: "1.0.0"', 'version: "unterminated'),
        document_text().replace('version: "1.0.0"', "version: 'invalid'quote'"),
        document_text().replace('version: "1.0.0"', "version: [1, 0, 0]"),
        document_text().replace('version: "1.0.0"', "version: - 1.0.0"),
        document_text().replace('version: "1.0.0"', 'version: {"major": 1}'),
        document_text().replace('version: "1.0.0"', "version: |\n  1.0.0"),
        document_text().replace('version: "1.0.0"', "version: 1.0.0 # comment"),
        document_text().replace('version: "1.0.0"', "version: &version 1.0.0"),
        document_text().replace('version: "1.0.0"', 'version: "1.0.0"\nversion: 2'),
        document_text().replace('version: "1.0.0"', '  version: "1.0.0"'),
        document_text(unexpected="value"),
    ],
)
def test_malformed_or_unsupported_frontmatter_is_rejected(
    tmp_path: Path, text: str
) -> None:
    write_document(tmp_path, text)

    with pytest.raises(RuleDocumentError):
        load_rule_docs(tmp_path, (SyntheticRule,))


@pytest.mark.parametrize("name", ["R01.md", "R01-.md", "r01-lowercase.md", "notes.md"])
def test_malformed_document_filenames_are_rejected(tmp_path: Path, name: str) -> None:
    write_document(tmp_path, document_text(), name=name)

    with pytest.raises(RuleDocumentError, match="expected a filename"):
        load_rule_docs(tmp_path, (SyntheticRule,))


def test_rule_directory_must_be_a_directory(tmp_path: Path) -> None:
    path = write_document(tmp_path, document_text())

    with pytest.raises(RuleDocumentError, match="expected a rule document directory"):
        load_rule_docs(path, ())
