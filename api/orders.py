"""
Order management API helpers.

Endpoints:
  POST   /trade/v2/orders                          → place order
  DELETE /trade/v2/orders/{OrderIds}                → cancel order(s)
  GET    /port/v1/orders/me                         → open orders
  POST   /trade/v2/orders/precheck                  → order pre-check
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.saxo_client import SaxoClient

from config import MAX_PREMIUM_PER_ORDER, COMMISSION_ROUND_TRIP


# ── Risk gate ───────────────────────────────────────────────────────────


def _check_premium(estimated_premium: float, description: str = "") -> None:
    """
    Hard risk limit: reject if estimated premium exceeds cap.
    Raises ValueError so the caller can catch and inform the user.
    """
    if estimated_premium > MAX_PREMIUM_PER_ORDER:
        raise ValueError(
            f"[ORDER] REJECTED — Premium ${estimated_premium:.2f} exceeds "
            f"${MAX_PREMIUM_PER_ORDER:.2f} hard limit. ({description})"
        )


# ── Pre-check ──────────────────────────────────────────────────────────


async def precheck_order(
    client: SaxoClient,
    order_body: dict[str, Any],
) -> dict[str, Any]:
    """
    Pre-validate an order before submission.
    Returns the API's precheck response including estimated costs.
    """
    resp = await client.post(
        "/trade/v2/orders/precheck",
        json_body=order_body,
    )
    resp.raise_for_status()
    data = resp.json()
    preview = data.get("PreviewOrder", {})
    est_cost = preview.get("EstimatedOrderValue", "N/A")
    print(f"[ORDER] Pre-check OK — Estimated value: {est_cost}")
    return data


# ── Place order ─────────────────────────────────────────────────────────


async def place_order(
    client: SaxoClient,
    *,
    account_key: str,
    uic: int,
    asset_type: str = "Stock",
    buy_sell: str = "Buy",
    order_type: str = "Market",
    amount: float = 1,
    order_duration_type: str = "DayOrder",
    limit_price: float | None = None,
    estimated_premium: float | None = None,
    description: str = "",
) -> dict[str, Any]:
    """
    Place a single order via POST /trade/v2/orders.

    If estimated_premium is provided, enforces the hard risk cap
    ($200 max + $4 commission).
    """
    total_cost = (estimated_premium or 0) + COMMISSION_ROUND_TRIP
    if estimated_premium is not None:
        _check_premium(estimated_premium, description)

    body: dict[str, Any] = {
        "AccountKey": account_key,
        "Uic": uic,
        "AssetType": asset_type,
        "BuySell": buy_sell,
        "OrderType": order_type,
        "Amount": amount,
        "OrderDuration": {"DurationType": order_duration_type},
        "ManualOrder": True,
    }
    if limit_price is not None and order_type == "Limit":
        body["OrderPrice"] = limit_price

    print(
        f"[ORDER] Placing: {buy_sell} {amount}x UIC={uic} ({asset_type}) "
        f"| Type={order_type}"
    )
    if estimated_premium is not None:
        print(
            f"[ORDER] Est. premium: ${estimated_premium:.2f} + "
            f"${COMMISSION_ROUND_TRIP:.2f} commission = ${total_cost:.2f}"
        )

    resp = await client.post("/trade/v2/orders", json_body=body)
    resp.raise_for_status()
    data = resp.json()
    order_id = data.get("OrderId", "?")
    print(f"[ORDER] Placed — OrderId: {order_id}")
    return data


# ── Open orders ─────────────────────────────────────────────────────────


async def get_open_orders(
    client: SaxoClient,
    *,
    account_key: str | None = None,
    client_key: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all open/working orders."""
    if account_key or client_key:
        params: dict[str, str] = {}
        if account_key:
            params["AccountKey"] = account_key
        if client_key:
            params["ClientKey"] = client_key
        resp = await client.get("/port/v1/orders", params=params)
    else:
        resp = await client.get("/port/v1/orders/me")

    resp.raise_for_status()
    data = resp.json()
    orders = data.get("Data", [])
    print(f"[INFO] Open orders: {len(orders)}")
    for o in orders:
        disp = o.get("DisplayAndFormat", {})
        print(
            f"       OrderId={o.get('OrderId', '?')} | "
            f"{o.get('BuySell', '?')} {o.get('Amount', '?')}x "
            f"{disp.get('Description', '?')} | "
            f"Status={o.get('Status', '?')} | "
            f"Type={o.get('OrderType', '?')}"
        )
    return orders


# ── Cancel order ────────────────────────────────────────────────────────


async def cancel_order(
    client: SaxoClient,
    order_id: str,
    account_key: str,
) -> bool:
    """
    Cancel a single order.
    DELETE /trade/v2/orders/{OrderIds}?AccountKey={AccountKey}
    """
    resp = await client.delete(
        f"/trade/v2/orders/{order_id}",
        params={"AccountKey": account_key},
    )
    if resp.status_code in (200, 204):
        print(f"[ORDER] Cancelled OrderId={order_id}")
        return True
    print(
        f"[ERROR] Cancel failed for OrderId={order_id} "
        f"({resp.status_code}): {resp.text[:200]}"
    )
    return False
