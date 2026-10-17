# HW1 Part A: the support tools

**Summary:** the five unfinished tools in `agent/tools.py` are implemented and pass their tests. One extra tool, `get_product`, was added after a Part B conversation showed the agent couldn't say what a customer had bought.

## The five required tools

Every tool follows the same pattern as the supplied ones. Permissions are checked in code, never left to the model. Each tool returns `{"ok": true, ...}` on success or `{"ok": false, "error": ..., "reason": ...}` on failure.

| Tool | What it does | Key decisions |
|---|---|---|
| `get_policy` | Returns the full text of one policy by its id | Exact, case-sensitive match. `CW-Returns` → `not_found`. |
| `search_products` | Searches the catalog by keyword, optional store and price limit | Every word of the query must appear in the title or description, ignoring case. Plurals are not handled: "mugs" finds nothing, "mug" finds 11. Cheapest first; the limit is clamped to 1–25. |
| `list_my_orders` | Lists the caller's 20 most recent orders | Takes no user or store argument. The scope comes from the logged-in session, so the model can't ask for someone else's orders. Support staff get an error and must use `get_order`. |
| `cancel_order` | Cancels an order that hasn't shipped | **Permission is checked before status.** A caller who doesn't own the order gets `permission_denied`, not "already delivered", so nothing about the order leaks. Only `placed` orders can be cancelled. |
| `find_order` | Finds orders by product name ("the vase") | Fuzzy match of the whole query against product titles (score ≥ 80, `rapidfuzz`). It searches all of the caller's orders, not just the latest 20, and returns the 5 newest matches. Tolerates typos ("speakr"), but full sentences ("the vase I bought") find nothing. |

## The additional tool: `get_product`

**Why:** order records contain only a `product_id`. Asked "What did I buy in order 4127?", the agent could only say "1 item" (Part B, record 8).

**What:** `get_product(product_id)` returns the product's title, description, category, store and current price. The catalog is public, so it needs no permission check and is available to all three roles. The agent calls `get_order` first, then `get_product`.

**Result:** the same question now gets "a **Heavy-Duty Vase** from Blue Heron Ceramics" (record 11).

## Tests

| Check | Result |
|---|---|
| HW1 contract tests: `uv run pytest --runxfail tests/test_hw_holes.py -k hw1` | 8 of 8 pass |
| Supplied regression tests (tools, permissions, refund rules) | 24 of 24 pass |
| New tests for `get_product` (`tests/test_get_product.py`) | 8 of 8 pass |

All checks run offline, without a model.

## Known limitations

- `search_products` and `find_order` are strict by design: plurals and full sentences can return nothing. This is where the model has to rephrase.
- `get_product` shows the *current* catalog price, which can differ from what the order cost ($298 vs $84 for order 4127).
- `rapidfuzz` was added as a direct dependency in `pyproject.toml`.
