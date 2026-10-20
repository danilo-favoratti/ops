"""Agent assets remain available when installed outside the source checkout."""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_packaged_prompts_and_rules_load_outside_checkout(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    configuration = tomllib.loads((project / "pyproject.toml").read_text())
    includes = configuration["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]
    installation = tmp_path / "installation"
    shutil.copytree(
        project / "src" / "opsiu",
        installation / "opsiu",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for source, destination in includes.items():
        source_path = project / source
        destination_path = installation / destination
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
        else:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
    working_directory = tmp_path / "unrelated"
    working_directory.mkdir()
    # A caller's working directory must not replace trusted packaged prompts.
    misleading = working_directory / ".llms" / "tasks"
    misleading.mkdir(parents=True)
    (misleading / "explicar-achado.md").write_text("Not a versioned prompt.")
    program = """
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from opsiu.adapters.agents.prompts import default_llms_directory, load_prompt
from opsiu.adapters.files.rule_docs import load_rule_docs
from opsiu.domain.rules.registry import RULES

directory = default_llms_directory().resolve()
assert directory.is_relative_to(Path(sys.argv[1]).resolve())
names = ('explicar-achado', 'revisar-nota', 'consolidar-deputado')
prompts = {name: load_prompt(name) for name in names}
for name, prompt in prompts.items():
    assert prompt.version == ('3' if name == 'explicar-achado' else '2')
    assert prompt.text.strip()
    assert prompt.sha256 == hashlib.sha256(
        (directory / 'tasks' / (name + '.md')).read_bytes()
    ).hexdigest()
documents = load_rule_docs(directory / 'rules', RULES)
assert len(documents) == 14
print(json.dumps({'prompts': list(prompts), 'rules': sorted(documents)}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", program, str(installation)],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["prompts"] == [
        "explicar-achado",
        "revisar-nota",
        "consolidar-deputado",
    ]
    assert result["rules"] == [f"R{number:02d}" for number in range(1, 15)]
