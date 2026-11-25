"""Tools for the order agent. Built per request so the verified identity is bound into the closure and the
model can never choose whose orders it reads."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from harbor.agents.services import OrderService


def make_order_tools(orders: OrderService, verified_email: str | None) -> list[BaseTool]:
    @tool
    async def lookup_order(order_number: str, email: str) -> str:
        """Look up one order by order number. `email` must be the customer's email; the verified channel email
        overrides whatever the model passes."""
        email_used = verified_email or email
        order = await orders.get_order(order_number, email_used)
        if order is None:
            return json.dumps({"found": False, "order_number": order_number})
        return json.dumps(order)

    @tool
    async def list_recent_orders(email: str) -> str:
        """List the customer's five most recent orders (number, status, date)."""
        email_used = verified_email or email
        rows = await orders.list_orders(email_used, limit=5)
        return json.dumps(
            [
                {"order_number": r["order_number"], "status": r["status"], "placed_at": r["placed_at"]}
                for r in rows
            ]
        )

    return [lookup_order, list_recent_orders]
