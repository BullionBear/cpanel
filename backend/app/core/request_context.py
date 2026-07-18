"""Per-request context for audit enrichment (IP, device, auth method)."""
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.requests import Request


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None = None
    user_agent: str | None = None
    auth_method: str | None = None


_request_meta: ContextVar[RequestMeta] = ContextVar("request_meta", default=RequestMeta())


def get_request_meta() -> RequestMeta:
    return _request_meta.get()


def set_request_meta(meta: RequestMeta) -> None:
    _request_meta.set(meta)


def clear_request_meta() -> None:
    _request_meta.set(RequestMeta())


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or None
    if request.client and request.client.host:
        return request.client.host[:64]
    return None


def client_user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:512]
