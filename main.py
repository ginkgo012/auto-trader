"""
Entry point — SEMI-auto mode with async terminal menu.

Initialization order:
  1. os.makedirs(".tokens", exist_ok=True)
  2. client = SaxoClient()  →  OAuth (reuse / refresh / re-auth)
  3. await client.upgrade_session()
  4. asyncio.create_task(client.token_refresh_loop())
  5. asyncio.create_task(client.poll_session_capability())
  6. Enter main menu loop
"""

import asyncio
import os
import sys

# Ensure the package root is on sys.path so imports resolve
sys.path.insert(0, os.path.dirname(__file__))

from aioconsole import ainput

from config import ENV, MODE, COMMISSION_ROUND_TRIP
from client.saxo_client import SaxoClient
from api.account import get_me, get_balance, get_positions
from api.market_data import search_instrument, get_quote, get_option_chain, list_strikes
from api.orders import place_order, get_open_orders, cancel_order

BANNER = f"""
========================================
 SAXO BOT — {ENV} Environment
========================================
[1] Account balance & positions
[2] Get live quote (search → quote)
[3] Manual order entry
[4] View open orders / cancel an order
[5] Refresh Session Capability now
[0] Exit
========================================"""

ASSET_TYPE_MENU = """
  ── Asset Type ──────────────────
  [1] Stock
  [2] Etf
  [3] StockOption
  [4] StockIndexOption
  [b] Back
  ────────────────────────────────"""

_ASSET_TYPE_CHOICES = {
    "1": "Stock",
    "2": "Etf",
    "3": "StockOption",
    "4": "StockIndexOption",
}


async def _pick_asset_type() -> str | None:
    """Show numbered menu and return asset type, or None to go back."""
    print(ASSET_TYPE_MENU)
    choice = (await ainput("  Select asset type [1-4] (default=1, 'b' to go back): ")).strip()
    if choice.lower() == "b":
        return None
    at = _ASSET_TYPE_CHOICES.get(choice or "1")
    if at is None:
        print("[ERROR] Invalid choice. Pick 1-4.")
        return None
    return at


# ── Menu handlers ───────────────────────────────────────────────────────


async def _handle_balance(client: SaxoClient, ctx: dict) -> None:
    await get_balance(client)
    await get_positions(client)


async def _pick_option_uic(client: SaxoClient, root_id: int) -> int | None:
    """
    Drill into an option chain: pick expiry → pick strike → return the UIC.
    Returns None if the user cancels at any step.
    """
    chain = await get_option_chain(client, root_id)
    option_space = chain.get("OptionSpace", [])
    if not option_space:
        print("[INFO] No option chain data available for this root.")
        return None

    exp_input = (await ainput("  Select expiry index (or 'b' to go back): ")).strip()
    if exp_input.lower() == "b" or not exp_input:
        return None
    try:
        exp_idx = int(exp_input)
    except ValueError:
        print("[ERROR] Must be a number.")
        return None

    strikes = await list_strikes(option_space, exp_idx)
    if not strikes:
        return None

    uic_input = (await ainput("  Enter the Call or Put UIC (or 'b' to go back): ")).strip()
    if uic_input.lower() == "b" or not uic_input:
        return None
    try:
        return int(uic_input)
    except ValueError:
        print("[ERROR] UIC must be a number.")
        return None


async def _handle_quote(client: SaxoClient, ctx: dict) -> None:
    keyword = (await ainput("  Ticker / keyword to search (or 'b' to go back): ")).strip()
    if keyword.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    if not keyword:
        return

    asset_type = await _pick_asset_type()
    if asset_type is None:
        return

    instruments = await search_instrument(client, keyword, asset_type)
    if not instruments:
        print("[INFO] No instruments found.")
        return

    is_option = asset_type in ("StockOption", "StockIndexOption")

    if is_option:
        # For options, the Identifier is an OptionRootId — drill into chain
        root_input = (
            await ainput("  Enter OptionRootId to browse chain (or 'b' to go back): ")
        ).strip()
        if root_input.lower() == "b" or not root_input:
            return
        try:
            root_id = int(root_input)
        except ValueError:
            print("[ERROR] Must be a number.")
            return
        uic = await _pick_option_uic(client, root_id)
        if uic is None:
            return
    else:
        uic_input = (
            await ainput("  Enter UIC to quote (blank to skip, 'b' to go back): ")
        ).strip()
        if uic_input.lower() == "b":
            print("[INFO] Going back to main menu.")
            return
        if not uic_input:
            return
        try:
            uic = int(uic_input)
        except ValueError:
            print("[ERROR] UIC must be a number.")
            return

    await get_quote(client, uic, asset_type)


async def _handle_order(client: SaxoClient, ctx: dict) -> None:
    account_key = ctx.get("account_key", "")
    if not account_key:
        print("[ERROR] AccountKey not available. Ensure get_me succeeded.")
        return

    # 1. Search for instrument
    keyword = (await ainput("  Ticker / keyword (or 'b' to go back): ")).strip()
    if keyword.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    if not keyword:
        return

    asset_type = await _pick_asset_type()
    if asset_type is None:
        return

    instruments = await search_instrument(client, keyword, asset_type)
    if not instruments:
        print("[INFO] No instruments found.")
        return

    is_option = asset_type in ("StockOption", "StockIndexOption")

    if is_option:
        root_input = (
            await ainput("  Enter OptionRootId to browse chain (or 'b' to go back): ")
        ).strip()
        if root_input.lower() == "b" or not root_input:
            return
        try:
            root_id = int(root_input)
        except ValueError:
            print("[ERROR] Must be a number.")
            return
        uic = await _pick_option_uic(client, root_id)
        if uic is None:
            return
    else:
        uic_input = (await ainput("  Enter UIC for order (or 'b' to go back): ")).strip()
        if uic_input.lower() == "b":
            print("[INFO] Going back to main menu.")
            return
        if not uic_input:
            return
        try:
            uic = int(uic_input)
        except ValueError:
            print("[ERROR] UIC must be a number.")
            return

    # 2. Get a quote so the user knows current price
    quote_data = await get_quote(client, uic, asset_type)
    ask = quote_data.get("Quote", {}).get("Ask")
    desc = quote_data.get("DisplayAndFormat", {}).get("Description", "?")

    # 3. Order parameters
    direction_input = (
        await ainput("  Buy or Sell [Buy/Sell] (default=Buy, 'b' to go back): ")
    ).strip()
    if direction_input.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    direction = direction_input or "Buy"
    if direction not in ("Buy", "Sell"):
        print("[ERROR] Must be 'Buy' or 'Sell'.")
        return

    amount_str = (await ainput("  Quantity (default=1, 'b' to go back): ")).strip()
    if amount_str.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    amount_str = amount_str or "1"
    try:
        amount = float(amount_str)
    except ValueError:
        print("[ERROR] Quantity must be a number.")
        return

    order_type_input = (
        await ainput("  OrderType [Market/Limit] (default=Market, 'b' to go back): ")
    ).strip()
    if order_type_input.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    order_type = order_type_input or "Market"

    limit_price = None
    if order_type == "Limit":
        lp_str = (await ainput("  Limit price (or 'b' to go back): ")).strip()
        if lp_str.lower() == "b":
            print("[INFO] Going back to main menu.")
            return
        try:
            limit_price = float(lp_str)
        except ValueError:
            print("[ERROR] Limit price must be a number.")
            return

    # 4. Estimate premium
    est_price = limit_price if limit_price else (ask if ask else 0)
    est_premium = est_price * amount if est_price else 0
    total_cost = est_premium + COMMISSION_ROUND_TRIP

    # 5. Confirm in SEMI mode
    print("\n  ── Order Summary ──────────────────")
    print(f"  Instrument : {desc}")
    print(f"  Direction  : {direction}")
    print(f"  Quantity   : {amount}")
    print(f"  Type       : {order_type}")
    if limit_price:
        print(f"  Limit Price: ${limit_price:.2f}")
    if ask:
        print(f"  Ask Price  : ${ask:.4f}")
    print(f"  Est. Premium: ${est_premium:.2f}")
    print(f"  Commission : ${COMMISSION_ROUND_TRIP:.2f}")
    print(f"  Total Cost : ${total_cost:.2f}")
    print("  ───────────────────────────────────")

    if MODE == "SEMI":
        confirm = (
            await ainput("  Place this order? (y/N, 'b' to go back): ")
        ).strip().lower()
        if confirm == "b":
            print("[INFO] Going back to main menu.")
            return
        if confirm != "y":
            print("[ORDER] Cancelled by user.")
            return

    try:
        await place_order(
            client,
            account_key=account_key,
            uic=uic,
            asset_type=asset_type,
            buy_sell=direction,
            order_type=order_type,
            amount=amount,
            limit_price=limit_price,
            estimated_premium=est_premium,
            description=desc,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")


async def _handle_open_orders(client: SaxoClient, ctx: dict) -> None:
    account_key = ctx.get("account_key", "")
    orders = await get_open_orders(client, account_key=account_key or None)
    if not orders:
        return

    oid = (
        await ainput(
            "  Enter OrderId to cancel (blank to skip, 'b' to go back): "
        )
    ).strip()
    if oid.lower() == "b":
        print("[INFO] Going back to main menu.")
        return
    if not oid:
        return

    if not account_key:
        print("[ERROR] AccountKey not available.")
        return

    confirm = (
        await ainput(f"  Cancel order {oid}? (y/N, 'b' to go back): ")
    ).strip().lower()
    if confirm == "b":
        print("[INFO] Going back to main menu.")
        return
    if confirm == "y":
        await cancel_order(client, oid, account_key)
    else:
        print("[ORDER] Cancel aborted.")


async def _handle_session_refresh(client: SaxoClient, ctx: dict) -> None:
    await client.upgrade_session()


# ── Main loop ───────────────────────────────────────────────────────────

MENU_DISPATCH = {
    "1": _handle_balance,
    "2": _handle_quote,
    "3": _handle_order,
    "4": _handle_open_orders,
    "5": _handle_session_refresh,
}


async def main() -> None:
    # 1. Ensure token directory
    os.makedirs(".tokens", exist_ok=True)

    # 2. Initialise client (OAuth)
    client = SaxoClient()
    await client.init()

    # 3. Upgrade session to FullTradingAndChat
    await client.upgrade_session()

    # 4–5. Background tasks
    refresh_task = asyncio.create_task(client.token_refresh_loop())
    poll_task = asyncio.create_task(client.poll_session_capability())

    # Fetch user info for AccountKey
    ctx: dict = {}
    try:
        user = await get_me(client)
        ctx["client_key"] = user.get("ClientKey", "")
        # Derive default AccountKey from user
        accounts = user.get("LegalAssetTypes") or []  # just for info
        # Get default account key from /port/v1/accounts/me
        acct_resp = await client.get("/port/v1/accounts/me")
        if acct_resp.status_code == 200:
            acct_data = acct_resp.json()
            acct_list = acct_data.get("Data", [])
            if acct_list:
                ctx["account_key"] = acct_list[0].get("AccountKey", "")
                print(f"[INFO] Default AccountKey: {ctx['account_key']}")
    except Exception as exc:
        print(f"[ERROR] Failed to fetch user info: {exc}")

    # 6. Menu loop
    print(BANNER)
    try:
        while True:
            choice = (await ainput("\nSelect [0-5]: ")).strip()
            if choice == "0":
                print("[INFO] Shutting down …")
                break
            handler = MENU_DISPATCH.get(choice)
            if handler:
                try:
                    await handler(client, ctx)
                except Exception as exc:
                    print(f"[ERROR] {exc}")
            else:
                print("[ERROR] Invalid choice. Enter 0–5.")
    finally:
        refresh_task.cancel()
        poll_task.cancel()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
