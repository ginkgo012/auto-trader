"""
OAuth 2.0 Authorization Code Grant flow for Saxo Bank OpenAPI.

Handles:
 - Full browser-based authorization when no tokens exist
 - Silent token refresh when access_token expires but refresh_token is valid
 - Token file cache in .tokens/<env>_token.json
"""

import asyncio
import json
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from config import (
    APP_KEY,
    APP_SECRET,
    AUTH_URL,
    TOKEN_URL,
    REDIRECT_URI,
    CALLBACK_PORT,
    TOKEN_FILE,
)

# ── Token file I/O ──────────────────────────────────────────────────────────


def load_token() -> dict | None:
    """Load cached token from disk. Returns None if file missing or corrupt."""
    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
        if "access_token" not in data:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_token(token_data: dict) -> None:
    """Persist token data to disk with timestamps."""
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"[AUTH] Token saved → {TOKEN_FILE}")


def is_access_token_valid(token_data: dict) -> bool:
    """Check if the access_token is still valid (with 30s safety margin)."""
    expires_at = token_data.get("access_token_expires_at", 0)
    return time.time() < (expires_at - 30)


def is_refresh_token_valid(token_data: dict) -> bool:
    """Check if the refresh_token is still valid (with 60s safety margin)."""
    expires_at = token_data.get("refresh_token_expires_at", 0)
    return time.time() < (expires_at - 60)


def _stamp_expiry(token_data: dict) -> dict:
    """Add absolute expiry timestamps to token data."""
    now = time.time()
    token_data["access_token_expires_at"] = now + token_data.get("expires_in", 1200)
    token_data["refresh_token_expires_at"] = now + token_data.get(
        "refresh_token_expires_in", 3600
    )
    token_data["obtained_at"] = now
    return token_data


# ── Token exchange / refresh ────────────────────────────────────────────────


async def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": APP_KEY,
                "client_secret": APP_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code // 100 != 2:
        raise RuntimeError(
            f"[AUTH] Token exchange failed ({resp.status_code}): {resp.text}"
        )
    token_data = resp.json()
    token_data = _stamp_expiry(token_data)
    save_token(token_data)
    print("[AUTH] Token exchange successful.")
    return token_data


async def refresh_access_token(token_data: dict) -> dict:
    """Use refresh_token to obtain a fresh access_token."""
    print("[AUTH] Refreshing access token …")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
                "redirect_uri": REDIRECT_URI,
                "client_id": APP_KEY,
                "client_secret": APP_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code // 100 != 2:
        raise RuntimeError(
            f"[AUTH] Token refresh failed ({resp.status_code}): {resp.text}"
        )
    new_data = resp.json()
    new_data = _stamp_expiry(new_data)
    save_token(new_data)
    print("[AUTH] Token refreshed successfully.")
    return new_data


# ── Local callback server ──────────────────────────────────────────────────


class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth callback code."""

    auth_code: str | None = None
    received_state: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        _CallbackHandler.auth_code = params.get("code", [None])[0]
        _CallbackHandler.received_state = params.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        body = (
            "<html><body><h2>Authorization complete.</h2>"
            "<p>You can close this tab and return to the terminal.</p>"
            "</body></html>"
        )
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        # Suppress default stderr logging
        pass


def _run_callback_server(timeout: int = 120) -> tuple[str | None, str | None]:
    """
    Start a blocking HTTP server on CALLBACK_PORT, wait for the redirect,
    then return (code, state). Times out after `timeout` seconds.
    """
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    server.timeout = timeout
    _CallbackHandler.auth_code = None
    _CallbackHandler.received_state = None
    server.handle_request()  # blocks until one request or timeout
    server.server_close()
    return _CallbackHandler.auth_code, _CallbackHandler.received_state


# ── Full authorization flow ─────────────────────────────────────────────────


async def authorize_via_browser() -> dict:
    """
    Open the Saxo login page in the default browser, wait for the callback,
    and exchange the code for tokens.
    """
    state = secrets.token_urlsafe(32)
    params = urlencode(
        {
            "response_type": "code",
            "client_id": APP_KEY,
            "state": state,
            "redirect_uri": REDIRECT_URI,
        }
    )
    auth_url = f"{AUTH_URL}?{params}"

    print(f"[AUTH] Opening browser for login …")
    print(f"[AUTH] If the browser does not open, visit:\n       {auth_url}")
    webbrowser.open(auth_url)

    # Run the blocking callback server in a thread so we don't block the loop
    loop = asyncio.get_running_loop()
    code, received_state = await loop.run_in_executor(
        None, _run_callback_server, 120
    )

    if not code:
        raise RuntimeError("[AUTH] No authorization code received (timeout?).")

    if received_state != state:
        raise RuntimeError(
            "[AUTH] OAuth state mismatch — possible CSRF. Aborting."
        )

    print("[AUTH] Authorization code received.")
    return await exchange_code_for_token(code)


# ── Main entry point ────────────────────────────────────────────────────────


async def ensure_token() -> dict:
    """
    Return a valid token dict, performing the minimum work needed:
      1. Cached token still valid → return it
      2. Access expired, refresh valid → silent refresh
      3. Both expired / no cache → full browser login
    """
    token_data = load_token()

    if token_data and is_access_token_valid(token_data):
        print("[AUTH] Cached access token is still valid.")
        return token_data

    if token_data and is_refresh_token_valid(token_data):
        try:
            return await refresh_access_token(token_data)
        except RuntimeError as exc:
            print(f"[AUTH] Refresh failed, falling back to browser login: {exc}")

    print("[AUTH] No valid token — starting browser authorization …")
    return await authorize_via_browser()
