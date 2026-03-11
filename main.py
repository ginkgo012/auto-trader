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

# Use the OS native trust store (macOS Keychain / Windows CertStore) so that
# corporate TLS inspection proxies (e.g. Zscaler) are trusted automatically.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore optional; falls back to default bundle

# aioconsole sets stdout to O_NONBLOCK via asyncio's write-pipe transport.
# Any plain print() can then race with a full buffer and raise BlockingIOError
# (errno 35 / EAGAIN) which surfaces as "[ERROR] [Errno 35] write could not
# complete without blocking".  Patch builtins.print globally to retry on EAGAIN
# so every file in the project is protected without modification.
import builtins as _builtins
import errno as _errno
import time as _time

_real_print = _builtins.print

def _safe_print(*args, sep=" ", end="\n", file=None, flush=False):
    for _ in range(50):          # up to ~500 ms total
        try:
            _real_print(*args, sep=sep, end=end, file=file, flush=flush)
            return
        except BlockingIOError as _e:
            if _e.errno == _errno.EAGAIN:
                _time.sleep(0.01)   # give the I/O buffer time to drain
            else:
                raise

_builtins.print = _safe_print

from aioconsole import ainput

from config import ENV, MODE, COMMISSION_ROUND_TRIP, MAX_PREMIUM_PER_ORDER
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
    while True:
        print(ASSET_TYPE_MENU)
        choice = (await ainput("  Select asset type [1-4] (default=1, 'b' to go back): ")).strip()
        if choice.lower() == "b":
            return None
        at = _ASSET_TYPE_CHOICES.get(choice or "1")
        if at is not None:
            return at
        print("[HINT] Invalid choice — enter a number 1-4, or 'b' to go back.")


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

    # Pick expiry
    while True:
        exp_input = (await ainput("  Select expiry index (or 'b' to go back): ")).strip()
        if exp_input.lower() == "b":
            return None
        try:
            exp_idx = int(exp_input)
            if 0 <= exp_idx < len(option_space):
                break
            print(f"[HINT] Index out of range — enter 0-{len(option_space)-1}.")
        except ValueError:
            print("[HINT] Enter a number for the expiry index, or 'b' to go back.")

    strikes = await list_strikes(option_space, exp_idx)
    if not strikes:
        return None

    # Pick UIC
    while True:
        uic_input = (await ainput("  Enter the Call or Put UIC (or 'b' to go back): ")).strip()
        if uic_input.lower() == "b":
            return None
        try:
            return int(uic_input)
        except ValueError:
            print("[HINT] UIC must be a number — try again, or 'b' to go back.")


async def _handle_quote(client: SaxoClient, ctx: dict) -> None:
    keyword = (await ainput("  Ticker / keyword to search (or 'b' to go back): ")).strip()
    if keyword.lower() == "b" or not keyword:
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
        while True:
            root_input = (
                await ainput("  Enter OptionRootId to browse chain (or 'b' to go back): ")
            ).strip()
            if root_input.lower() == "b":
                return
            try:
                root_id = int(root_input)
                break
            except ValueError:
                print("[HINT] Must be a number — try again, or 'b' to go back.")
        uic = await _pick_option_uic(client, root_id)
        if uic is None:
            return
    else:
        while True:
            uic_input = (
                await ainput("  Enter UIC to quote (or 'b' to go back): ")
            ).strip()
            if uic_input.lower() == "b":
                return
            try:
                uic = int(uic_input)
                break
            except ValueError:
                print("[HINT] UIC must be a number — try again, or 'b' to go back.")

    await get_quote(client, uic, asset_type)


async def _handle_order(client: SaxoClient, ctx: dict) -> None:
    account_key = ctx.get("account_key", "")
    if not account_key:
        print("[ERROR] AccountKey not available. Ensure get_me succeeded.")
        return

    # 1. Search for instrument
    keyword = (await ainput("  Ticker / keyword (or 'b' to go back): ")).strip()
    if keyword.lower() == "b" or not keyword:
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
        while True:
            root_input = (
                await ainput("  Enter OptionRootId to browse chain (or 'b' to go back): ")
            ).strip()
            if root_input.lower() == "b":
                return
            try:
                root_id = int(root_input)
                break
            except ValueError:
                print("[HINT] Must be a number — try again, or 'b' to go back.")
        uic = await _pick_option_uic(client, root_id)
        if uic is None:
            return
    else:
        while True:
            uic_input = (await ainput("  Enter UIC for order (or 'b' to go back): ")).strip()
            if uic_input.lower() == "b":
                return
            try:
                uic = int(uic_input)
                break
            except ValueError:
                print("[HINT] UIC must be a number — try again, or 'b' to go back.")

    # 2. Get a quote so the user knows current price
    quote_data = await get_quote(client, uic, asset_type)
    ask = quote_data.get("Quote", {}).get("Ask")
    desc = quote_data.get("DisplayAndFormat", {}).get("Description", "?")

    # 3. Order parameters — Direction
    while True:
        print("\n  ── Direction ──")
        print("  [1] Buy")
        print("  [2] Sell")
        print("  [b] Back")
        dir_input = (await ainput("  Select direction [1-2] (default=1 Buy): ")).strip()
        if dir_input.lower() == "b":
            return
        _DIR_MAP = {"1": "Buy", "2": "Sell", "": "Buy"}
        direction = _DIR_MAP.get(dir_input)
        if direction is not None:
            break
        print("[HINT] Invalid choice — enter 1 (Buy) or 2 (Sell), or 'b' to go back.")

    # Quantity
    while True:
        amount_str = (await ainput("  Quantity (default=1, 'b' to go back): ")).strip()
        if amount_str.lower() == "b":
            return
        amount_str = amount_str or "1"
        try:
            amount = float(amount_str)
            break
        except ValueError:
            print("[HINT] Quantity must be a number — try again, or 'b' to go back.")

    # Order type
    while True:
        print("\n  ── Order Type ──")
        print("  [1] Market")
        print("  [2] Limit")
        print("  [b] Back")
        ot_input = (await ainput("  Select order type [1-2] (default=1 Market): ")).strip()
        if ot_input.lower() == "b":
            return
        _OT_MAP = {"1": "Market", "2": "Limit", "": "Market"}
        order_type = _OT_MAP.get(ot_input)
        if order_type is not None:
            break
        print("[HINT] Invalid choice — enter 1 (Market) or 2 (Limit), or 'b' to go back.")

    limit_price = None
    if order_type == "Limit":
        while True:
            lp_str = (await ainput("  Limit price (or 'b' to go back): ")).strip()
            if lp_str.lower() == "b":
                return
            try:
                limit_price = float(lp_str)
                break
            except ValueError:
                print("[HINT] Limit price must be a number — try again, or 'b' to go back.")

    # ToOpenClose for options
    to_open_close = "ToOpen"
    if is_option:
        while True:
            print("\n  ── Open / Close ──")
            print("  [1] ToOpen  (open new position)")
            print("  [2] ToClose (close existing position)")
            print("  [b] Back")
            oc_input = (await ainput("  Select [1-2] (default=1 ToOpen): ")).strip()
            if oc_input.lower() == "b":
                return
            _OC_MAP = {"1": "ToOpen", "2": "ToClose", "": "ToOpen"}
            to_open_close = _OC_MAP.get(oc_input)
            if to_open_close is not None:
                break
            print("[HINT] Invalid choice — enter 1 (ToOpen) or 2 (ToClose), or 'b' to go back.")

    # 4. Estimate premium
    est_price = limit_price if limit_price else (ask if ask else 0)
    est_premium = est_price * amount if est_price else 0
    total_cost = est_premium + COMMISSION_ROUND_TRIP

    # 5. Order summary
    exceeds_cap = est_premium > MAX_PREMIUM_PER_ORDER
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
    if exceeds_cap:
        print(f"  ⚠ WARNING: Premium ${est_premium:.2f} EXCEEDS "
              f"${MAX_PREMIUM_PER_ORDER:.2f} cap!")
    print("  ───────────────────────────────────")

    # 6. Confirm
    if MODE == "SEMI":
        print("\n  [1] Confirm — place order")
        print("  [2] Cancel")
        print("  [b] Back")
        confirm = (await ainput("  Confirm? [1-2] (default=2 Cancel): ")).strip()
        if confirm != "1":
            print("[ORDER] Cancelled by user.")
            return

        # Double-confirm if premium exceeds cap
        if exceeds_cap:
            print(f"\n  ⚠ PREMIUM ${est_premium:.2f} EXCEEDS "
                  f"${MAX_PREMIUM_PER_ORDER:.2f} CAP ⚠")
            print("  [1] Yes, I accept the risk — place anyway")
            print("  [2] No, cancel")
            risk_confirm = (await ainput(
                "  Double-confirm [1-2] (default=2 Cancel): "
            )).strip()
            if risk_confirm != "1":
                print("[ORDER] Cancelled — premium exceeds cap.")
                return

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
        to_open_close=to_open_close,
    )


async def _handle_open_orders(client: SaxoClient, ctx: dict) -> None:
    account_key = ctx.get("account_key", "")
    orders = await get_open_orders(client, account_key=account_key or None)
    if not orders:
        return

    while True:
        oid = (
            await ainput(
                "  Enter OrderId to cancel (blank to skip, 'b' to go back): "
            )
        ).strip()
        if oid.lower() == "b" or not oid:
            return
        if oid.isdigit():
            break
        print("[HINT] OrderId should be a number — try again, or 'b' to go back.")

    if not account_key:
        print("[ERROR] AccountKey not available.")
        return

    print(f"\n  Cancel order {oid}?")
    print("  [1] Yes — cancel it")
    print("  [2] No")
    print("  [b] Back")
    confirm = (await ainput("  Confirm cancel [1-2] (default=2 No): ")).strip()
    if confirm == "1":
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
                print(BANNER)
            else:
                print("[HINT] Invalid choice — enter a number 0-5.")
                print(BANNER)
    finally:
        refresh_task.cancel()
        poll_task.cancel()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
