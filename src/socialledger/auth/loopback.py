"""OAuth 2.0 authorization-code flow with a local loopback redirect and PKCE.

No hosted server is involved: a one-shot HTTP listener on 127.0.0.1 receives the redirect,
checks ``state``, and hands the authorization code back. Request lines are never logged, so
the code does not leak to the terminal.

Docker: set ``SOCIALLEDGER_OAUTH_PORT`` to a fixed port, publish it (``-p 8765:8765``) and set
``SOCIALLEDGER_OAUTH_BIND=0.0.0.0``. See docs/docker.md.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlparse

PORT_ENV = "SOCIALLEDGER_OAUTH_PORT"
BIND_ENV = "SOCIALLEDGER_OAUTH_BIND"


class OAuthFlowError(Exception):
    pass


@dataclass
class Pkce:
    verifier: str
    challenge: str
    method: str = "S256"

    @classmethod
    def generate(cls, encoding: Literal["base64url", "hex"] = "base64url") -> Pkce:
        """RFC 7636 S256. ``hex`` exists because TikTok's desktop docs specify hex encoding."""
        verifier = secrets.token_urlsafe(64)[:96]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        if encoding == "hex":
            challenge = digest.hex()
        else:
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(verifier=verifier, challenge=challenge)


@dataclass
class AuthResult:
    code: str
    redirect_uri: str
    pkce: Pkce | None
    extra: dict[str, str] = field(default_factory=dict)


_SUCCESS_PAGE = (
    "<!doctype html><meta charset=utf-8><title>socialledger</title>"
    "<body style='font-family:system-ui;margin:3rem'>"
    "<h1>{title}</h1><p>{body}</p><p>You can close this window and return to the terminal.</p>"
)


class _Handler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != self.server.callback_path.rstrip("/"):
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.result = params
        ok = "code" in params and "error" not in params
        title = "Connected" if ok else "Authorization failed"
        body = (
            "socialledger received the authorization."
            if ok
            else html.escape(params.get("error_description") or params.get("error") or "unknown")
        )
        page = _SUCCESS_PAGE.format(title=title, body=body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:
        # Never log request lines: they contain the authorization code.
        return


class _CallbackServer(HTTPServer):
    callback_path: str
    result: dict[str, str] | None = None


def run_loopback_flow(
    authorize_url: str,
    params: dict[str, str],
    *,
    host: str = "127.0.0.1",
    callback_path: str = "/callback",
    port: int | None = None,
    use_pkce: bool = True,
    pkce_encoding: Literal["base64url", "hex"] = "base64url",
    timeout: float = 300.0,
    open_browser: Callable[[str], bool] = webbrowser.open,
    announce: Callable[[str], None] = print,
) -> AuthResult:
    """Run the browser consent flow and return the authorization code.

    ``params`` holds the platform's authorize parameters (client_id, scope, ...);
    ``redirect_uri``, ``state``, ``response_type`` and PKCE fields are added here.
    """
    if port is None:
        port = int(os.environ.get(PORT_ENV, "0"))
    bind = os.environ.get(BIND_ENV, host)
    server = _CallbackServer((bind, port), _Handler)
    server.callback_path = callback_path
    server.timeout = timeout
    actual_port = server.server_address[1]
    redirect_uri = f"http://{host}:{actual_port}{callback_path}"

    state = secrets.token_urlsafe(24)
    pkce = Pkce.generate(pkce_encoding) if use_pkce else None
    query = {**params, "redirect_uri": redirect_uri, "state": state, "response_type": "code"}
    if pkce:
        query |= {"code_challenge": pkce.challenge, "code_challenge_method": pkce.method}
    url = f"{authorize_url}?{urlencode(query)}"

    announce("Opening your browser to sign in. If it does not open, visit this URL:\n" + url)
    open_browser(url)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        server.shutdown()
        server.server_close()
        raise OAuthFlowError(f"Timed out after {int(timeout)}s waiting for the browser sign-in.")
    server.server_close()

    result = server.result or {}
    if "error" in result:
        raise OAuthFlowError(
            f"The platform returned an error: {result.get('error')} "
            f"{result.get('error_description', '')}".strip()
        )
    if result.get("state") != state:
        raise OAuthFlowError("OAuth state mismatch; the sign-in was aborted for safety.")
    code = result.get("code")
    if not code:
        raise OAuthFlowError("No authorization code was received.")
    extra = {k: v for k, v in result.items() if k not in {"code", "state"}}
    return AuthResult(code=code, redirect_uri=redirect_uri, pkce=pkce, extra=extra)
