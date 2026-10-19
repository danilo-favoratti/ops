"""Load versioned prompt bodies and hashes from packaged or checkout resources."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_TASKS = frozenset({"explicar-achado", "revisar-nota", "consolidar-deputado"})


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    text: str
    version: str
    sha256: str


def default_llms_directory() -> Path:
    """Prefer installed resources, then checkout resources anchored to this module."""
    module = Path(__file__).resolve()
    packaged = module.parents[2] / "_llms"
    if packaged.exists():
        if not packaged.is_dir():
            raise NotADirectoryError(
                f"prompt resources are not a directory: {packaged}"
            )
        return packaged
    for ancestor in module.parents:
        checkout = ancestor / ".llms"
        if checkout.is_dir():
            return checkout
    raise FileNotFoundError("prompt resources not found beside the installed package")


def _version(value: str) -> str:
    """Parse one trimmed text scalar without treating frontmatter as full YAML.

    Plain text, JSON double-quoted strings, and single-quoted strings with doubled
    apostrophes are supported. Collections, block scalars, tags, aliases, anchors,
    inline comments, and mapping syntax are deliberately rejected.
    """
    value = value.strip()
    if value.startswith('"'):
        try:
            decoded: object = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(
                "prompt_version has an invalid double-quoted scalar"
            ) from error
        if not isinstance(decoded, str):
            raise ValueError("prompt_version must be text")
        value = decoded.strip()
    elif value.startswith("'"):
        if re.fullmatch(r"'(?:[^']|'')*'", value) is None:
            raise ValueError("prompt_version has an invalid single-quoted scalar")
        value = value[1:-1].replace("''", "'").strip()
    elif value and (
        value[0] in "[{}]>|&*!#?"
        or value.startswith("- ")
        or " #" in value
        or ": " in value
    ):
        raise ValueError("prompt_version uses unsupported frontmatter syntax")
    if not value or "\n" in value or "\r" in value:
        raise ValueError("prompt_version must be nonempty text on one line")
    return value


def load_prompt(name: str, directory: Path | None = None) -> Prompt:
    """Read one known task; an explicit directory is the tasks directory itself."""
    if name not in _TASKS:
        raise ValueError(f"unknown prompt task: {name!r}")
    tasks = directory if directory is not None else default_llms_directory() / "tasks"
    path = tasks / f"{name}.md"
    raw = path.read_bytes()
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"prompt must be valid UTF-8: {path}") from error
    lines = decoded.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError(f"prompt frontmatter must start with ---: {path}")
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], 1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing is None:
        raise ValueError(f"prompt frontmatter is not closed: {path}")
    version: str | None = None
    for line in lines[1:closing]:
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.rstrip("\r\n").partition(":")
        if not separator or key != "prompt_version":
            raise ValueError(f"unknown or malformed prompt frontmatter: {path}")
        if version is not None:
            raise ValueError(f"duplicate prompt_version: {path}")
        version = _version(value)
    if version is None:
        raise ValueError(f"missing prompt_version: {path}")
    text = "".join(lines[closing + 1 :]).strip()
    if not text:
        raise ValueError(f"prompt body must not be empty: {path}")
    return Prompt(name, text, version, hashlib.sha256(raw).hexdigest())
