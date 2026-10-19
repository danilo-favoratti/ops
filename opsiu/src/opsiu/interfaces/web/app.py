"""Portuguese local review pages, each backed by a durable request run."""

import json
from collections.abc import Callable
from dataclasses import fields
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from opsiu.adapters.agents.prompts import default_llms_directory
from opsiu.adapters.files.review_pdf import read_review_pdf
from opsiu.adapters.files.rule_docs import load_rule_docs
from opsiu.application.ports import JsonValue, ReviewFilters
from opsiu.application.review import ReviewService
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import evidence_json
from opsiu.domain.models import Competencia
from opsiu.domain.rules import registry
from opsiu.interfaces.web.runtime import RequestScope
from opsiu.interfaces.web.security import (
    COOKIE,
    FormSecurity,
    WebError,
    original_url,
    validate_host,
)
from opsiu.settings import Settings

_DIRECTORY = Path(__file__).parent
_LABELS = {
    "nome_parlamentar": "Parlamentar",
    "cpf": "CPF do parlamentar",
    "numero_carteira_parlamentar": "Carteira parlamentar",
    "legislatura": "Legislatura",
    "sigla_uf": "UF",
    "sigla_partido": "Partido",
    "codigo_legislatura": "Código da legislatura",
    "numero_sub_cota": "Código da categoria",
    "descricao": "Categoria",
    "numero_especificacao_sub_cota": "Código da especificação",
    "descricao_especificacao": "Especificação",
    "fornecedor": "Fornecedor",
    "cnpj_cpf": "CPF/CNPJ do fornecedor",
    "payer_kind": "Tipo do fornecedor",
    "numero": "Número do documento",
    "tipo_documento": "Tipo do documento",
    "data_emissao": "Data de emissão",
    "valor_documento": "Valor do documento (R$)",
    "valor_glosa": "Glosa (R$)",
    "valor_liquido": "Valor líquido (R$)",
    "competencia": "Competência",
    "parcela": "Parcela",
    "passageiro": "Passageiro",
    "trecho": "Trecho",
    "lote": "Lote",
    "ressarcimento": "Ressarcimento",
    "data_pagamento_restituicao": "Data de restituição",
    "restituicao": "Restituição (R$)",
    "numero_deputado_id": "Identificador do deputado",
    "id_documento": "Identificador do documento",
    "url_documento": "Endereço original do documento",
    "record_sha256": "SHA-256 do registro",
}


def _value(value: object) -> str:
    if value is None:
        return "Não informado"
    if isinstance(value, Competencia):
        return f"{value.mes:02d}/{value.ano}"
    if isinstance(value, Decimal):
        return format(value, ".2f").replace(".", ",")
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _minimum(value: str) -> Decimal | None:
    if not value:
        return None
    if "," in value:
        if "." in value or value.count(",") != 1:
            raise ValueError("Informe o valor sem separadores de milhar.")
        value = value.replace(",", ".")
    return Decimal(value)


def _audited[T](
    scope: RequestScope,
    command: str,
    arguments: dict[str, JsonValue],
    step_name: str,
    operation: Callable[[], T],
) -> T:
    error: Exception | None = None
    result: T | None = None
    with scope.run_factory(command, arguments) as run:
        with run.step(step_name):
            try:
                scope.preflight()
                result = operation()
            except Exception as caught:
                error = caught
                raise
    if error is not None:
        raise error
    return cast(T, result)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else Settings()
    templates = Jinja2Templates(directory=_DIRECTORY / "templates")
    security = FormSecurity()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def scope_for(request: Request, token: str | None = None) -> RequestScope:
        return RequestScope(
            configured,
            preflight=(
                lambda: (
                    validate_host(request)
                    if token is None
                    else security.validate(request, token)
                )
            ),
        )

    def service(scope: RequestScope) -> ReviewService:
        return ReviewService(
            scope.repository, scope.run_factory, reviewer=configured.review.reviewer
        )

    def complete(response: Response, scope: RequestScope) -> Response:
        if scope.run is not None:
            response.headers["X-OPSIU-Run-ID"] = scope.run.run_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def render(
        request: Request,
        scope: RequestScope,
        name: str,
        context: dict[str, object],
        *,
        status: int = 200,
    ) -> Response:
        token = security.token(request)
        response = templates.TemplateResponse(
            request=request,
            name=name,
            context={
                "csrf_token": token,
                "run_id": scope.run.run_id if scope.run is not None else None,
                "reviewer": configured.review.reviewer,
                "error": None,
                "success": None,
                **context,
            },
            status_code=status,
        )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return complete(response, scope)

    def failure(request: Request, scope: RequestScope, error: Exception) -> Response:
        if isinstance(error, WebError):
            status, message = error.status, str(error)
        elif isinstance(error, (LookupError, FileNotFoundError)):
            status, message = 404, "Achado ou arquivo não encontrado."
        elif isinstance(error, DomainError):
            status, message = (
                409,
                "A operação não é compatível com o estado atual "
                "do achado ou de suas evidências.",
            )
        elif isinstance(error, (ValueError, InvalidOperation)):
            status, message = 400, "Confira os parâmetros ou a decisão informada."
        else:
            status, message = (
                500,
                "Não foi possível concluir a operação. Consulte a execução registrada.",
            )
        detail = scope.repository.last_detail
        if request.headers.get("hx-request") == "true" and detail is not None:
            return render(
                request,
                scope,
                "decision_panel.html",
                {"detail": detail, "error": message},
                status=status,
            )
        return render(request, scope, "error.html", {"error": message}, status=status)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/findings", response_class=HTMLResponse)
    def list_findings(request: Request) -> Response:
        scope = scope_for(request)
        raw = {
            name: request.query_params.get(name, "")
            for name in ("rule", "deputy", "uf", "min_amount", "offset")
        }

        def load() -> dict[str, object]:
            filters = ReviewFilters(
                rule_id=raw["rule"] or None,
                deputy_id=int(raw["deputy"]) if raw["deputy"] else None,
                uf=raw["uf"] or None,
                min_amount=_minimum(raw["min_amount"]),
                offset=int(raw["offset"] or "0"),
            )
            items = service(scope).list_pending(filters)
            return {
                "items": items,
                "filters": raw,
                "rules": tuple(rule.descriptor for rule in registry.RULES),
                "next_offset": filters.offset + filters.limit
                if len(items) == filters.limit
                else None,
                "previous_offset": max(0, filters.offset - filters.limit)
                if filters.offset
                else None,
            }

        try:
            return _audited(
                scope,
                "review.list",
                dict(raw),
                "listar_pendentes",
                lambda: render(request, scope, "list.html", load()),
            )
        except Exception as error:
            return failure(request, scope, error)

    @app.get("/findings/{finding_key}", response_class=HTMLResponse)
    def finding_detail(request: Request, finding_key: str) -> Response:
        scope = scope_for(request)

        def load() -> dict[str, object]:
            detail = service(scope).get_finding(finding_key)
            documents = load_rule_docs(
                default_llms_directory() / "rules", registry.RULES
            )
            document = documents.get(detail.record.finding.rule_id)
            matches = (
                document is not None
                and document.version == detail.record.finding.rule_version
            )
            return {
                "detail": detail,
                "rule_basis": document.fundamento
                if document is not None and matches
                else None,
                "rule_text": document.body
                if document is not None and matches
                else None,
                "rule_unavailable": not matches,
                "original_url": original_url(detail.record.url_documento),
                "expense_fields": tuple(
                    (_LABELS[field.name], _value(getattr(detail.expense, field.name)))
                    for field in fields(detail.expense)
                ),
                "evidence_text": json.dumps(
                    json.loads(evidence_json(detail.record.finding.evidence)),
                    ensure_ascii=False,
                    indent=2,
                ),
            }

        try:
            return _audited(
                scope,
                "review.detail",
                {"finding_key": finding_key},
                "abrir_achado",
                lambda: render(request, scope, "detail.html", load()),
            )
        except Exception as error:
            return failure(request, scope, error)

    @app.post("/findings/{finding_key}/decisions")
    async def decide(request: Request, finding_key: str) -> Response:
        try:
            form = await request.form(max_files=0, max_fields=10, max_part_size=65_536)
            decision, comment, token = (
                form.get("decision", ""),
                form.get("comment", ""),
                form.get("csrf_token", ""),
            )
            if not all(isinstance(value, str) for value in (decision, comment, token)):
                raise WebError(400, "O formulário deve conter apenas campos de texto.")
        except Exception as form_error:
            scope = scope_for(request)
            cause = form_error

            def invalid_form() -> None:
                raise WebError(400, "Não foi possível ler o formulário.") from cause

            try:
                _audited(
                    scope,
                    "review.decide",
                    {"finding_key": finding_key},
                    "registrar_decisao",
                    invalid_form,
                )
            except Exception as recorded:
                return failure(request, scope, recorded)
            raise AssertionError("invalid form must fail")
        scope = scope_for(request, cast(str, token))
        try:
            service(scope).decide(finding_key, cast(str, decision), cast(str, comment))
            if request.headers.get("hx-request") == "true":
                detail = scope.repository.last_detail
                if detail is None:
                    raise RuntimeError("saved decision has no refreshed detail")
                return render(
                    request,
                    scope,
                    "decision_panel.html",
                    {"detail": detail, "success": "Decisão registrada no histórico."},
                )
            return complete(
                RedirectResponse(f"/findings/{finding_key}", status_code=303), scope
            )
        except Exception as error:
            return failure(request, scope, error)

    @app.get("/findings/{finding_key}/pdf")
    def pdf(request: Request, finding_key: str) -> Response:
        scope = scope_for(request)

        def load() -> bytes:
            detail = service(scope).get_finding(finding_key)
            if detail.pdf is None:
                raise LookupError("no cached PDF")
            return read_review_pdf(detail.pdf.content, configured.paths.external_dir)

        try:
            data = _audited(
                scope, "review.pdf", {"finding_key": finding_key}, "abrir_pdf", load
            )
            response = Response(
                data,
                media_type="application/pdf",
                headers={"Content-Disposition": 'inline; filename="documento.pdf"'},
            )
            return complete(response, scope)
        except Exception as error:
            return failure(request, scope, error)

    @app.get("/static/{filename}")
    def static(request: Request, filename: str) -> Response:
        scope = scope_for(request)

        def load() -> bytes:
            if filename not in {"htmx.min.js", "review.css"}:
                raise FileNotFoundError(filename)
            return (_DIRECTORY / "static" / filename).read_bytes()

        try:
            data = _audited(
                scope, "review.asset", {"asset": filename}, "abrir_recurso", load
            )
            content_type = "text/javascript" if filename.endswith(".js") else "text/css"
            return complete(Response(data, media_type=content_type), scope)
        except Exception as error:
            return failure(request, scope, error)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> Response:
        scope = scope_for(request)

        def reject() -> None:
            raise WebError(error.status_code, "Página ou operação não encontrada.")

        try:
            _audited(
                scope,
                "review.request",
                {"method": request.method},
                "validar_requisicao",
                reject,
            )
        except Exception as recorded:
            return failure(request, scope, recorded)
        raise AssertionError("HTTP error must fail")

    return app
