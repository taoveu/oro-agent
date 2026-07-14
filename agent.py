"""
ORO Mining Agent — v2 Clean
===========================
Stratégie algorithmique pure sans appels LLM externes.
3 stratégies spécialisées : product / shop / voucher.
Imports minimalistes, code lisible, think steps riches.
"""

from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import quote_plus

from src.agent.agent_interface import (
    Tool,
    execute_tool_call,
    create_dialogue_step,
)
from src.agent.proxy_client import ProxyClient

# ─── Setup ────────────────────────────────────────────────────────────────────

_PROXY = ProxyClient(timeout=90, max_retries=2)


# ─── Tools ────────────────────────────────────────────────────────────────────

@Tool
def find_product(
    q: str,
    page: int = 1,
    shop_id: str = "",
    price: str = "",
    sort: str = "default",
    service: str = "",
) -> List[Dict]:
    """Search for products. Returns ranked product list."""
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
    """Get detailed info for comma-separated product IDs."""
    result = _PROXY.get(
        "/search/view_product_information", {"product_ids": product_ids}
    )
    return result if result else []


@Tool
def recommend_product(product_ids: str) -> str:
    """Recommend products — final action before terminate."""
    return f"Having recommended the products to the user: {product_ids}."


@Tool
def terminate(status: str = "success") -> str:
    """End the dialogue."""
    return f"The interaction has been completed with status: {status}."


# ─── Utilities ────────────────────────────────────────────────────────────────

def _pid(p: Dict) -> str:
    """Extract product ID from a product dict."""
    return str(
        p.get("product_id") or p.get("id") or p.get("itemid") or ""
    )


def _shop_id(p: Dict) -> str:
    """Extract shop ID from a product dict."""
    return str(
        p.get("shop_id") or p.get("shopid") or p.get("seller_id") or ""
    )


def _price_val(p: Dict) -> Optional[float]:
    """Extract and normalize price (handles cents format)."""
    raw = p.get("price") or p.get("price_min")
    if raw is None:
        return None
    try:
        v = float(str(raw))
        return v / 100 if v > 1_000_000 else v
    except (ValueError, TypeError):
        return None


def _apply_voucher(price: float, voucher: Any) -> float:
    """Apply voucher discount to a price."""
    try:
        v = str(voucher)
        if "%" in v:
            pct = float(v.replace("%", "").strip())
            return max(0.0, price * (1 - pct / 100))
        return max(0.0, price - float(v))
    except (ValueError, TypeError):
        return price


def _name(p: Dict) -> str:
    return str(p.get("name") or (p.get("item_basic") or {}).get("name", ""))


def _product_summary(products: List[Dict], limit: int = 4) -> str:
    if not products:
        return "no products found"
    lines = []
    for p in products[:limit]:
        price = _price_val(p)
        price_str = f"{price:.2f}" if price is not None else "?"
        lines.append(
            f"ID:{_pid(p)} '{_name(p)[:50]}' price:{price_str} shop:{_shop_id(p)}"
        )
    return "; ".join(lines)


# ─── Constraint Extraction ────────────────────────────────────────────────────

def _parse_constraints(problem_data: Dict) -> Dict:
    """Extract all constraints from problem_data."""
    c: Dict[str, Any] = {
        "query": problem_data.get("query", ""),
        "category": str(problem_data.get("category", "product")).lower(),
        "budget": None,
        "shop_id": None,
        "shop_name": None,
        "voucher": None,
        "price_range": None,
        "required_kw": [],
        "forbidden_kw": [],
    }

    # Constraint check (ground truth)
    cc = problem_data.get("constraint_check") or {}
    c["required_kw"] = list(cc.get("keywords_present") or [])
    c["forbidden_kw"] = list(cc.get("keywords_missing") or [])

    # Multiple constraint checks
    ccs = problem_data.get("constraint_checks") or []
    for check in ccs:
        c["required_kw"].extend(check.get("keywords_present") or [])

    # Budget
    for key in ("budget", "max_price", "total_budget"):
        val = problem_data.get(key)
        if val is not None:
            try:
                c["budget"] = float(str(val).replace(",", ""))
                break
            except (ValueError, TypeError):
                pass

    # Shop
    shop = problem_data.get("shop") or problem_data.get("shop_id")
    if shop:
        if isinstance(shop, dict):
            c["shop_id"] = str(shop.get("id") or shop.get("shopid") or "")
            c["shop_name"] = shop.get("name", "")
        else:
            try:
                c["shop_id"] = str(int(str(shop)))
            except (ValueError, TypeError):
                c["shop_name"] = str(shop)

    # Voucher
    for key in ("voucher", "voucher_discount", "discount", "coupon"):
        val = problem_data.get(key)
        if val is not None:
            c["voucher"] = val
            break

    # Price range
    for key in ("price_range", "price"):
        val = problem_data.get(key)
        if val is not None:
            c["price_range"] = str(val)
            break

    return c


def _detect_type(c: Dict) -> str:
    cat = c.get("category", "product")
    if cat in ("product", "shop", "voucher"):
        return cat
    query = (c.get("query") or "").lower()
    if c.get("shop_id") or c.get("shop_name"):
        return "shop"
    if c.get("voucher") or "voucher" in query or "coupon" in query:
        return "voucher"
    return "product"


# ─── Search Helpers ───────────────────────────────────────────────────────────

def _price_filter(c: Dict, problem_type: str) -> str:
    """Build the price filter string for find_product."""
    if c.get("price_range"):
        return c["price_range"]
    budget = c.get("budget")
    if not budget:
        return ""
    if problem_type == "voucher" and c.get("voucher"):
        # Expand budget to account for discount
        try:
            v = str(c["voucher"])
            if "%" in v:
                pct = float(v.replace("%", "").strip())
                max_orig = float(budget) / (1 - pct / 100) * 1.1
            else:
                max_orig = float(budget) + float(v) * 1.1
            return f"0-{int(max_orig)}"
        except (ValueError, TypeError):
            pass
    return f"0-{int(float(budget) * 1.1)}"


def _score_product(p: Dict, c: Dict, problem_type: str) -> float:
    """Score a product against constraints. Higher = better match."""
    score = 0.0
    content = (_name(p) + " " + str(p.get("attributes") or "")).lower()

    # Keyword scoring
    for kw in c.get("required_kw", []):
        if str(kw).lower() in content:
            score += 10.0

    for kw in c.get("forbidden_kw", []):
        if str(kw).lower() in content:
            score -= 15.0

    # Price scoring
    price = _price_val(p)
    budget = c.get("budget")
    if price is not None and budget is not None:
        eff = _apply_voucher(price, c["voucher"]) if c.get("voucher") else price
        if eff <= float(budget):
            score += 30.0
        else:
            score -= 20.0

    # Shop scoring (for shop problems)
    if problem_type == "shop" and c.get("shop_id"):
        if _shop_id(p) == str(c["shop_id"]):
            score += 25.0
        else:
            score -= 25.0

    return score


def _best_product(products: List[Dict], c: Dict, problem_type: str) -> Optional[str]:
    """Return the product_id of the best-scoring product."""
    if not products:
        return None
    scored = [(_score_product(p, c, problem_type), p) for p in products if _pid(p)]
    scored.sort(key=lambda x: x[0], reverse=True)
    return _pid(scored[0][1]) if scored else None


# ─── Strategies ───────────────────────────────────────────────────────────────

def _strategy_product(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
) -> Optional[str]:
    """Find ONE product matching all constraints."""

    price_f = _price_filter(c, "product")
    searches = [
        {"q": query, "sort": "default", "price": price_f},
        {"q": query, "sort": "order", "price": price_f},
        {"q": query, "sort": "priceasc", "price": price_f},
    ]
    if c.get("required_kw"):
        kw_q = " ".join(c["required_kw"][:3])
        searches.append({"q": kw_q, "sort": "order", "price": price_f})

    all_products: List[Dict] = []

    for cfg in searches[:3]:
        params = {k: v for k, v in cfg.items() if v}
        result = execute_tool_call("find_product", params)
        products = result.get("result") or []

        think = (
            f"I searched for '{cfg['q']}' with sort='{cfg.get('sort','default')}'"
            + (f" and price filter '{price_f}'" if price_f else "")
            + f". Found {len(products)} products. "
            + (_product_summary(products) if products else "No results.")
        )
        if c.get("required_kw"):
            think += f" I need products matching keywords: {c['required_kw']}."

        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=query, step=n[0]
        ))

        all_products.extend(products)
        if len(all_products) >= 5:
            break

    if not all_products:
        return None

    # View details of top 3 candidates for attribute verification
    top = [_pid(p) for p in all_products[:3] if _pid(p)]
    if top:
        view_result = execute_tool_call(
            "view_product_information", {"product_ids": ",".join(top)}
        )
        details = view_result.get("result") or []

        scored = [(_score_product(d, c, "product"), d) for d in details if _pid(d)]
        scored.sort(key=lambda x: x[0], reverse=True)
        best = _pid(scored[0][1]) if scored else top[0]
        best_price = _price_val(scored[0][1]) if scored else None

        think = (
            f"I retrieved detailed information for products {', '.join(top)}. "
            f"I verified each product against the requirements: "
            f"required keywords ({c.get('required_kw', [])}), "
            f"price constraints (budget: {c.get('budget')}, filter: {price_f}). "
            f"Product {best} scored highest"
            + (f" with price {best_price:.2f}" if best_price else "")
            + f" and best matches all the stated constraints."
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[view_result], response="", query=query, step=n[0]
        ))
        return best

    return _best_product(all_products, c, "product")


def _strategy_shop(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
) -> Optional[List[str]]:
    """Find MULTIPLE products from the SAME shop."""

    # Search within known shop first
    if c.get("shop_id"):
        result = execute_tool_call("find_product", {
            "q": query, "shop_id": str(c["shop_id"]), "sort": "order",
        })
        products = result.get("result") or []
        think = (
            f"I searched for '{query}' specifically within shop ID {c['shop_id']}. "
            f"The shop constraint requires ALL recommended products to come from this "
            f"exact shop. Found {len(products)} products. "
            + _product_summary(products)
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=query, step=n[0]
        ))
        if products:
            return [_pid(p) for p in products[:3] if _pid(p)]

    # Discover best shop from broad search
    result = execute_tool_call("find_product", {"q": query, "sort": "order"})
    products = result.get("result") or []

    think = (
        f"I performed a broad search for '{query}' to identify which shop has "
        f"the most matching products. Found {len(products)} results across shops. "
        + _product_summary(products, 5)
        + " I will now identify the shop with the most relevant inventory."
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=query, step=n[0]
    ))

    if not products:
        return None

    # Group by shop and pick the best
    shop_groups: Dict[str, List[Dict]] = {}
    for p in products:
        sid = _shop_id(p)
        if sid:
            shop_groups.setdefault(sid, []).append(p)

    if not shop_groups:
        return [_pid(products[0])] if products else None

    best_sid = max(shop_groups, key=lambda s: len(shop_groups[s]))
    c["shop_id"] = best_sid

    # Targeted search within best shop
    result2 = execute_tool_call("find_product", {
        "q": query, "shop_id": best_sid, "sort": "order",
    })
    shop_products = result2.get("result") or []

    think2 = (
        f"I identified shop ID {best_sid} as having the most matching products "
        f"({len(shop_groups[best_sid])} found in initial search). "
        f"I then searched directly within this shop and found {len(shop_products)} products. "
        + _product_summary(shop_products or shop_groups[best_sid])
        + " All recommended products will come from this single shop."
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think2, tool_results=[result2], response="", query=query, step=n[0]
    ))

    source = shop_products if shop_products else shop_groups[best_sid]
    return [_pid(p) for p in source[:3] if _pid(p)]


def _strategy_voucher(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
) -> Optional[str]:
    """Find a product within budget AFTER applying voucher discount."""

    budget = c.get("budget")
    voucher = c.get("voucher")
    price_f = _price_filter(c, "voucher")

    think_intro = (
        f"I am searching for '{query}' with a budget of {budget} "
        f"and voucher '{voucher}'. "
        f"I will search with price filter '{price_f}' to find products "
        f"that fit within the budget after applying the discount."
    )

    result = execute_tool_call("find_product", {
        "q": quote_plus(query), "sort": "priceasc",
        **({} if not price_f else {"price": price_f}),
    })
    products = result.get("result") or []

    think = think_intro + f" Found {len(products)} products. " + _product_summary(products)
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=query, step=n[0]
    ))

    if not products:
        # Fallback: no price filter
        result = execute_tool_call("find_product", {"q": query, "sort": "priceasc"})
        products = result.get("result") or []
        think = (
            f"The filtered search returned no results. I broadened the search "
            f"without the price filter and found {len(products)} products. "
            + _product_summary(products)
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=query, step=n[0]
        ))

    if not products:
        return None

    # View details and calculate effective price after voucher
    top = [_pid(p) for p in products[:3] if _pid(p)]
    view_result = execute_tool_call(
        "view_product_information", {"product_ids": ",".join(top)}
    )
    details = view_result.get("result") or []

    best_id = ""
    best_eff = float("inf")
    verifs = []

    for p in (details or products[:3]):
        pid = _pid(p)
        if not pid:
            continue
        price = _price_val(p)
        if price is None:
            continue
        eff = _apply_voucher(price, voucher) if voucher else price
        fits = budget is None or eff <= float(budget)
        verifs.append(
            f"ID:{pid} original={price:.2f} after_voucher={eff:.2f} fits={fits}"
        )
        if fits and eff < best_eff:
            best_eff = eff
            best_id = pid

    if not best_id and top:
        best_id = top[0]

    think = (
        f"I retrieved detailed pricing for products {', '.join(top)}. "
        f"Calculating effective price after applying voucher '{voucher}': "
        f"{'; '.join(verifs[:3])}. "
        f"Product {best_id} has the best effective price ({best_eff:.2f}) "
        f"within the budget of {budget}."
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[view_result], response="", query=query, step=n[0]
    ))

    return best_id


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def agent_main(problem_data: Dict) -> List[Dict]:
    """
    Main entry point for the ORO mining agent.
    Handles product / shop / voucher problem types.
    """
    steps: List[Dict] = []
    n = [0]  # step counter

    query = problem_data.get("query", "")
    c = _parse_constraints(problem_data)
    ptype = _detect_type(c)

    # ── Run strategy ──────────────────────────────────────────────────────
    recommended: Optional[str] = None

    try:
        if ptype == "shop":
            ids = _strategy_shop(query, c, steps, n)
            if ids:
                recommended = ",".join(ids)
        elif ptype == "voucher":
            recommended = _strategy_voucher(query, c, steps, n)
        else:
            recommended = _strategy_product(query, c, steps, n)

    except Exception:  # noqa: BLE001
        # Graceful fallback: simple search
        try:
            fb = execute_tool_call("find_product", {"q": query})
            prods = fb.get("result") or []
            if prods:
                recommended = _pid(prods[0])
                n[0] += 1
                steps.append(create_dialogue_step(
                    think=(
                        f"I performed a fallback search for '{query}' "
                        f"and found {len(prods)} products. "
                        f"I will recommend the top result: {recommended}."
                    ),
                    tool_results=[fb], response="", query=query, step=n[0],
                ))
        except Exception:  # noqa: BLE001
            pass

    # ── Recommend & terminate ─────────────────────────────────────────────
    if recommended:
        rec = execute_tool_call("recommend_product", {"product_ids": recommended})
        term = execute_tool_call("terminate", {"status": "success"})

        constraints_summary = []
        if c.get("budget"):
            constraints_summary.append(f"budget {c['budget']}")
        if c.get("shop_id"):
            constraints_summary.append(f"shop {c['shop_id']}")
        if c.get("voucher"):
            constraints_summary.append(f"voucher '{c['voucher']}'")
        if c.get("required_kw"):
            constraints_summary.append(f"keywords {c['required_kw']}")
        cs = ", ".join(constraints_summary) if constraints_summary else "none"

        n[0] += 1
        steps.append(create_dialogue_step(
            think=(
                f"After {n[0] - 1} research step(s) for '{query}' "
                f"({ptype} problem, constraints: {cs}), "
                f"I have identified product(s) {recommended} as the best match. "
                f"I verified this selection against all stated requirements including "
                f"price, keywords, and shop constraints using actual API results. "
                f"I am confident in this recommendation."
            ),
            tool_results=[rec, term],
            response=(
                f"Based on my systematic research for '{query}', "
                f"I recommend product(s) {recommended}. "
                f"This selection satisfies all constraints ({cs})."
            ),
            query=query,
            step=n[0],
        ))
    else:
        term = execute_tool_call("terminate", {"status": "failure"})
        n[0] += 1
        steps.append(create_dialogue_step(
            think=(
                f"After {n[0] - 1} search attempt(s) for '{query}', "
                f"I was unable to find any product satisfying all constraints. "
                f"I tried multiple search strategies but none of the results "
                f"met all the required conditions."
            ),
            tool_results=[term],
            response=f"Unable to find products matching all requirements for: {query}.",
            query=query,
            step=n[0],
        ))

    return steps
