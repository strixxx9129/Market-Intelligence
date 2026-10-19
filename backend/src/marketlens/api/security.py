"""Edge-facing security middleware (pure ASGI: they only touch the scope and the headers).

ClientIPMiddleware
    Behind a platform proxy (Fly, Render, a load balancer) the TCP peer is the proxy, so every
    visitor would look like one IP and share one anonymous rate limit. X-Forwarded-For can't
    be trusted blindly: a client can send its own and pick any IP. The platform's dedicated
    header (Fly-Client-IP) is SET by the edge, overwriting whatever the client sent, so we use
    exactly that header and nothing else (TRUSTED_CLIENT_IP_HEADER).

SecurityHeadersMiddleware
    Defence in depth for browsers talking to the API:
    - Strict-Transport-Security: the browser refuses plain HTTP for this host for a year, so
      a network attacker can't downgrade a visitor (deployed environments only).
    - X-Content-Type-Options: nosniff: JSON is never re-interpreted as HTML/JS.
    - Content-Security-Policy default-src 'none' + frame-ancestors 'none': an API response
      rendered as a page can't load or run anything, and can't be framed (clickjacking).
      The Swagger page (/docs) needs its CDN assets and an inline init script, so it gets
      its own narrower policy.
    - Referrer-Policy: no-referrer, Permissions-Policy: no camera/mic/geolocation.
"""

import ipaddress

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "font-src https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'"
)
DOCS_PATHS = ("/docs", "/redoc")


class ClientIPMiddleware:
    def __init__(self, app: ASGIApp, header: str | None) -> None:
        self.app = app
        self.header = header.lower().encode("latin-1") if header else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.header and scope["type"] in ("http", "websocket"):
            for name, value in scope.get("headers", []):
                if name == self.header:
                    raw = value.decode("latin-1").strip()
                    try:
                        ip = str(ipaddress.ip_address(raw))
                    except ValueError:
                        break  # malformed: keep the socket address
                    port = (scope.get("client") or (None, 0))[1]
                    scope = {**scope, "client": (ip, port)}
                    break
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts_max_age_s: int = 0) -> None:
        self.app = app
        self.hsts = (
            f"max-age={hsts_max_age_s}; includeSubDomains".encode() if hsts_max_age_s else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        csp = DOCS_CSP if path.startswith(DOCS_PATHS) else API_CSP

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"content-security-policy", csp.encode()),
                ]
                if self.hsts:
                    extra.append((b"strict-transport-security", self.hsts))
                headers += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
