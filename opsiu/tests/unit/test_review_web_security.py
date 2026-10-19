"""Local form protection rejects forged metadata without losing request audits."""

import json
import os
import shutil
from pathlib import Path
from typing import Never

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from opsiu.interfaces.web import app as web_app
from opsiu.interfaces.web import runtime
from opsiu.interfaces.web.security import (
    COOKIE,
    FormSecurity,
    WebError,
    original_url,
    validate_host,
)
from opsiu.settings import PathSettings, Settings

pytestmark = pytest.mark.unit


def request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "http",
            "method": "POST",
            "path": "/findings/key/decisions",
            "query_string": b"",
            "server": ("127.0.0.1", 80),
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in {"host": "127.0.0.1", **(headers or {})}.items()
            ],
        }
    )


@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://127.0.0.1:80"])
def test_signed_token_accepts_equivalent_same_origin(origin: str) -> None:
    security = FormSecurity()
    token = security.token(request())
    submitted = request({"cookie": f"{COOKIE}={token}", "origin": origin})
    security.validate(submitted, token)
    assert security.token(submitted) == token


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://example.invalid"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1:8765"},
        {"origin": "http://127.0.0.1:invalid"},
        {"referer": "http://example.invalid/page"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ],
)
def test_forged_request_metadata_is_rejected(headers: dict[str, str]) -> None:
    security = FormSecurity()
    token = security.token(request())
    with pytest.raises(WebError) as failure:
        security.validate(request({"cookie": f"{COOKIE}={token}", **headers}), token)
    assert failure.value.status == 403


@pytest.mark.parametrize("token", ["", "forged", "não-ascii"])
def test_missing_or_tampered_form_token_is_rejected(token: str) -> None:
    security = FormSecurity()
    cookie = security.token(request())
    with pytest.raises(WebError) as failure:
        security.validate(request({"cookie": f"{COOKIE}={cookie}"}), token)
    assert failure.value.status == 403


def test_restart_invalidates_old_form_and_host_rebinding_is_rejected() -> None:
    token = FormSecurity().token(request())
    with pytest.raises(WebError):
        FormSecurity().validate(request({"cookie": f"{COOKIE}={token}"}), token)
    with pytest.raises(WebError) as failure:
        validate_host(request({"host": "attacker.invalid"}))
    assert failure.value.status == 400


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "//example.invalid/invoice",
        "https://name:password@example.invalid",
        "https://example.invalid/\nscript",
        "http://[invalid",
    ],
)
def test_unsafe_original_urls_never_become_links(url: str | None) -> None:
    assert original_url(url) is None


def test_http_invoice_links_preserve_the_original_url() -> None:
    assert original_url("https://example.invalid/invoice?id=007") == (
        "https://example.invalid/invoice?id=007"
    )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return Settings(
        paths=PathSettings(
            db_path=tmp_path / "db.duckdb",
            runs_dir=tmp_path / "runs",
            external_dir=tmp_path / "external",
            data_dir=tmp_path / "input",
        )
    )


def test_request_database_open_failure_keeps_a_durable_file_run(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*args: object, **kwargs: object) -> Never:
        raise PermissionError("PRIVATE_DATABASE_ERROR")

    monkeypatch.setattr(runtime, "connect_database", denied)
    with TestClient(
        web_app.create_app(settings), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/findings")
    assert response.status_code == 500
    assert "PRIVATE_DATABASE_ERROR" not in response.text
    run_id = response.headers["X-OPSIU-Run-ID"]
    manifest = json.loads(
        (settings.paths.runs_dir / run_id / "manifest.json").read_text()
    )
    assert manifest["status"] == "completed_with_errors"
    assert len(manifest["steps"]) == 1
    assert manifest["steps"][0]["status"] == "failed"
    assert not settings.paths.db_path.exists()


def test_template_failure_is_recorded_inside_the_get_step(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = tmp_path / "web"
    shutil.copytree(web_app._DIRECTORY / "templates", resources / "templates")
    (resources / "templates/list.html").write_text("{% invalid-template %}")
    monkeypatch.setattr(web_app, "_DIRECTORY", resources)
    with TestClient(
        web_app.create_app(settings), base_url="http://127.0.0.1"
    ) as client:
        response = client.get("/findings")
    assert response.status_code == 500
    run_id = response.headers["X-OPSIU-Run-ID"]
    manifest = json.loads(
        (settings.paths.runs_dir / run_id / "manifest.json").read_text()
    )
    assert manifest["status"] == "completed_with_errors"
    assert manifest["steps"][0]["status"] == "failed"
