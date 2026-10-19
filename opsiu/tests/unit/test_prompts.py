"""Prompt bytes, metadata boundaries, and location-independent resource lookup."""

import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from opsiu.adapters.agents import prompts
from opsiu.adapters.agents.prompts import default_llms_directory, load_prompt

pytestmark = pytest.mark.unit


def write_prompt(
    directory: Path, content: bytes, name: str = "explicar-achado"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "name", ["explicar-achado", "revisar-nota", "consolidar-deputado"]
)
def test_known_tasks_load_body_only_and_hash_all_original_bytes(
    tmp_path: Path, name: str
) -> None:
    raw = (
        "---\r\nprompt_version: '  versão-1  '\r\n---\r\n\r\nExplique o achado.\r\n"
    ).encode()
    write_prompt(tmp_path, raw, name)
    prompt = load_prompt(name, tmp_path)
    assert prompt.name == name
    assert prompt.text == "Explique o achado."
    assert prompt.version == "versão-1"
    assert prompt.sha256 == hashlib.sha256(raw).hexdigest()
    with pytest.raises(FrozenInstanceError):
        setattr(prompt, "version", "2")


@pytest.mark.parametrize(
    "scalar,expected",
    [
        ("1", "1"),
        ("  release-1.2  ", "release-1.2"),
        ('"  versão-2  "', "versão-2"),
        (r'"vers\u00e3o-3"', "versão-3"),
        ("'release''s-version'", "release's-version"),
    ],
)
def test_documented_plain_and_quoted_version_scalars(
    tmp_path: Path, scalar: str, expected: str
) -> None:
    write_prompt(
        tmp_path,
        f"---\n# Version marker\nprompt_version: {scalar}\n\n---\nCorpo.\n".encode(),
    )
    assert load_prompt("explicar-achado", tmp_path).version == expected


def test_version_body_and_line_endings_each_change_original_byte_hash(
    tmp_path: Path,
) -> None:
    bodies = (
        b"---\nprompt_version: 1\n---\nCorpo.\n",
        b"---\nprompt_version: 2\n---\nCorpo.\n",
        b"---\nprompt_version: 2\n---\nOutro corpo.\n",
        b"---\r\nprompt_version: 2\r\n---\r\nOutro corpo.\r\n",
    )
    loaded = []
    for raw in bodies:
        write_prompt(tmp_path, raw)
        loaded.append(load_prompt("explicar-achado", tmp_path))
    assert len({prompt.sha256 for prompt in loaded}) == len(bodies)
    assert loaded[0].text == loaded[1].text
    assert loaded[1].version == loaded[2].version
    assert loaded[2].text == loaded[3].text


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"Corpo sem frontmatter.",
        b"\n---\nprompt_version: 1\n---\nCorpo.",
        b"---\nprompt_version: 1\nCorpo.",
        b"---\n---\nCorpo.",
        b"---\nprompt_version:\n---\nCorpo.",
        b"---\nprompt_version: ''\n---\nCorpo.",
        b'---\nprompt_version: "  "\n---\nCorpo.',
        b"---\nprompt_version: 1\nprompt_version: 2\n---\nCorpo.",
        b"---\nprompt_version: 1\nunknown: value\n---\nCorpo.",
        b"---\nversion: 1\n---\nCorpo.",
        b"---\nprompt_version 1\n---\nCorpo.",
        b"---\n prompt_version: 1\n---\nCorpo.",
        b"---\nprompt_version: 1\n---\n \t\n",
        b"---\nprompt_version: 1\n---\n\xff",
        b"---\nprompt_version: [1, 2]\n---\nCorpo.",
        b"---\nprompt_version: {version: 1}\n---\nCorpo.",
        b"---\nprompt_version: |\n---\nCorpo.",
        b"---\nprompt_version: >\n---\nCorpo.",
        b"---\nprompt_version: *alias\n---\nCorpo.",
        b"---\nprompt_version: &anchor 1\n---\nCorpo.",
        b"---\nprompt_version: !tag 1\n---\nCorpo.",
        b"---\nprompt_version: 1 # comment\n---\nCorpo.",
        b"---\nprompt_version: version: 1\n---\nCorpo.",
        b"---\nprompt_version: 'unfinished\n---\nCorpo.",
        b'---\nprompt_version: "unfinished\n---\nCorpo.',
        b'---\nprompt_version: "1" trailing\n---\nCorpo.',
        b"---\nprompt_version: '1' trailing\n---\nCorpo.",
        b'---\nprompt_version: "a\\nb"\n---\nCorpo.',
    ],
)
def test_malformed_frontmatter_empty_body_and_bad_utf8_are_rejected(
    tmp_path: Path, raw: bytes
) -> None:
    write_prompt(tmp_path, raw)
    with pytest.raises(ValueError):
        load_prompt("explicar-achado", tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "unknown",
        "explicar-achado.md",
        "../explicar-achado",
        "/explicar-achado",
        "tasks/explicar-achado",
        "..\\explicar-achado",
        "EXPLICAR-ACHADO",
        "explicar-achado\x00",
    ],
)
def test_task_names_reject_traversal_and_unknown_files(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError, match="unknown prompt task"):
        load_prompt(name, tmp_path / "does-not-exist")


def test_explicit_directory_is_tasks_directory_and_missing_file_is_not_hidden(
    tmp_path: Path,
) -> None:
    write_prompt(tmp_path / "tasks", b"---\nprompt_version: 1\n---\nCorpo.")
    assert load_prompt("explicar-achado", tmp_path / "tasks").text == "Corpo."
    with pytest.raises(FileNotFoundError):
        load_prompt("explicar-achado", tmp_path)


def fake_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "checkout"
    module = project / "src/opsiu/adapters/agents/prompts.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(prompts, "__file__", str(module))
    return project


def test_default_lookup_prefers_packaged_resources_and_ignores_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = fake_module(tmp_path, monkeypatch)
    packaged = project / "src/opsiu/_llms"
    write_prompt(packaged / "tasks", b"---\nprompt_version: packaged\n---\nInstalado.")
    write_prompt(project / ".llms/tasks", b"---\nprompt_version: checkout\n---\nLocal.")
    unrelated = tmp_path / "unrelated"
    write_prompt(unrelated / ".llms/tasks", b"---\nprompt_version: cwd\n---\nCWD.")
    monkeypatch.chdir(unrelated)
    assert default_llms_directory() == packaged
    assert load_prompt("explicar-achado").version == "packaged"


def test_default_checkout_lookup_is_anchored_to_module_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = fake_module(tmp_path, monkeypatch)
    checkout = project / ".llms"
    write_prompt(checkout / "tasks", b"---\nprompt_version: checkout\n---\nLocal.")
    unrelated = tmp_path / "unrelated"
    write_prompt(unrelated / ".llms/tasks", b"---\nprompt_version: cwd\n---\nCWD.")
    monkeypatch.chdir(unrelated)
    assert default_llms_directory() == checkout
    assert load_prompt("explicar-achado").version == "checkout"


def test_default_lookup_never_falls_back_to_cwd_only_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_module(tmp_path, monkeypatch)
    unrelated = tmp_path / "unrelated"
    write_prompt(unrelated / ".llms/tasks", b"---\nprompt_version: cwd\n---\nCWD.")
    monkeypatch.chdir(unrelated)
    with pytest.raises(FileNotFoundError, match="prompt resources"):
        default_llms_directory()


def test_packaged_resource_path_must_be_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = fake_module(tmp_path, monkeypatch)
    (project / "src/opsiu/_llms").touch()
    with pytest.raises(NotADirectoryError):
        default_llms_directory()
