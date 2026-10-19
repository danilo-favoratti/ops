"""Read a documented scalar frontmatter subset and reconcile rule documents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from opsiu.domain.rules.base import Rule, RuleDescriptor

_FIELDS = frozenset({"id", "version", "severidade", "fundamento"})
_FILENAME = re.compile(r"(?P<id>R[0-9]{2})-.+\.md")


class RuleDocumentError(ValueError):
    """Rule metadata is malformed or inconsistent with the code registry."""


@dataclass(frozen=True, slots=True)
class RuleDocument:
    id: str
    version: str
    severity: str
    fundamento: str
    body: str
    path: Path


def _scalar(value: str, path: Path, line_number: int) -> str:
    location = f"{path}:{line_number}"
    if value.startswith('"'):
        try:
            decoded: object = json.loads(value)
        except json.JSONDecodeError as error:
            raise RuleDocumentError(f"{location}: invalid quoted scalar") from error
        if not isinstance(decoded, str):
            raise RuleDocumentError(f"{location}: metadata must be text")
        value = decoded.strip()
    elif value.startswith("'"):
        if re.fullmatch(r"'(?:[^']|'')*'", value) is None:
            raise RuleDocumentError(f"{location}: invalid single-quoted scalar")
        value = value[1:-1].replace("''", "'").strip()
    elif value and (
        value[0] in "[{}]>|&*!#?" or value.startswith("- ") or " #" in value
    ):
        raise RuleDocumentError(f"{location}: unsupported frontmatter syntax")
    if not value:
        raise RuleDocumentError(f"{location}: metadata must not be empty")
    return value


def _load_document(path: Path) -> RuleDocument:
    filename = _FILENAME.fullmatch(path.name)
    if filename is None:
        raise RuleDocumentError(f"{path}: expected a filename like R01-rule-name.md")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise RuleDocumentError(f"{path}: frontmatter must start with ---")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise RuleDocumentError(f"{path}: frontmatter is not closed") from error

    fields: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:closing], start=2):
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator or key not in _FIELDS:
            raise RuleDocumentError(
                f"{path}:{line_number}: unknown or malformed metadata field {key!r}"
            )
        if key in fields:
            raise RuleDocumentError(f"{path}:{line_number}: duplicate field {key}")
        fields[key] = _scalar(value.strip(), path, line_number)
    missing = _FIELDS - fields.keys()
    if missing:
        raise RuleDocumentError(
            f"{path}: missing metadata: {', '.join(sorted(missing))}"
        )
    if fields["id"] != filename.group("id"):
        raise RuleDocumentError(f"{path}: filename and frontmatter rule id differ")
    return RuleDocument(
        id=fields["id"],
        version=fields["version"],
        severity=fields["severidade"],
        fundamento=fields["fundamento"],
        body="\n".join(lines[closing + 1 :]).strip(),
        path=path,
    )


def load_rule_docs(
    directory: Path, rules: tuple[type[Rule], ...]
) -> dict[str, RuleDocument]:
    """Require exactly one matching document for every registered rule class."""
    descriptors: dict[str, RuleDescriptor] = {}
    for rule_type in rules:
        descriptor = rule_type.descriptor
        if descriptor.id in descriptors:
            raise RuleDocumentError(f"duplicate registered rule: {descriptor.id}")
        descriptors[descriptor.id] = descriptor

    if directory.exists() and not directory.is_dir():
        raise RuleDocumentError(f"{directory}: expected a rule document directory")
    documents: dict[str, RuleDocument] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md":
            continue
        document = _load_document(path)
        if document.id in documents:
            raise RuleDocumentError(f"duplicate document for rule: {document.id}")
        documents[document.id] = document

    missing = descriptors.keys() - documents.keys()
    unknown = documents.keys() - descriptors.keys()
    if missing or unknown:
        raise RuleDocumentError(
            f"rule document registry mismatch; missing: {', '.join(sorted(missing))}; "
            f"unknown: {', '.join(sorted(unknown))}"
        )
    for rule_id, descriptor in descriptors.items():
        document = documents[rule_id]
        for name in ("version", "severity", "fundamento"):
            if getattr(document, name) != getattr(descriptor, name):
                raise RuleDocumentError(
                    f"{document.path}: {name} differs from registry"
                )
    return documents
