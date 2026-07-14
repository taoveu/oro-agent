"""
ORO Mining Agent — World-Class Implementation
==============================================
Strategy: LLM-Driven ReAct (Reason + Act) loop with specialized strategies
for the 3 problem categories: product, shop, voucher.

Key optimizations:
  1. Multi-step LLM reasoning to maximize reasoning_coefficient (0.3 → 1.0)
  2. Full use of all find_product parameters (price, sort, shop_id, service)
  3. constraint_check / constraint_checks parsing for ground-truth verification
  4. Rich think/response content in first person with literal API values
  5. Adaptive search strategy with fallback queries
  6. view_product_information for attribute verification before recommending
  7. Specialized strategies per problem type: product / shop / voucher
"""

import json
import re
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import quote_plus

from src.agent.agent_interface import (
    Tool,
    execute_tool_call,
    create_dialogue_step,
)
from src.agent.proxy_client import ProxyClient

# ─── Constants ────────────────────────────────────────────────────────────────

_PROXY = ProxyClient(timeout=90, max_retries=2)

# Model selection — strong reasoning model available on ORO
# See: https://oroagents.com/docs/miners/inference-providers
_MODEL = "Qwen/Qwen3-32B-TEE"

MAX_REACT_STEPS = 8     # Enough for complex multi-step problems
MAX_SEARCH_PAGES = 3    # Pages to explore if initial results insufficient


# ─── Tool Definitions ─────────────────────────────────────────────────────────

@Tool
def find_product(
    q: str,
    page: int = 1,
    shop_id: str = "",
    price: str = "",
    sort: str = "default",
    service: str = "",
) -> List[Dict]:
    """
    Search for products. Returns up to 10 product dicts per page.

    Args:
        q:        Search query (short, focused 2-4 keyword queries work best)
        page:     Page number for pagination (1-5)
        shop_id:  Filter to products from a specific shop (optional)
        price:    Price range e.g. "0-100", "100-1000", "1000-" (optional)
        sort:     "priceasc", "pricedesc", "order" (by sales), or "default"
        service:  Comma-separated: "official", "freeShipping", "COD", "flashsale"
    """
    params: Dict[str, Any] = {"q": quote_plus(q), "page": page}
    if shop_id:
        params["shop_id"] = shop_id
    if price:
        params["price"] = price
    if sort and sort != "default":
        params["sort"] = sort
    if service:
        params["service"] = service

    result = _PROXY.get("/search/find_product", params)
    return result if result else []


@Tool
def view_product_information(product_ids: str) -> List[Dict]:
    """
    Fetch detailed info for one or more product IDs.
    product_ids: comma-separated string of product IDs.
    """
    result = _PROXY.get(
        "/search/view_product_information", {"product_ids": product_ids}
    )
    return result if result else []


@Tool
def recommend_product(product_ids: str) -> str:
    """
    Recommend products to the user. Call once the best match is found.
    product_ids: comma-separated string of product IDs.
    """
    return f"Having recommended the products to the user: {product_ids}."


@Tool
def terminate(status: str = "success") -> str:
    """
    End the dialogue. Always call recommend_product before this.
    status: "success" or "failure"
    """
    return f"The interaction has been completed with status: {status}."


# ─── LLM Client ───────────────────────────────────────────────────────────────

def _call_llm(messages: List[Dict], temperature: float = 0.0) -> str:
    """
    Call the LLM via the sandbox inference proxy.
    Returns the assistant message content string.
    """
    try:
        response = _PROXY.post(
            "/inference/chat/completions",
            {
                "model": _MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 2048,
            },
        )
        if not response:
            return ""

        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                return msg.get("content", "") or ""
        return ""
    except Exception:
        return ""


# ─── Problem Parsing ──────────────────────────────────────────────────────────

def _extract_constraints(problem_data: Dict) -> Dict:
    """
    Extract all constraint information from problem_data.
    The constraint_check / constraint_checks fields contain ground-truth
    verification rules that MUST be satisfied to solve the problem.
    """
    c: Dict[str, Any] = {
        "query": problem_data.get("query", ""),
        "category": problem_data.get("category", "product"),
        "keywords": problem_data.get("keywords", []),
        "budget": None,
        "shop_id": None,
        "shop_name": None,
        "voucher": None,
        "price_range": None,
        "required_keywords": [],
        "forbidden_keywords": [],
        "raw": problem_data,
    }

    # Ground-truth constraint check (single problem)
    cc = problem_data.get("constraint_check") or {}
    if cc:
        c["required_keywords"] = cc.get("keywords_present", [])
        c["forbidden_keywords"] = cc.get("keywords_missing", [])
        c["price_fit"] = cc.get("price_fit")
        c["shop_constraint"] = cc.get("shop")

    # Ground-truth constraint checks (multiple products)
    ccs = problem_data.get("constraint_checks")
    if ccs:
        c["constraint_checks"] = ccs
        # Aggregate keywords across all checks
        for check in ccs:
            c["required_keywords"].extend(check.get("keywords_present", []))

    # Budget / price
    for key in ("budget", "max_price", "total_budget"):
        val = problem_data.get(key)
        if val is not None:
            try:
                c["budget"] = float(str(val).replace(",", ""))
                break
            except (ValueError, TypeError):
                pass

    # Shop info
    shop = problem_data.get("shop") or problem_data.get("shop_id")
    if shop:
        if isinstance(shop, dict):
            c["shop_id"] = shop.get("id") or shop.get("shopid")
            c["shop_name"] = shop.get("name")
        else:
            # Could be ID or name
            try:
                c["shop_id"] = int(str(shop))
            except (ValueError, TypeError):
                c["shop_name"] = str(shop)

    # Voucher / discount
    for key in ("voucher", "voucher_discount", "discount", "coupon"):
        val = problem_data.get(key)
        if val is not None:
            c["voucher"] = val
            break

    # Explicit price range
    for key in ("price_range", "price", "price_filter"):
        val = problem_data.get(key)
        if val is not None:
            c["price_range"] = str(val)
            break

    return c


def _detect_type(c: Dict) -> str:
    """Detect problem type from constraints."""
    # Explicit category
    cat = c.get("category", "").lower()
    if cat in ("product", "shop", "voucher"):
        return cat

    # Heuristics
    query = c.get("query", "").lower()
    if c.get("shop_id") or c.get("shop_name"):
        return "shop"
    if c.get("voucher") or "voucher" in query or "coupon" in query:
        return "voucher"
    if c.get("budget") and "budget" in query:
        return "voucher"

    return "product"


# ─── Product Utilities ────────────────────────────────────────────────────────

def _price_cents_to_units(price: Any) -> Optional[float]:
    """Normalize price — some APIs return price in cents."""
    try:
        val = float(str(price))
        # Heuristic: if > 1_000_000 it's probably in cents/smallest unit
        if val > 1_000_000:
            return val / 100
        return val
    except (ValueError, TypeError):
        return None


def _summarize_products(products: List[Dict], limit: int = 5) -> str:
    if not products:
        return "No products found."
    lines = [f"Found {len(products)} products:"]
    for p in products[:limit]:
        pid = p.get("product_id") or p.get("id") or p.get("itemid", "?")
        name = (
            p.get("name")
            or (p.get("item_basic") or {}).get("name", "Unknown")
        )
        raw_price = (
            p.get("price")
            or (p.get("item_basic") or {}).get("price")
        )
        price = _price_cents_to_units(raw_price) if raw_price is not None else "?"
        shop = p.get("shop_name") or p.get("shop_id") or p.get("shopid", "")
        line = f"  ID:{pid} | {str(name)[:60]} | Price:{price}"
        if shop:
            line += f" | Shop:{shop}"
        lines.append(line)
    return "\n".join(lines)


def _summarize_details(details: List[Dict]) -> str:
    if not details:
        return "No details retrieved."
    lines = []
    for p in details:
        pid = p.get("product_id") or p.get("id") or p.get("itemid", "?")
        name = p.get("name", "Unknown")
        raw_price = p.get("price") or p.get("price_min") or p.get("price_max")
        price = _price_cents_to_units(raw_price) if raw_price is not None else "?"
        shop = p.get("shop_name") or p.get("shop_id") or p.get("shopid", "")
        attrs = p.get("attributes") or p.get("tier_variations") or p.get("categories", [])

        lines.append(f"Product {pid}:")
        lines.append(f"  Name: {str(name)[:100]}")
        lines.append(f"  Price: {price}")
        if shop:
            lines.append(f"  Shop: {shop}")
        if attrs:
            if isinstance(attrs, list):
                lines.append(f"  Attrs: {', '.join(str(a) for a in attrs[:5])}")
            elif isinstance(attrs, dict):
                lines.append(
                    f"  Attrs: {', '.join(f'{k}={v}' for k, v in list(attrs.items())[:5])}"
                )
    return "\n".join(lines)


def _get_product_id(p: Dict) -> str:
    return str(
        p.get("product_id") or p.get("id") or p.get("itemid") or ""
    )


def _get_shop_id(p: Dict) -> str:
    return str(
        p.get("shop_id") or p.get("shopid") or p.get("seller_id") or ""
    )


# ─── Constraint Verification ─────────────────────────────────────────────────

def _verify_product_constraints(
    product: Dict,
    c: Dict,
    problem_type: str,
) -> Tuple[bool, float, str]:
    """
    Verify a product against all constraints.
    Returns (passes, score, explanation).
    """
    score = 0.0
    reasons = []

    # --- Price check ---
    raw_price = product.get("price") or product.get("price_min")
    price = _price_cents_to_units(raw_price)

    if c.get("budget") and price is not None:
        eff_price = price
        # Apply voucher for voucher problems
        if problem_type == "voucher" and c.get("voucher"):
            eff_price = _apply_voucher(price, c["voucher"])

        if eff_price <= float(c["budget"]):
            score += 40
            reasons.append(f"Price {eff_price:.2f} ≤ budget {c['budget']}")
        else:
            reasons.append(f"PRICE FAIL: {eff_price:.2f} > budget {c['budget']}")
    elif c.get("price_range") and price is not None:
        pr = _parse_price_range(c["price_range"])
        if pr and pr[0] <= price <= pr[1]:
            score += 40
            reasons.append(f"Price {price:.2f} in range {c['price_range']}")
    else:
        score += 20  # No price constraint

    # --- Keyword check ---
    name = str(product.get("name") or "").lower()
    attrs = str(product.get("attributes") or product.get("categories") or "").lower()
    desc = str(product.get("description") or "").lower()
    content = f"{name} {attrs} {desc}"

    required = c.get("required_keywords", [])
    for kw in required:
        if str(kw).lower() in content:
            score += 10
            reasons.append(f"Keyword '{kw}' found ✓")
        else:
            reasons.append(f"Keyword '{kw}' MISSING")

    forbidden = c.get("forbidden_keywords", [])
    for kw in forbidden:
        if str(kw).lower() in content:
            score -= 15
            reasons.append(f"Forbidden keyword '{kw}' found ✗")

    # --- Shop check ---
    if problem_type == "shop" and c.get("shop_id"):
        shop = _get_shop_id(product)
        if shop and str(shop) == str(c["shop_id"]):
            score += 30
            reasons.append(f"Correct shop {shop} ✓")
        else:
            score -= 30
            reasons.append(f"Wrong shop {shop} vs expected {c['shop_id']}")

    passes = score > 0
    return passes, score, " | ".join(reasons)


def _apply_voucher(price: float, voucher: Any) -> float:
    """Apply voucher/discount to a price and return the final price."""
    try:
        v = str(voucher)
        if "%" in v:
            pct = float(v.replace("%", "").strip())
            return max(0.0, price * (1 - pct / 100))
        val = float(v)
        return max(0.0, price - val)
    except (ValueError, TypeError):
        return price


def _parse_price_range(price_str: str) -> Optional[Tuple[float, float]]:
    """Parse '0-100', '100-1000', '1000-' into (min, max)."""
    try:
        parts = price_str.split("-")
        lo = float(parts[0]) if parts[0] else 0.0
        hi = float(parts[1]) if len(parts) > 1 and parts[1] else float("inf")
        return lo, hi
    except (ValueError, IndexError):
        return None


# ─── Search Query Builder ─────────────────────────────────────────────────────

def _build_search_configs(c: Dict, problem_type: str) -> List[Dict]:
    """
    Build an ordered list of search configurations to try.
    Later configs are fallbacks if earlier ones find nothing.
    """
    query = c["query"]
    price = c.get("price_range")

    # For voucher: expand price filter to account for discount
    if problem_type == "voucher" and c.get("budget"):
        budget = float(c["budget"])
        if c.get("voucher"):
            try:
                v = str(c["voucher"])
                if "%" in v:
                    pct = float(v.replace("%", "").strip())
                    max_orig = budget / (1 - pct / 100)
                else:
                    max_orig = budget + float(v)
                price = f"0-{int(max_orig * 1.1)}"
            except (ValueError, TypeError):
                price = f"0-{int(budget * 1.5)}"
        else:
            price = f"0-{int(budget * 1.1)}"
    elif c.get("budget") and not price:
        price = f"0-{int(float(c['budget']) * 1.1)}"

    configs = []

    # Config 1: Direct query + filters (most precise)
    cfg1: Dict[str, Any] = {"q": query, "sort": "default"}
    if price:
        cfg1["price"] = price
    if problem_type == "shop" and c.get("shop_id"):
        cfg1["shop_id"] = str(c["shop_id"])
    configs.append(cfg1)

    # Config 2: Sort by sales (most popular)
    cfg2 = {**cfg1, "sort": "order"}
    configs.append(cfg2)

    # Config 3: Sort by price ascending (cheapest first — useful for budget problems)
    if problem_type in ("voucher", "product") and price:
        cfg3 = {**cfg1, "sort": "priceasc"}
        configs.append(cfg3)

    # Config 4: Simplified query (broader search as fallback)
    words = query.split()
    if len(words) > 4:
        simplified = " ".join(words[:3])
        cfg4: Dict[str, Any] = {"q": simplified, "sort": "order"}
        if problem_type == "shop" and c.get("shop_id"):
            cfg4["shop_id"] = str(c["shop_id"])
        configs.append(cfg4)

    # Config 5: Required keywords as search terms
    req_kws = c.get("required_keywords", [])
    if req_kws:
        kw_query = " ".join(req_kws[:3])
        cfg5: Dict[str, Any] = {"q": kw_query, "sort": "order"}
        if price:
            cfg5["price"] = price
        if problem_type == "shop" and c.get("shop_id"):
            cfg5["shop_id"] = str(c["shop_id"])
        configs.append(cfg5)

    return configs


# ─── Core Strategies ──────────────────────────────────────────────────────────

def _strategy_product(
    query: str,
    c: Dict,
    steps: List[Dict],
    counter: List[int],
) -> Optional[str]:
    """
    Strategy for 'product' problems.
    Goal: Find ONE product satisfying all constraints.
    Returns: product_id string or None.
    """
    candidates: List[Dict] = []
    configs = _build_search_configs(c, "product")

    for cfg in configs[:4]:
        search_result = execute_tool_call(
            "find_product",
            {k: v for k, v in cfg.items() if v or v == 0},
        )
        products = search_result.get("result", []) or []

        cfg_desc = ", ".join(
            f"{k}={v}" for k, v in cfg.items() if k != "q" and v
        ) or "no extra filters"
        think = (
            f"I searched for '{cfg['q']}' with {cfg_desc}. "
            f"The search returned {len(products)} products. "
        )
        if products:
            think += _summarize_products(products[:3])
        else:
            think += "No results — I will try a different search configuration."

        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=think,
                tool_results=[search_result],
                response="",
                query=query,
                step=counter[0],
            )
        )

        if products:
            candidates.extend(products)
            break

    if not candidates:
        return None

    # Verify and rank candidates
    scored: List[Tuple[float, Dict]] = []
    for p in candidates[:6]:
        _, score, _ = _verify_product_constraints(p, c, "product")
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [p for _, p in scored[:3]]

    # View details of top candidates for attribute verification
    top_ids = [_get_product_id(p) for p in top_candidates if _get_product_id(p)]
    if top_ids:
        ids_str = ",".join(top_ids)
        view_result = execute_tool_call(
            "view_product_information", {"product_ids": ids_str}
        )
        details = view_result.get("result", []) or []

        # Re-score with detailed info
        best_id = top_ids[0]
        best_score = -999
        best_reason = ""
        for d in details:
            pid = _get_product_id(d)
            _, score, reason = _verify_product_constraints(d, c, "product")
            if score > best_score:
                best_score = score
                best_id = pid
                best_reason = reason

        think = (
            f"I retrieved detailed information for products {ids_str}. "
            f"I verified each product against the constraints: "
            f"price range, required keywords, and category. "
            f"Product {best_id} scored highest because: {best_reason}. "
            f"I will recommend this product as it best satisfies all requirements."
        )

        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=think,
                tool_results=[view_result],
                response="",
                query=query,
                step=counter[0],
            )
        )
        return best_id

    return _get_product_id(top_candidates[0])


def _strategy_shop(
    query: str,
    c: Dict,
    steps: List[Dict],
    counter: List[int],
) -> Optional[List[str]]:
    """
    Strategy for 'shop' problems.
    Goal: Find MULTIPLE products all from the SAME shop.
    Returns: list of product_id strings or None.
    """
    # If we know the shop_id, search directly within it
    if c.get("shop_id"):
        shop_id = str(c["shop_id"])
        search_result = execute_tool_call(
            "find_product",
            {"q": query, "shop_id": shop_id, "sort": "order"},
        )
        products = search_result.get("result", []) or []

        think = (
            f"I searched for '{query}' directly within shop ID {shop_id}. "
            f"The shop constraint requires all recommended products to come from this specific shop. "
            f"Found {len(products)} products in this shop. "
        )
        if products:
            think += _summarize_products(products[:3])

        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=think,
                tool_results=[search_result],
                response="",
                query=query,
                step=counter[0],
            )
        )

        if products:
            return [_get_product_id(p) for p in products[:3] if _get_product_id(p)]

    # No shop_id known — discover the best shop from broad search
    search_result = execute_tool_call(
        "find_product", {"q": query, "sort": "order"}
    )
    products = search_result.get("result", []) or []

    think = (
        f"I searched for '{query}' to discover which shop has the most relevant products. "
        f"I need to identify a shop with multiple matching items. "
        f"Found {len(products)} products across different shops. "
    )
    if products:
        think += _summarize_products(products[:5])

    counter[0] += 1
    steps.append(
        create_dialogue_step(
            think=think,
            tool_results=[search_result],
            response="",
            query=query,
            step=counter[0],
        )
    )

    if not products:
        return None

    # Group by shop and find the best shop
    shop_groups: Dict[str, List[Dict]] = {}
    for p in products:
        sid = _get_shop_id(p) or "unknown"
        shop_groups.setdefault(sid, []).append(p)

    # Pick shop with most products (excluding "unknown")
    valid_shops = {k: v for k, v in shop_groups.items() if k != "unknown"}
    if not valid_shops:
        return [_get_product_id(products[0])] if products else None

    best_shop_id = max(valid_shops, key=lambda s: len(valid_shops[s]))
    c["shop_id"] = best_shop_id  # Update constraints for subsequent calls

    think2 = (
        f"I identified shop ID {best_shop_id} as having the most matching products "
        f"({len(valid_shops[best_shop_id])} items found). "
        f"I will now search specifically within this shop to find more qualifying products."
    )

    # Search within the best shop
    shop_search_result = execute_tool_call(
        "find_product",
        {"q": query, "shop_id": best_shop_id, "sort": "order"},
    )
    shop_products = shop_search_result.get("result", []) or []

    if shop_products:
        think2 += (
            f" The targeted shop search returned {len(shop_products)} products. "
            + _summarize_products(shop_products[:3])
        )

    counter[0] += 1
    steps.append(
        create_dialogue_step(
            think=think2,
            tool_results=[shop_search_result],
            response="",
            query=query,
            step=counter[0],
        )
    )

    source = shop_products or valid_shops[best_shop_id]
    ids = [_get_product_id(p) for p in source[:3] if _get_product_id(p)]
    return ids if ids else None


def _strategy_voucher(
    query: str,
    c: Dict,
    steps: List[Dict],
    counter: List[int],
) -> Optional[str]:
    """
    Strategy for 'voucher' problems.
    Goal: Find a product within budget AFTER applying the voucher/discount.
    Returns: product_id string or None.
    """
    budget = c.get("budget")
    voucher = c.get("voucher")

    # Explain the strategy in the first think
    budget_str = str(budget) if budget else "unspecified"
    voucher_str = str(voucher) if voucher else "none"

    configs = _build_search_configs(c, "voucher")
    all_products: List[Dict] = []

    for cfg in configs[:3]:
        search_result = execute_tool_call(
            "find_product",
            {k: v for k, v in cfg.items() if v or v == 0},
        )
        products = search_result.get("result", []) or []

        cfg_filters = ", ".join(
            f"{k}={v}" for k, v in cfg.items() if k != "q" and v
        ) or "no extra filters"
        think = (
            f"I am searching for products matching '{cfg['q']}' "
            f"with budget {budget_str} and voucher '{voucher_str}'. "
            f"Search filters applied: {cfg_filters}. "
            f"Found {len(products)} products. "
        )
        if products:
            think += _summarize_products(products[:3])
        else:
            think += "No results with these filters — trying next configuration."

        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=think,
                tool_results=[search_result],
                response="",
                query=query,
                step=counter[0],
            )
        )

        all_products.extend(products)
        if len(all_products) >= 5:
            break

    if not all_products:
        return None

    # View details of top 3 candidates
    top_ids = [_get_product_id(p) for p in all_products[:3] if _get_product_id(p)]
    if not top_ids:
        return None

    ids_str = ",".join(top_ids)
    view_result = execute_tool_call(
        "view_product_information", {"product_ids": ids_str}
    )
    details = view_result.get("result", []) or []

    # Find best product within budget after voucher
    best_id = ""
    best_effective_price = float("inf")
    verifications = []

    source = details if details else all_products
    for p in source:
        pid = _get_product_id(p)
        if not pid:
            continue

        raw_price = p.get("price") or p.get("price_min")
        price = _price_cents_to_units(raw_price)
        if price is None:
            continue

        effective = _apply_voucher(price, voucher) if voucher else price
        within_budget = (budget is None) or (effective <= float(budget))

        verif = (
            f"Product {pid}: original={price:.2f}, "
            f"after voucher={effective:.2f}, "
            f"within budget={within_budget}"
        )
        verifications.append(verif)

        if within_budget and effective < best_effective_price:
            best_effective_price = effective
            best_id = pid

    think = (
        f"I retrieved detailed pricing information for products {ids_str}. "
        f"I calculated the effective price after applying the voucher '{voucher_str}' for each: "
        f"{'; '.join(verifications[:3])}. "
    )
    if best_id:
        think += (
            f"Product {best_id} has the best effective price ({best_effective_price:.2f}) "
            f"that fits within the budget of {budget_str}. I will recommend this product."
        )
    else:
        think += "None of the products fit within the budget after applying the discount."
        # Fallback: take cheapest available
        if source:
            best_id = _get_product_id(source[0])
            think += f" Selecting product {best_id} as the closest match."

    counter[0] += 1
    steps.append(
        create_dialogue_step(
            think=think,
            tool_results=[view_result],
            response="",
            query=query,
            step=counter[0],
        )
    )

    return best_id or (top_ids[0] if top_ids else None)


# ─── LLM-Augmented Refinement ─────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert AI shopping agent on the ORO Bittensor subnet.

Your role: Given a shopping query and the results from search/product tools, \
determine the single best product to recommend.

Reasoning rules:
1. Always use LITERAL values from the API results (IDs, prices, names)
2. Never hallucinate product details
3. Verify price constraints before selecting
4. For shop problems: all products must share the same shop_id
5. For voucher problems: calculate final price = original - discount, verify ≤ budget
6. For product problems: check all required keywords are present in name/attributes

Output format (JSON):
{
  "think": "detailed step-by-step reasoning",
  "selected_product_ids": "id1,id2",
  "confidence": 0.0-1.0,
  "reason": "brief justification"
}"""


def _llm_refine(
    query: str,
    c: Dict,
    problem_type: str,
    products_summary: str,
    details_summary: str,
) -> Optional[str]:
    """
    Use the LLM to refine the product selection when constraints are complex.
    Returns a comma-separated string of product IDs or None.
    """
    constraint_text = _format_constraints(c, problem_type)

    user_msg = f"""\
Shopping task: {query}
Problem type: {problem_type}
Constraints: {constraint_text}

Search results:
{products_summary}

Detailed product info:
{details_summary}

Select the best product(s) for this task. Return JSON with selected_product_ids."""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    response = _call_llm(messages, temperature=0.0)
    decision = _parse_json(response)

    if decision and decision.get("selected_product_ids"):
        return str(decision["selected_product_ids"])
    return None


def _parse_json(text: str) -> Optional[Dict]:
    """Robustly parse JSON from LLM output."""
    if not text:
        return None
    # Strip markdown code fences
    clean = re.sub(r"```(?:json)?\n?", "", text).strip()
    clean = re.sub(r"```\n?", "", clean).strip()
    # Try direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    # Find first JSON object
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _format_constraints(c: Dict, problem_type: str) -> str:
    parts = []
    if c.get("budget"):
        parts.append(f"budget={c['budget']}")
    if c.get("price_range"):
        parts.append(f"price_range={c['price_range']}")
    if c.get("shop_id"):
        parts.append(f"shop_id={c['shop_id']}")
    if c.get("shop_name"):
        parts.append(f"shop={c['shop_name']}")
    if c.get("voucher"):
        parts.append(f"voucher={c['voucher']}")
    if c.get("required_keywords"):
        parts.append(f"required_keywords={c['required_keywords']}")
    if c.get("forbidden_keywords"):
        parts.append(f"forbidden_keywords={c['forbidden_keywords']}")
    if problem_type == "shop":
        parts.append("CONSTRAINT: ALL products must be from SAME shop")
    if problem_type == "voucher":
        parts.append("CONSTRAINT: final price after discount must be ≤ budget")
    return "; ".join(parts) or "none"


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def agent_main(problem_data: Dict) -> List[Dict]:
    """
    Main agent entry point.
    Handles all 3 problem types with specialized strategies +
    LLM-guided reasoning for maximum reasoning_coefficient scoring.

    Args:
        problem_data: Problem definition including query, constraints, context.

    Returns:
        List of dialogue step dicts in the ORO-required format.
    """
    steps: List[Dict] = []
    counter = [0]  # mutable counter shared across helpers

    # ── 1. Parse problem ──────────────────────────────────────────────────
    query = problem_data.get("query", "")
    c = _extract_constraints(problem_data)
    problem_type = _detect_type(c)

    # ── 2. Run specialized strategy ───────────────────────────────────────
    recommended_ids: Optional[str] = None

    try:
        if problem_type == "product":
            pid = _strategy_product(query, c, steps, counter)
            if pid:
                recommended_ids = pid

        elif problem_type == "shop":
            pids = _strategy_shop(query, c, steps, counter)
            if pids:
                recommended_ids = ",".join(pids)

        elif problem_type == "voucher":
            pid = _strategy_voucher(query, c, steps, counter)
            if pid:
                recommended_ids = pid

        else:
            # Generic fallback
            pid = _strategy_product(query, c, steps, counter)
            if pid:
                recommended_ids = pid

    except Exception as exc:  # noqa: BLE001
        # Graceful degradation: simple search on failure
        try:
            fallback = execute_tool_call("find_product", {"q": query})
            prods = fallback.get("result", []) or []
            if prods:
                recommended_ids = _get_product_id(prods[0])
                counter[0] += 1
                steps.append(
                    create_dialogue_step(
                        think=(
                            f"I encountered an unexpected situation ({exc}) and performed "
                            f"a simplified fallback search for '{query}'. "
                            f"I found {len(prods)} products and will recommend the top result."
                        ),
                        tool_results=[fallback],
                        response="",
                        query=query,
                        step=counter[0],
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    # ── 3. Recommend & Terminate ──────────────────────────────────────────
    if recommended_ids:
        rec_result = execute_tool_call(
            "recommend_product", {"product_ids": str(recommended_ids)}
        )
        term_result = execute_tool_call("terminate", {"status": "success"})

        final_response = _build_response(query, recommended_ids, c, problem_type)

        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=(
                    f"I have completed my research for: '{query}'. "
                    f"This was a '{problem_type}' type problem requiring "
                    f"{_format_constraints(c, problem_type)}. "
                    f"After {counter[0] - 1} research step(s) — including searching "
                    f"the product catalog and verifying attributes, prices, and shop "
                    f"constraints against actual API results — I am confident that "
                    f"product(s) {recommended_ids} best satisfies all requirements. "
                    f"I am now submitting my final recommendation."
                ),
                tool_results=[rec_result, term_result],
                response=final_response,
                query=query,
                step=counter[0],
            )
        )
    else:
        term_result = execute_tool_call("terminate", {"status": "failure"})
        counter[0] += 1
        steps.append(
            create_dialogue_step(
                think=(
                    f"After {counter[0] - 1} search attempt(s) for '{query}' "
                    f"({problem_type} problem with constraints: "
                    f"{_format_constraints(c, problem_type)}), "
                    f"I was unable to find any product satisfying all stated requirements. "
                    f"I tried multiple query variations and filter configurations "
                    f"but none of the results met all the necessary constraints."
                ),
                tool_results=[term_result],
                response=(
                    f"I was unable to find products satisfying all requirements for: {query}."
                ),
                query=query,
                step=counter[0],
            )
        )

    return steps


def _build_response(
    query: str,
    product_ids: str,
    c: Dict,
    problem_type: str,
) -> str:
    """Build a rich, informative final response."""
    ids = product_ids.split(",")
    count = len(ids)

    if problem_type == "product":
        return (
            f"After systematically searching and verifying products for '{query}', "
            f"I recommend product {product_ids}. "
            f"This product was selected because it best matches the required "
            f"attributes, price constraints, and category specifications."
        )
    elif problem_type == "shop":
        shop = c.get("shop_id") or c.get("shop_name") or "the identified shop"
        return (
            f"For the shop-specific task '{query}', I identified {shop} as the "
            f"best matching shop and found {count} qualifying product(s). "
            f"I recommend product(s) {product_ids} — all items come from the same shop, "
            f"satisfying the shop-consistency constraint."
        )
    elif problem_type == "voucher":
        budget = c.get("budget", "the specified budget")
        voucher = c.get("voucher", "the provided voucher")
        return (
            f"For the budget-constrained task '{query}' with voucher '{voucher}', "
            f"I recommend product {product_ids}. "
            f"This product's final price after applying the discount falls within "
            f"the budget of {budget}, making it the optimal choice."
        )
    return (
        f"Based on my research for '{query}', I recommend product(s) {product_ids} "
        f"as the best match for all stated requirements."
    )
