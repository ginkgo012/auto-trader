"""
Market data API helpers.

Endpoints:
  GET /ref/v1/instruments                          → search by keyword
  GET /trade/v1/infoprices                         → live/delayed quote
  GET /ref/v1/instruments/contractoptionspaces      → option chain
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.saxo_client import SaxoClient


# ── Instrument search ───────────────────────────────────────────────────


async def search_instrument(
    client: SaxoClient,
    keyword: str,
    asset_types: str = "Stock",
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Search instruments by keyword.

    asset_types: comma-separated, e.g. "Stock", "StockOption", "Etf"
    Returns a list of instrument dicts with Uic, Description, Symbol, etc.
    """
    params = {
        "Keywords": keyword,
        "AssetTypes": asset_types,
        "$top": str(limit),
    }
    resp = await client.get("/ref/v1/instruments", params=params)
    resp.raise_for_status()
    data = resp.json()
    instruments = data.get("Data", [])

    print(f"[INFO] Instrument search '{keyword}' ({asset_types}): "
          f"{len(instruments)} results")
    for inst in instruments:
        print(
            f"       UIC={inst.get('Identifier', '?'):>8} | "
            f"{inst.get('Symbol', '?'):<10} | "
            f"{inst.get('Description', '?'):<40} | "
            f"{inst.get('AssetType', '?')}"
        )
    return instruments


# ── Live / delayed quotes ───────────────────────────────────────────────


async def get_quote(
    client: SaxoClient,
    uic: int,
    asset_type: str = "Stock",
) -> dict[str, Any]:
    """
    Fetch a snapshot quote (info-price) for a single UIC.

    Returns the full InfoPrice response including Quote sub-object.
    """
    params = {
        "Uic": str(uic),
        "AssetType": asset_type,
        "FieldGroups": "DisplayAndFormat,InstrumentPriceDetails,Quote",
    }
    resp = await client.get("/trade/v1/infoprices", params=params)
    resp.raise_for_status()
    data = resp.json()

    quote = data.get("Quote", {})
    disp = data.get("DisplayAndFormat", {})
    desc = disp.get("Description", "?")
    bid = quote.get("Bid", "N/A")
    ask = quote.get("Ask", "N/A")
    mid = quote.get("Mid", "N/A")
    delay = quote.get("DelayedByMinutes", 0)

    tag = "[PRICE]" if delay == 0 else f"[PRICE][DELAYED {delay}m]"
    print(f"{tag} {desc} — Bid: {bid} | Ask: {ask} | Mid: {mid}")
    return data


# ── Option chain ────────────────────────────────────────────────────────


async def get_option_chain(
    client: SaxoClient,
    option_root_id: int,
    *,
    expiry_dates: str | None = None,
) -> dict[str, Any]:
    """
    Fetch the option chain for a given OptionRootId.

    option_root_id: obtained from a prior instrument search with
                    AssetType = "StockOption"
    expiry_dates:   optional comma-separated ISO dates, e.g. "2026-04-17"
    """
    params: dict[str, str] = {"OptionRootId": str(option_root_id)}
    if expiry_dates:
        params["ExpiryDates"] = expiry_dates

    resp = await client.get(
        "/ref/v1/instruments/contractoptionspaces",
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    option_space = data.get("OptionSpace", [])
    print(f"[INFO] Option chain for root {option_root_id}: "
          f"{len(option_space)} expiry groups")
    for group in option_space:
        expiry = group.get("Expiry", {}).get("ExpiryDate", "?")
        strikes = group.get("SpecificOptions", [])
        print(f"       Expiry {expiry}: {len(strikes)} strikes")
    return data
