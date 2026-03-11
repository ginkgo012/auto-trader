"""
Account / Portfolio API helpers.

Endpoints:
  GET /port/v1/users/me          → user info
  GET /port/v1/balances/me       → balance for logged-in user
  GET /port/v1/balances          → balance for specific account
  GET /port/v1/positions/me      → open positions for logged-in user
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.saxo_client import SaxoClient


async def get_me(client: SaxoClient) -> dict[str, Any]:
    """Return the authenticated user profile."""
    resp = await client.get("/port/v1/users/me")
    resp.raise_for_status()
    data = resp.json()
    print(f"[INFO] User: {data.get('Name', 'N/A')} | "
          f"ClientKey: {data.get('ClientKey', 'N/A')}")
    return data


async def get_balance(
    client: SaxoClient,
    *,
    account_key: str | None = None,
    client_key: str | None = None,
) -> dict[str, Any]:
    """
    Return balance data.
    If no keys supplied, uses the /me convenience endpoint.
    """
    if account_key or client_key:
        params: dict[str, str] = {}
        if account_key:
            params["AccountKey"] = account_key
        if client_key:
            params["ClientKey"] = client_key
        resp = await client.get("/port/v1/balances", params=params)
    else:
        resp = await client.get("/port/v1/balances/me")

    resp.raise_for_status()
    data = resp.json()

    cash = data.get("CashBalance", "N/A")
    total = data.get("TotalValue", "N/A")
    currency = data.get("Currency", "")
    print(f"[INFO] Balance — Cash: {cash} {currency} | Total: {total} {currency}")
    return data


async def get_positions(
    client: SaxoClient,
    *,
    account_key: str | None = None,
    client_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return open positions (uses /me when no keys given)."""
    if account_key or client_key:
        params: dict[str, str] = {
            "FieldGroups": "DisplayAndFormat,PositionBase,PositionView",
        }
        if account_key:
            params["AccountKey"] = account_key
        if client_key:
            params["ClientKey"] = client_key
        resp = await client.get("/port/v1/positions", params=params)
    else:
        resp = await client.get(
            "/port/v1/positions/me",
            params={"FieldGroups": "DisplayAndFormat,PositionBase,PositionView"},
        )

    resp.raise_for_status()
    data = resp.json()
    positions = data.get("Data", [])
    print(f"[INFO] Open positions: {len(positions)}")
    print(f"[INFO] Open positions: {len(positions)}")
    for pos in positions:
        disp  = pos.get("DisplayAndFormat", {})
        base  = pos.get("PositionBase", {})
        view  = pos.get("PositionView", {})
        desc  = disp.get("Description") or disp.get("Symbol") or "?"
        amt   = base.get("Amount", "?")
        pl    = view.get("ProfitLossOnTrade", base.get("ProfitLossOnTrade", "?"))
        open_price = base.get("OpenPrice", "?")
        currency   = disp.get("Currency", "")
        print(
            f"       {desc} | "
            f"Amount: {amt} | "
            f"Open: {open_price} | "
            f"P/L: {pl} {currency}"
        )
    return positions
