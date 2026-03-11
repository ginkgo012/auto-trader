"""
Async HTTP wrapper around the Saxo Bank OpenAPI.

Provides:
 - get / post / put / patch / delete convenience methods
 - Automatic Authorization header injection
 - Background token refresh loop
 - Session capability upgrade + polling
"""

import asyncio

import httpx

from auth.oauth import (
    ensure_token,
    refresh_access_token,
    is_access_token_valid,
    is_refresh_token_valid,
    save_token,
    authorize_via_browser,
)
from config import BASE_URL, TOKEN_REFRESH_INTERVAL, SESSION_POLL_INTERVAL


class SaxoClient:
    """Thin async HTTP client for Saxo OpenAPI with auto-refresh."""

    def __init__(self) -> None:
        self.base_url: str = BASE_URL
        self.token_data: dict = {}
        self._http: httpx.AsyncClient | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def init(self) -> None:
        """Authenticate and create the underlying httpx client."""
        self.token_data = await ensure_token()
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
            headers=self._auth_headers(),
        )
        print("[SESSION] SaxoClient initialised.")

    async def close(self) -> None:
        """Shut down the HTTP client gracefully."""
        if self._http:
            await self._http.aclose()
            self._http = None
        print("[SESSION] SaxoClient closed.")

    # ── Auth header helpers ─────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_data['access_token']}"}

    def _update_auth(self) -> None:
        """Push the latest token into the persistent httpx client headers."""
        if self._http:
            self._http.headers.update(self._auth_headers())

    # ── Generic HTTP verbs ──────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        assert self._http is not None, "Call .init() before making requests."
        resp = await self._http.request(
            method,
            path,
            params=params,
            json=json_body,
        )
        # If 401, attempt a single token refresh then retry once
        if resp.status_code == 401:
            print("[SESSION] 401 — attempting token refresh …")
            await self._do_refresh()
            resp = await self._http.request(
                method,
                path,
                params=params,
                json=json_body,
            )
        if resp.status_code >= 400:
            print(
                f"[ERROR] {method} {path} → {resp.status_code}: "
                f"{resp.text[:300]}"
            )
        return resp

    async def get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        return await self._request("GET", path, params=params)

    async def post(
        self, path: str, *, json_body: dict | None = None
    ) -> httpx.Response:
        return await self._request("POST", path, json_body=json_body)

    async def put(
        self, path: str, *, json_body: dict | None = None
    ) -> httpx.Response:
        return await self._request("PUT", path, json_body=json_body)

    async def patch(
        self, path: str, *, json_body: dict | None = None
    ) -> httpx.Response:
        return await self._request("PATCH", path, json_body=json_body)

    async def delete(
        self, path: str, *, params: dict | None = None
    ) -> httpx.Response:
        return await self._request("DELETE", path, params=params)

    # ── Token refresh (internal + background loop) ──────────────────────

    async def _do_refresh(self) -> None:
        """Refresh the access token, falling back to full re-auth."""
        if is_refresh_token_valid(self.token_data):
            try:
                self.token_data = await refresh_access_token(self.token_data)
                self._update_auth()
                return
            except RuntimeError as exc:
                print(f"[AUTH] Refresh failed: {exc}")

        print("[AUTH] Refresh token expired — re-authorizing via browser …")
        self.token_data = await authorize_via_browser()
        self._update_auth()

    async def token_refresh_loop(self) -> None:
        """
        Background task: refresh access_token every TOKEN_REFRESH_INTERVAL
        seconds so it never expires mid-session.
        """
        while True:
            await asyncio.sleep(TOKEN_REFRESH_INTERVAL)
            try:
                if not is_access_token_valid(self.token_data):
                    print("[AUTH] Access token expired — refreshing now.")
                await self._do_refresh()
                print("[AUTH] Scheduled token refresh complete.")
            except Exception as exc:
                print(f"[ERROR] Token refresh loop error: {exc}")

    # ── Session capability management ───────────────────────────────────

    async def upgrade_session(self) -> bool:
        """
        PATCH /root/v1/sessions/capabilities → request FullTradingAndChat.
        Returns True on 200/202, False on error.
        """
        resp = await self.patch(
            "/root/v1/sessions/capabilities",
            json_body={"TradeLevel": "FullTradingAndChat"},
        )
        if resp.status_code in (200, 202, 204):
            print("[SESSION] Upgrade to FullTradingAndChat requested (202).")
            return True
        print(
            f"[SESSION] Upgrade failed ({resp.status_code}): "
            f"{resp.text[:200]}"
        )
        return False

    async def poll_session_capability(self) -> None:
        """
        Background task: every SESSION_POLL_INTERVAL seconds, check
        current TradeLevel and re-upgrade if it was silently downgraded
        (e.g. by another session like the Saxo web platform).
        """
        while True:
            await asyncio.sleep(SESSION_POLL_INTERVAL)
            try:
                resp = await self.get("/root/v1/sessions/capabilities")
                if resp.status_code != 200:
                    print(
                        f"[SESSION] Poll failed ({resp.status_code}): "
                        f"{resp.text[:200]}"
                    )
                    continue

                data = resp.json()
                trade_level = data.get("TradeLevel", "Unknown")
                if trade_level != "FullTradingAndChat":
                    print(
                        f"[SESSION] TradeLevel is '{trade_level}' "
                        f"— re-upgrading …"
                    )
                    await self.upgrade_session()
                else:
                    print("[SESSION] TradeLevel OK: FullTradingAndChat")
            except Exception as exc:
                print(f"[ERROR] Session poll error: {exc}")
