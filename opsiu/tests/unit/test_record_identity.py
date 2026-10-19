"""Source-scoped identity preserves legacy document keys and exact record hashes."""

import hashlib

import pytest

from opsiu.domain.findings import finding_key, source_record_identity


def test_source_identity_is_stable_and_separates_years_and_records() -> None:
    identity = source_record_identity(2025, "a" * 64)
    assert identity == source_record_identity(2025, "a" * 64)
    assert identity != source_record_identity(2026, "a" * 64)
    assert identity != source_record_identity(2025, "b" * 64)
    assert finding_key("R12", "2", 0, identity) != finding_key("R12", "2", 0)
    assert (
        finding_key("R12", "1", 123) == hashlib.sha256(b'["R12","1",123]').hexdigest()
    )
    with pytest.raises(ValueError):
        finding_key("R12", "2", 123, identity)
    with pytest.raises(ValueError):
        source_record_identity(2025, "invalid")


def test_unidentified_document_label_retains_positive_document_numbers() -> None:
    from jinja2 import Environment, FileSystemLoader

    from opsiu.interfaces.web.app import _DIRECTORY
    from opsiu.interfaces.web.presentation import money

    environment = Environment(loader=FileSystemLoader(_DIRECTORY / "templates"))
    environment.filters["brl"] = money
    template = environment.from_string(
        '{% from "macros.html" import document_label %}{{ document_label(value) }}'
    )
    assert template.render(value=0) == "Documento sem identificador"
    assert template.render(value=123) == "Documento 123"
