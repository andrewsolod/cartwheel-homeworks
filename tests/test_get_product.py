"""HW1 additional tool: get_product, tested through its logic function
(no LLM, no API keys)."""

from __future__ import annotations

import pytest

from agent import tools
from agent.agent import TOOLS_BY_ROLE
from agent.auth import AuthContext

SHOPPER_1 = AuthContext(user_id=1, role="shopper")
MERCHANT_STORE_2 = AuthContext(user_id=9002, role="merchant", store_id=2)
SUPPORT = AuthContext(user_id=9501, role="support")


def test_get_product_names_the_product_in_order_4127(world: dict) -> None:
    with tools.db.connection() as conn:
        order = tools.db.get_order(conn, 4127)
    result = tools.get_product(SHOPPER_1, order.product_id)
    assert result["ok"] is True
    product = result["product"]
    assert product["product_id"] == order.product_id
    assert product["title"] == "Heavy-Duty Vase"
    assert product["store_id"] == 1
    assert product["store_name"] == "Blue Heron Ceramics"
    assert set(product) == {
        "product_id", "store_id", "store_name", "title", "description", "category", "price_usd",
    }


@pytest.mark.parametrize("ctx", [SHOPPER_1, MERCHANT_STORE_2, SUPPORT])
def test_get_product_is_public_catalog_data_for_every_role(world: dict, ctx: AuthContext) -> None:
    # Product 1 belongs to store 1; a store-2 merchant may still read the public catalog.
    assert tools.get_product(ctx, 1)["ok"] is True


def test_get_product_unknown_id_is_not_found(world: dict) -> None:
    result = tools.get_product(SHOPPER_1, 999_999)
    assert result["ok"] is False
    assert result["error"] == "not_found"
    assert "999999" in result["reason"]


@pytest.mark.parametrize("role", ["shopper", "merchant", "support"])
def test_get_product_is_registered_for_every_role(role: str) -> None:
    assert "get_product" in [tool.name for tool in TOOLS_BY_ROLE[role]]
