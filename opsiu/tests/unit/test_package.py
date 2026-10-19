"""Package installation and command-line smoke tests."""

import pytest
from typer.testing import CliRunner

import opsiu
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.unit


def test_package_import() -> None:
    assert opsiu.__version__ == "0.1.0"


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CEAP" in result.stdout
    assert "db" in result.stdout
