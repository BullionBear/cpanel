"""Record authenticated API activity (IP, device, auth method, return code)."""
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.request_context import (
    RequestMeta,
    clear_request_meta,
    client_ip,
    client_user_agent,
    set_request_meta,
)
from app.core.security import decode_access_token
from app.models.audit_log import AuditLog

# Paths that should never create activity rows (noise / public).
_SKIP_PREFIXES = (
    "/health",
    "/status",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/discord/login",
    "/auth/discord/callback",
)

# Authenticated GETs that are read-only UI chatter.
_SKIP_GET_EXACT = {
    "/audit",
    "/auth/me",
    "/auth/api-token",
}


def _should_skip(method: str, path: str) -> bool:
    if path == "/" or any(path == p or path.startswith(p + "/") for p in _SKIP_PREFIXES):
        return True
    if method == "GET" and path in _SKIP_GET_EXACT:
        return True
    return False


def _auth_from_header(authorization: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (actor_id, actor_name, auth_method) when a bearer JWT is present."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None, None
    token = authorization[7:].strip()
    if not token:
        return None, None, None
    payload = decode_access_token(token)
    if payload is None or not payload.get("sub"):
        return None, None, None
    token_type = payload.get("type")
    if token_type == "ui_access":
        auth_method = "discord_oauth"
    elif token_type == "api_access":
        auth_method = "api_key"
    else:
        return None, None, None
    return str(payload["sub"]), payload.get("name") or None, auth_method


class ActivityAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        ip = client_ip(request)
        ua = client_user_agent(request)
        actor_id, actor_name, auth_method = _auth_from_header(request.headers.get("authorization"))
        set_request_meta(RequestMeta(ip_address=ip, user_agent=ua, auth_method=auth_method))
        try:
            response = await call_next(request)
        finally:
            clear_request_meta()

        if actor_id and not _should_skip(request.method, request.url.path):
            await self._record(request, response, actor_id, actor_name, auth_method, ip, ua)
        return response

    async def _record(
        self,
        request: Request,
        response: Response,
        actor_id: str,
        actor_name: str | None,
        auth_method: str | None,
        ip: str | None,
        ua: str | None,
    ) -> None:
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            return
        now = datetime.now(timezone.utc)
        status_code = response.status_code
        entry = AuditLog(
            actor_type="user",
            actor_id=actor_id,
            actor_name=actor_name,
            action="http.request",
            target=f"{request.method} {request.url.path}",
            params=None,
            status="succeeded" if status_code < 400 else "failed",
            error=None if status_code < 400 else f"HTTP {status_code}",
            ip_address=ip,
            user_agent=ua,
            auth_method=auth_method,
            return_code=status_code,
            started_at=now,
            finished_at=now,
        )
        try:
            async with factory() as session:
                session.add(entry)
                await session.commit()
        except Exception:
            # Activity logging must never break the response path.
            pass
