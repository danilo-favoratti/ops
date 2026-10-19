"""Local-host validation and signed double-submit tokens for review forms."""

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request

COOKIE = "opsiu_csrf"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class WebError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def validate_host(request: Request) -> None:
    if request.url.hostname not in LOCAL_HOSTS:
        raise WebError(400, "A revisão está disponível apenas no endereço local.")


class FormSecurity:
    def __init__(self) -> None:
        self.secret = secrets.token_bytes(32)

    def _signature(self, nonce: str) -> str:
        return hmac.new(self.secret, nonce.encode("ascii"), hashlib.sha256).hexdigest()

    def _valid(self, token: str) -> bool:
        try:
            nonce, signature = token.split(".", 1)
            return bool(nonce) and hmac.compare_digest(
                signature, self._signature(nonce)
            )
        except (ValueError, UnicodeError, TypeError):
            return False

    def token(self, request: Request) -> str:
        supplied = request.cookies.get(COOKIE, "")
        if self._valid(supplied):
            return supplied
        nonce = secrets.token_urlsafe(32)
        return f"{nonce}.{self._signature(nonce)}"

    def validate(self, request: Request, token: str) -> None:
        validate_host(request)
        cookie = request.cookies.get(COOKIE, "")
        if (
            not self._valid(cookie)
            or not token.isascii()
            or not hmac.compare_digest(cookie, token)
        ):
            raise WebError(403, "Formulário inválido ou expirado; recarregue a página.")
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        supplied_origin = origin if origin is not None else referer
        if supplied_origin is not None:
            try:
                parsed = urlsplit(supplied_origin)
                expected = urlsplit(str(request.url))
                valid = (
                    parsed.scheme == expected.scheme
                    and parsed.hostname == expected.hostname
                    and (parsed.port or (443 if parsed.scheme == "https" else 80))
                    == (expected.port or (443 if expected.scheme == "https" else 80))
                    and parsed.username is None
                    and parsed.password is None
                )
            except ValueError:
                valid = False
            if not valid:
                raise WebError(403, "A origem do formulário não é permitida.")
        if request.headers.get("sec-fetch-site", "same-origin") not in {
            "same-origin",
            "none",
        }:
            raise WebError(403, "A origem do formulário não é permitida.")


def original_url(value: str | None) -> str | None:
    if not value or any(ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return value
    except ValueError:
        pass
    return None
