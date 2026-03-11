"""
Market data API helpers.

Endpoints:
  GET /ref/v1/instruments                          → search by keyword
  GET /trade/v1/infoprices                         → live/delayed quote
  GET /ref/v1/instruments/contractoptionspaces      → option chain
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.saxo_client import SaxoClient


# ── Asset type normalization ─────────────────────────────────────────────

_ASSET_TYPE_MAP = {
    "stock": "Stock",
    "etf": "Etf",
    "stockoption": "StockOption",
    "stockindexoption": "StockIndexOption",
    "cfdonstock": "CfdOnStock",
    "fxspot": "FxSpot",
}


def normalize_asset_type(raw: str) -> str:
    """Case-insensitive normalization of asset type input."""
    return _ASSET_TYPE_MAP.get(raw.strip().lower(), raw.strip())


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

    is_option = asset_types in ("StockOption", "StockIndexOption")
    id_label = "RootId" if is_option else "UIC"
    print(f"[INFO] Instrument search '{keyword}' ({asset_types}): "
          f"{len(instruments)} results")
    for inst in instruments:
        print(
            f"       {id_label}={inst.get('Identifier', '?'):>8} | "
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
                    AssetType = "StockOption" or "StockIndexOption"
    expiry_dates:   optional comma-separated ISO dates, e.g. "2026-04-17"
    """
    params: dict[str, str] = {}
    if expiry_dates:
        params["ExpiryDates"] = expiry_dates
        params["OptionSpaceSegment"] = "SpecificDates"
    else:
        params["OptionSpaceSegment"] = "AllDates"

    resp = await client.get(
        f"/ref/v1/instruments/contractoptionspaces/{option_root_id}",
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    option_space = data.get("OptionSpace", [])
    print(f"[INFO] Option chain for root {option_root_id}: "
          f"{len(option_space)} expiry groups")
    for i, group in enumerate(option_space):
        expiry = group.get("Expiry", group.get("DisplayExpiry", "?"))
        strikes = group.get("SpecificOptions", [])
        tag = f"{len(strikes)} strikes" if strikes else "no strikes loaded"
        print(f"  [{i}] Expiry {expiry} — {tag}")
    return data


async def list_strikes(
    option_space: list[dict],
    expiry_index: int,
    page_size: int = 20,
) -> list[dict]:
    """
    Print and return the strikes for a given expiry index, paginated.
    Pagination prevents errno 35 (EAGAIN) caused by aioconsole's
    non-blocking stdout being overwhelmed by large option chain output.
    Each entry has PutCall, StrikePrice, and Uic.
    Groups by StrikePrice to show Call/Put side by side.
    """
    if expiry_index < 0 or expiry_index >= len(option_space):
        print("[ERROR] Invalid expiry index.")
        return []

    group = option_space[expiry_index]
    expiry = group.get("Expiry", group.get("DisplayExpiry", "?"))
    options = group.get("SpecificOptions", [])
    if not options:
        print(f"[INFO] No strikes loaded for expiry {expiry}.")
        return []

    # Group by strike price: each strike may have a Call and/or Put entry
    strikes_map: dict[float, dict[str, int | None]] = {}
    for opt in options:
        sp = opt.get("StrikePrice", 0)
        pc = opt.get("PutCall", "")
        uic = opt.get("Uic")
        if sp not in strikes_map:
            strikes_map[sp] = {"Call": None, "Put": None}
        if pc in ("Call", "Put"):
            strikes_map[sp][pc] = uic

    sorted_strikes = sorted(strikes_map.items())
    total = len(sorted_strikes)
    print(f"[INFO] Strikes for expiry {expiry}: {total} (showing {page_size} per page)")

    # Paginate — yield to event loop between pages to avoid EAGAIN
    for start in range(0, total, page_size):
        page = sorted_strikes[start : start + page_size]
        for sp, sides in page:
            call_uic = sides["Call"]
            put_uic = sides["Put"]
            print(
                f"       Strike {sp:>10} | "
                f"Call UIC={str(call_uic or '-'):>8} | "
                f"Put  UIC={str(put_uic or '-'):>8}"
            )
        await asyncio.sleep(0)  # yield to event loop to drain stdout buffer
        end = min(start + page_size, total)
        if end < total:
            try:
                from aioconsole import ainput  # local import to avoid hard dep
                cont = (await ainput(f"  -- {end}/{total} shown. Enter to continue, 's' to stop: ")).strip()
                if cont.lower() == "s":
                    break
            except ImportError:
                await asyncio.sleep(0)

    return options
