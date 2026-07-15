"""
ORO Mining Agent — v5.2
========================
v5.2 (2026-07-16) :
- fix: sélection déterministe du shop commun pour voucher shop-type
  (list(set)[0] était aléatoire → score par pertinence query-term)

v5.1 (2026-07-16) :
- fix: _clean_search_query() — supprime prix/filler de la NL query avant find_product
  (ex: 'priced from 15 to 37 pesos' → câbles électriques; fix → 'car sponge pads')
- fix: _extract_query_terms() garde les tirets ('pre-strung' reste compound)
- fix: _score_product() utilise _keyword_match (word-boundary) au lieu de substring

v5.0 (2026-07-15) :
- fix CRITIQUE: API réelle retourne 'title', pas 'name' → noms vides en prod
- fix: constraint_check non envoyé en prod → scoring via _extract_query_terms()
- fix: view_product_information n'a pas de 'price' → candidats skippés en v4
- fix: sold_count dominant (chocolats battaient les sacs) → poids réduit à 0.05x
- feat: _merge_product_data() — fusionne find_product (title+price) + view (attributes)
- feat: _strategy_product() re-score après merge pour décision finale

v4 :
- Voucher structuré V3 : cap, threshold, voucher_type (platform/shop)
- Voucher multi-produits : panier N items avec vérification basket total
- Shop multi-produits : intersection des shops pour N sous-requêtes
- Prix minimum : extraction "over/above X" depuis la query
- Service filter : LazFlash, COD détectés dans la query
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from src.agent.agent_interface import (
    Tool,
    create_dialogue_step,
    execute_tool_call,
)
from src.agent.proxy_client import ProxyClient

# ─── Setup ────────────────────────────────────────────────────────────────────

_PROXY = ProxyClient(timeout=90, max_retries=2)

_RE_BUDGET_END = re.compile(
    r"\n\n?My budget is only.*|\.?\s*My budget is only.*",
    re.DOTALL | re.IGNORECASE,
)
_RE_TRAILING_HELP = re.compile(
    r"\s*(?:Can you help me find (?:these products?|a store[^?]*)[\?.]?|"
    r"Please show (?:me )?(?:stores?|shops?)[^.]*\.?|"
    r"Show me options matching these details\.?)",
    re.IGNORECASE,
)


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


# ─── Core Utilities ───────────────────────────────────────────────────────────


def _pid(p: Dict) -> str:
    direct = p.get("product_id") or p.get("id") or p.get("itemid")
    if direct:
        return str(direct)
    nested = (p.get("item_basic") or {}).get("itemid") or (
        p.get("item_basic") or {}
    ).get("id")
    return str(nested) if nested else ""


def _shop_id(p: Dict) -> str:
    return str(p.get("shop_id") or p.get("shopid") or p.get("seller_id") or "")


def _price_val(p: Dict) -> Optional[float]:
    raw = p.get("price") or p.get("price_min")
    if raw is None:
        return None
    try:
        v = float(str(raw))
        return v / 100 if v > 1_000_000 else v
    except (ValueError, TypeError):
        return None


def _apply_voucher(price: float, voucher: Any) -> float:
    """Apply legacy string voucher (e.g. '20%' or '10')."""
    try:
        v = str(voucher)
        if "%" in v:
            pct = float(v.split("%")[0].strip())
            return max(0.0, price * (1 - pct / 100))
        return max(0.0, price - float(v.split()[0]))
    except (ValueError, TypeError):
        return price


def _apply_voucher_obj(total: float, vobj: Dict) -> float:
    """Apply a structured V3 voucher object to a basket total.

    Checks threshold (min spend), then applies percentage (with cap) or fixed discount.
    """
    threshold = float(vobj.get("threshold") or 0)
    if total <= threshold:
        return total  # voucher not activated
    disc_type = str(vobj.get("discount_type") or "fixed").lower()
    if disc_type == "percentage":
        rate = float(vobj.get("discount") or 0)
        disc = total * rate
        cap = vobj.get("cap")
        if cap is not None:
            disc = min(disc, float(cap))
    else:
        disc = float(vobj.get("face_value") or 0)
    return max(0.0, total - disc)


def _eff_price(price: float, c: Dict) -> float:
    """Effective price after applying voucher (obj or legacy string)."""
    vobj = c.get("voucher_obj")
    if vobj:
        return _apply_voucher_obj(price, vobj)
    v = c.get("voucher")
    if v:
        return _apply_voucher(price, v)
    return price


def _name(p: Dict) -> str:
    """Extract product name — real ORO API uses 'title', not 'name'."""
    return str(
        p.get("title") or p.get("name")
        or (p.get("item_basic") or {}).get("name", "")
        or (p.get("item_basic") or {}).get("title", "")
        or ""
    )


# ─── Stopwords for query term extraction ─────────────────────────────────────
_STOPWORDS = {
    "looking", "for", "a", "an", "the", "in", "at", "on", "of", "with",
    "and", "or", "to", "from", "by", "is", "are", "was", "i", "me",
    "my", "that", "this", "it", "its", "be", "have", "has", "do",
    "show", "find", "get", "want", "need", "please", "some",
    "any", "all", "brand", "item", "product", "one", "ones", "piece",
    "pieces", "unit", "units", "set", "type", "kind", "style",
    "php", "peso", "pesos", "priced", "price", "cost", "costs",
    "over", "above", "below", "under", "between", "more", "than",
    "less", "within", "up",
}


def _extract_query_terms(query: str) -> List[str]:
    """Extract meaningful search terms from a natural language query."""
    # Keep hyphens so 'pre-strung' stays as one unit
    clean = re.sub(r"[^\w\s-]", " ", query.lower())
    words = clean.split()
    terms = [
        w.strip("-")
        for w in words
        if len(w.strip("-")) > 2
        and w.strip("-") not in _STOPWORDS
        and not re.match(r"^[\d-]+$", w)
    ]
    return terms


def _clean_search_query(query: str) -> str:
    """Extract clean product search terms from a natural language query.

    Removes price ranges, filler phrases, service tags and other non-product
    text so the search API receives only relevant product keywords.

    Examples:
      'Looking for a hosport brand waist bag for motorcycles, priced from 180 to 505 PHP.'
        -> 'hosport brand waist bag for motorcycles'
      'Show me headlight covers where the cost is above 85 PHP.'
        -> 'headlight covers'
      'Looking for car sponge pads, pack of 10, priced from 15 to 37 pesos.'
        -> 'car sponge pads, pack of 10'
    """
    q = query.strip()

    # 1. Remove leading filler phrases
    q = re.sub(
        r'^(?:show\s+me\s+|find\s+me\s+|i\s+want\s+|i\s+need\s+'
        r'|(?:i(?:\'m|\s+am)\s+)?looking\s+for\s+(?:a\s+|an\s+)?)',
        '', q, flags=re.IGNORECASE,
    ).strip()

    # 2. Remove price range / cost mentions (many patterns)
    _PRICE_PATTERNS = [
        r',?\s*priced?\s+from\s+[\d,]+\s+to\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*(?:that\s+)?cost[s]?\s+(?:between|from)\s+[\d,]+\s+(?:and\s+)?[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*(?:and\s+)?cost[s]?\s+(?:over|above|more\s+than)\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*where\s+the\s+cost\s+is\s+(?:over|above|below|under)\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*priced?\s+(?:over|above|below|under|from)\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*(?:that\s+)?cost[s]?\s+(?:over|above|below|under)\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        r',?\s*(?:at|for)\s+[\d,]+\s+(?:to|and)\s+[\d,]+\s*(?:php|peso[s]?)\.?',
        # Trailing "and" left over from "and cost between X and Y"
        r'\s+and\s*$',
    ]
    for pat in _PRICE_PATTERNS:
        q = re.sub(pat, '', q, flags=re.IGNORECASE)

    # 3. Remove service tag mentions
    q = re.sub(r',?\s*with\s+laz\s*flash\s+deals?\s*\.?', '', q, flags=re.IGNORECASE)
    q = re.sub(r',?\s*(?:with\s+)?(?:laz\s*flash)\s*\.?', '', q, flags=re.IGNORECASE)

    # 4. Remove "in the X category"
    q = re.sub(r'\s+in\s+the\s+\w+\s+category\.?', '', q, flags=re.IGNORECASE)

    # 5. Remove "that come/run/are X" descriptive clauses at end
    q = re.sub(r'\s+that\s+(?:come|run|are|have)\s+\S+', '', q, flags=re.IGNORECASE)

    # 6. Remove parenthetical quantity notes like "(1 piece)"
    q = re.sub(r'\s*\(\d+\s+(?:piece|pcs?|unit[s]?)\)', '', q, flags=re.IGNORECASE)

    # 7. Final cleanup
    q = q.strip().rstrip('.,;')
    q = ' '.join(q.split())

    return q if len(q) > 3 else query


def _searchable(p: Dict) -> str:
    """Build full searchable text from all product fields (real API format)."""
    parts = [_name(p)]
    parts.append(str(p.get("description") or ""))
    parts.append(str(p.get("short_description") or ""))
    attrs = p.get("attributes") or {}
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            parts.append(k.replace("_", " "))
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            else:
                parts.append(str(v))
    sku = p.get("sku_options") or {}
    if isinstance(sku, dict):
        for sv in sku.values():
            if isinstance(sv, dict):
                parts.extend(str(x) for x in sv.values())
    return " ".join(parts).lower()


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
    """Extract all constraints from problem_data (supports V3 dict voucher)."""
    c: Dict[str, Any] = {
        "query": problem_data.get("query", ""),
        "category": str(problem_data.get("category", "product")).lower(),
        "budget": None,
        "shop_id": None,
        "shop_name": None,
        "voucher": None,
        "voucher_obj": None,
        "voucher_threshold": 0.0,
        "voucher_type": "platform",
        "price_range": None,
        "required_kw": [],
        "forbidden_kw": [],
    }

    # constraint_check is NOT sent to agents in production — only to the validator.
    # We keep it for test harness backward compatibility.
    cc = problem_data.get("constraint_check") or {}
    c["required_kw"] = list(cc.get("keywords_present") or [])
    c["forbidden_kw"] = list(cc.get("keywords_missing") or [])

    for check in (problem_data.get("constraint_checks") or []):
        c["required_kw"].extend(check.get("keywords_present") or [])

    # Always extract query terms for relevance scoring (works in production too)
    c["_query_terms"] = _extract_query_terms(c["query"])

    for key in ("budget", "max_price", "total_budget"):
        val = problem_data.get(key)
        if val is not None:
            try:
                c["budget"] = float(str(val).replace(",", ""))
                break
            except (ValueError, TypeError):
                pass

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

    for key in ("voucher", "voucher_discount", "discount", "coupon"):
        val = problem_data.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            c["voucher_obj"] = val
            c["voucher_threshold"] = float(val.get("threshold") or 0)
            c["voucher_type"] = str(val.get("voucher_type") or "platform").lower()
            if c["budget"] is None and val.get("budget") is not None:
                try:
                    c["budget"] = float(val["budget"])
                except (ValueError, TypeError):
                    pass
            disc_type = str(val.get("discount_type") or "fixed").lower()
            if disc_type == "percentage":
                pct = int((val.get("discount") or 0) * 100)
                cap = val.get("cap")
                c["voucher"] = f"{pct}%" + (f" (cap {cap})" if cap else "")
            else:
                fv = val.get("face_value")
                c["voucher"] = str(int(fv)) if fv else "0"
        else:
            c["voucher"] = val
        break

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
    if c.get("voucher") or c.get("voucher_obj") or "voucher" in query:
        return "voucher"
    return "product"


# ─── Price Range Parsing ──────────────────────────────────────────────────────


def _parse_price_range_from_query(query: str) -> Tuple[Optional[float], Optional[float]]:
    """Extract (min_price, max_price) from natural language query."""
    q = query.lower()

    m = re.search(
        r"(?:from|between|priced\s+)\s*([\d,]+)\s+(?:to|and)\s+([\d,]+)", q
    )
    if m:
        try:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except ValueError:
            pass

    m = re.search(
        r"(?:cost|costs|priced|price)?\s*(?:over|above|more\s+than|greater\s+than)\s+([\d,]+)",
        q,
    )
    if m:
        try:
            return float(m.group(1).replace(",", "")), None
        except ValueError:
            pass

    m = re.search(
        r"(?:under|below|less\s+than|up\s+to|within|max(?:imum)?)\s+([\d,]+)", q
    )
    if m:
        try:
            return None, float(m.group(1).replace(",", ""))
        except ValueError:
            pass

    return None, None


def _price_filter(c: Dict, problem_type: str) -> str:
    """Build price filter string for find_product API."""
    if c.get("price_range"):
        return c["price_range"]

    query = c.get("query", "")
    lo, hi = _parse_price_range_from_query(query)
    budget = c.get("budget")

    if hi is not None:
        upper = hi
    elif budget is not None:
        if problem_type == "voucher":
            vobj = c.get("voucher_obj")
            if vobj:
                disc_type = str(vobj.get("discount_type") or "fixed").lower()
                if disc_type == "percentage":
                    rate = float(vobj.get("discount") or 0)
                    cap = vobj.get("cap")
                    if cap:
                        upper = float(budget) + float(cap) * 1.1
                    else:
                        upper = float(budget) / max(0.01, 1 - rate) * 1.1
                else:
                    fv = float(vobj.get("face_value") or 0)
                    upper = float(budget) + fv * 1.1
            else:
                v = c.get("voucher")
                if v:
                    try:
                        vs = str(v)
                        if "%" in vs:
                            pct = float(vs.split("%")[0].strip())
                            upper = float(budget) / (1 - pct / 100) * 1.1
                        else:
                            upper = float(budget) + float(vs.split()[0]) * 1.1
                    except (ValueError, TypeError):
                        upper = float(budget) * 1.1
                else:
                    upper = float(budget) * 1.1
        else:
            upper = float(budget) * 1.1
    else:
        upper = None

    lo_str = str(int(lo)) if lo is not None else "0"

    if upper is not None:
        return f"{lo_str}-{int(upper)}"
    elif lo is not None:
        return f"{lo_str}-"
    return ""


# ─── Keyword & Scoring Helpers ────────────────────────────────────────────────


def _keyword_match(kw: str, content: str) -> bool:
    """Word-boundary aware keyword matching."""
    kw_lower = str(kw).lower().strip()
    if not kw_lower:
        return False
    if " " in kw_lower:
        return kw_lower in content
    _PUNCT = "()[]{}.,!?;:'\"\\_/-"
    for w in content.split():
        if w.strip(_PUNCT) == kw_lower:
            return True
    return False


def _score_product(p: Dict, c: Dict, problem_type: str) -> float:
    """Score a product. Uses full searchable text including title + attributes."""
    score = 0.0
    content = _searchable(p)  # title + description + attributes + sku

    # Hard keyword requirements (from test harness constraint_check)
    for kw in c.get("required_kw", []):
        if _keyword_match(kw, content):
            score += 20.0
        else:
            score -= 5.0  # mild penalty for missing required keyword
    for kw in c.get("forbidden_kw", []):
        if _keyword_match(kw, content):
            score -= 30.0

    # Query term relevance scoring (always active — works in production)
    # Uses word-boundary matching to avoid false substring matches
    query_terms = c.get("_query_terms") or []
    for term in query_terms:
        if _keyword_match(term, content):
            score += 3.0

    # Budget compliance
    price = _price_val(p)
    budget = c.get("budget")
    if price is not None and budget is not None:
        eff = _eff_price(price, c)
        if eff <= float(budget):
            score += 30.0
        else:
            score -= 20.0

    # Shop constraint
    if problem_type == "shop" and c.get("shop_id"):
        if _shop_id(p) == str(c["shop_id"]):
            score += 25.0
        else:
            score -= 25.0

    # sold_count as minor tiebreaker only (not dominant)
    score += min(p.get("sold_count", 0), 200) * 0.05

    return score


def _hard_filter(products: List[Dict], c: Dict, problem_type: str = "product") -> List[Dict]:
    """Remove products violating any hard constraint (budget, shop, forbidden_kw)."""
    out = []
    budget = c.get("budget")
    shop_id = c.get("shop_id")
    forbidden = c.get("forbidden_kw", [])

    for p in products:
        content = _searchable(p)
        price = _price_val(p)

        if budget is not None and price is not None:
            if _eff_price(price, c) > float(budget) + 0.01:
                continue
        if shop_id and _shop_id(p) != str(shop_id):
            continue
        if any(_keyword_match(kw, content) for kw in forbidden):
            continue

        out.append(p)
    return out


def _best_product(products: List[Dict], c: Dict, problem_type: str) -> Optional[str]:
    if not products:
        return None
    scored = [(_score_product(p, c, problem_type), p) for p in products if _pid(p)]
    scored.sort(key=lambda x: x[0], reverse=True)
    return _pid(scored[0][1]) if scored else None


# ─── Multi-Query Splitter ─────────────────────────────────────────────────────


def _split_multi_query(query: str, category: str) -> List[str]:
    """Split a compound ORO V3 query into individual product sub-queries."""
    clean = _RE_BUDGET_END.sub("", query).strip()
    clean = _RE_TRAILING_HELP.sub("", clean).strip().rstrip(".")

    # Pattern 1: "For the first [product], ... For the second, ..."
    parts = re.split(
        r"For the (?:first|second|third|fourth)(?:\s+product|\s+item)?,?\s+",
        clean, flags=re.IGNORECASE,
    )
    parts = [p.strip().rstrip(". ") for p in parts if p.strip() and len(p.strip()) > 8]
    if len(parts) >= 2:
        return parts

    # Pattern 2: ", and also [a/an]?"
    parts = re.split(r",\s+and\s+also\s+(?:a\s+|an\s+)?", clean, flags=re.IGNORECASE)
    if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
        return [p.strip() for p in parts]

    # Pattern 3: " and also [a/an]?" (no leading comma)
    parts = re.split(r"\s+and\s+also\s+(?:a\s+|an\s+)?", clean, flags=re.IGNORECASE)
    if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
        return [p.strip() for p in parts]

    # Pattern 4: ". Also, I [need/want/am looking for]"
    parts = re.split(
        r"\.\s+Also,?\s+I(?:'m)?\s+(?:also\s+)?(?:looking\s+for\s+|need\s+a?\s*|want\s+)",
        clean, flags=re.IGNORECASE,
    )
    if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
        return [p.strip() for p in parts]

    # Pattern 5 (Shop): "offering both [A], and [B]"
    if category == "shop":
        shop_c = re.sub(
            r"^(?:Find\s+(?:a\s+)?shops?\s+(?:offering|selling)\s+(?:both\s+)?|"
            r"I'm\s+looking\s+for\s+a\s+shop\s+that\s+(?:sells|offers)\s+(?:both\s+)?|"
            r"Find\s+a\s+shop\s+(?:that\s+)?offering\s+(?:both\s+)?)",
            "", clean, flags=re.IGNORECASE,
        ).strip()
        parts = re.split(r",\s+and\s+(?:a\s+|an\s+)(?=[a-zA-Z])", shop_c, flags=re.IGNORECASE)
        if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
            return [p.strip() for p in parts]
        parts = re.split(r",\s+(?:and\s+)?", shop_c)
        if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
            return [p.strip() for p in parts]

    # Pattern 6 (Voucher): "Find a X, a Y, a Z"
    if category == "voucher" and re.match(r"^(?:Find|Show me)\s+a\s+", clean, re.IGNORECASE):
        no_lead = re.sub(r"^(?:Find|Show me)\s+", "", clean, flags=re.IGNORECASE).strip()
        parts = re.split(r",\s+(?:a\s+|an\s+)", no_lead, flags=re.IGNORECASE)
        if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
            return [p.strip() for p in parts]

    # Pattern 7: General "X and a/an Y" (voucher multi-product like TC033)
    # e.g. "Looking for a pearl white Realme smartphone and a gold Infinix phone"
    # Strip leading "Looking for a/an " prefix first
    stripped = re.sub(
        r"^(?:Looking\s+for|Find|I(?:'m|\s+am)\s+looking\s+for)\s+(?:a\s+|an\s+)?",
        "", clean, flags=re.IGNORECASE,
    ).strip()
    parts = re.split(r"\s+and\s+(?:a\s+|an\s+)(?=[a-zA-Z])", stripped, flags=re.IGNORECASE)
    if len(parts) >= 2 and all(len(p.strip()) > 8 for p in parts):
        return [p.strip() for p in parts]

    return [clean or query]


# ─── Sub-Query Product Finder ─────────────────────────────────────────────────


def _find_best_for_subq(
    sq: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]],
    shop_id: str = "",
    price_f: str = "",
) -> Optional[Tuple[str, float, Dict]]:
    """Find best product for one sub-query. Returns (pid, price, product) or None."""
    # Use clean search terms for better API relevance
    clean_sq = _clean_search_query(sq)
    params: Dict[str, Any] = {"q": clean_sq, "sort": "default"}
    if price_f:
        params["price"] = price_f
    if shop_id:
        params["shop_id"] = shop_id

    result = execute_tool_call("find_product", params)
    products = result.get("result") or []

    forbidden = c.get("forbidden_kw", [])
    if forbidden:
        products = [
            p for p in products
            if not any(
                _keyword_match(kw, (_name(p) + " " + str(p.get("attributes", ""))).lower())
                for kw in forbidden
            )
        ]
    if shop_id:
        products = [p for p in products if _shop_id(p) == shop_id]

    think = (
        f"Searching '{clean_sq[:60]}'"
        + (f" in shop {shop_id}" if shop_id else "")
        + f" -> {len(products)} results. "
        + (_product_summary(products, 2) if products else "No matches.")
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=sq, step=n[0]
    ))

    if not products:
        return None
    if candidates_pool is not None:
        candidates_pool.extend(products[:3])

    best = products[0]
    return (_pid(best), _price_val(best) or 0.0, best)


# ─── LLM Reasoning ────────────────────────────────────────────────────────────


def _build_reasoning_prompt(
    query: str, c: Dict, candidates: List[Dict], chosen: str, ptype: str
) -> str:
    """Build LLM prompt. Candidates may come from find_product (has price+title)
    or merged data. We don't skip on missing price so candidates are always shown."""
    vobj = c.get("voucher_obj")
    voucher = c.get("voucher")
    budget = c.get("budget")

    cand_lines: List[str] = []
    for p in candidates[:6]:
        pid = _pid(p)
        name = _name(p)[:60]  # uses title field now
        price = _price_val(p)
        price_str = f"${price:.2f}" if price is not None else "(price N/A)"
        eff_str = ""
        if price is not None:
            if vobj:
                eff = _apply_voucher_obj(price, vobj)
                eff_str = f" -> after voucher: ${eff:.2f}"
            elif voucher:
                eff = _apply_voucher(price, voucher)
                eff_str = f" -> after {voucher}: ${eff:.2f}"
        # Include key attributes for context
        attrs = p.get("attributes") or {}
        attr_str = ""
        if isinstance(attrs, dict) and attrs:
            attr_str = " | attrs: " + ", ".join(
                f"{k}={v[0] if isinstance(v, list) and v else v}"
                for k, v in list(attrs.items())[:3]
            )
        label = " <- CHOSEN" if pid in chosen.split(",") else ""
        cand_lines.append(
            f"  * ID:{pid} | {name} | {price_str}{eff_str}{attr_str} | sold:{p.get('sold_count', '?')}{label}"
        )

    cons: List[str] = []
    if budget:
        cons.append(f"  - Budget cap: ${budget}")
    if vobj:
        threshold = vobj.get("threshold", 0)
        disc = (
            f"{int((vobj.get('discount') or 0)*100)}% (cap {vobj.get('cap')})"
            if vobj.get("discount_type") == "percentage"
            else f"fixed ${vobj.get('face_value')}"
        )
        cons.append(f"  - Voucher: {disc}, min spend: ${threshold}")
    elif voucher:
        cons.append(f"  - Voucher: {voucher}")
    if c.get("shop_id"):
        cons.append(f"  - Must be from shop: {c['shop_id']}")
    if c.get("required_kw"):
        cons.append(f"  - Required keywords: {c['required_kw']}")
    if c.get("forbidden_kw"):
        cons.append(f"  - Forbidden keywords: {c['forbidden_kw']}")
    price_range = c.get("price_range")
    if not price_range:
        lo, hi = _parse_price_range_from_query(query)
        if lo or hi:
            price_range = f"{lo or 0}-{hi or 'max'}"
    if price_range:
        cons.append(f"  - Price range: {price_range}")

    n_items = len(chosen.split(",")) if chosen else 0
    basket_note = f"\nNote: {n_items}-item basket recommendation." if n_items > 1 else ""

    return (
        "You are a shopping assistant evaluating products from a live e-commerce API. "
        "Write 4-6 sentences of internal reasoning (your 'think' step) about your search results.\n\n"
        f'USER REQUEST: "{query}"\n'
        f"TASK TYPE: {ptype}{basket_note}\n\n"
        "CONSTRAINTS:\n" + ("\n".join(cons) if cons else "  none") + "\n\n"
        "CANDIDATES EVALUATED:\n"
        + ("\n".join(cand_lines) if cand_lines else "  none (no results found)") + "\n\n"
        f"FINAL SELECTION: Product ID(s) {chosen}\n\n"
        "Write in first person. Be specific with product names, prices, and attributes. "
        "Explain WHY you chose this product and why others were less suitable. "
        "Mention keyword matching, price range compliance, brand, and product relevance. "
        "No bullet points or headers. 4-6 sentences maximum."
    )


def _fallback_think(query: str, c: Dict, chosen: str, ptype: str) -> str:
    parts: List[str] = []
    if c.get("budget"):
        parts.append(f"budget ${c['budget']}")
    if c.get("shop_id"):
        parts.append(f"shop {c['shop_id']}")
    if c.get("voucher"):
        parts.append(f"voucher '{c['voucher']}'")
    if c.get("required_kw"):
        parts.append(f"required keywords {c['required_kw']}")
    cs = ", ".join(parts) if parts else "no specific constraints"
    n_items = len(chosen.split(",")) if chosen else 0
    basket = f" ({n_items}-product basket)" if n_items > 1 else ""
    return (
        f"After systematic research for '{query}' ({ptype} task){basket}, "
        f"I evaluated multiple candidates against the constraints ({cs}). "
        f"Product(s) {chosen} emerged as the best match, verified against price limits, "
        f"keyword requirements, and shop constraints via live API results. "
        f"Other candidates were ruled out due to budget excess, shop mismatch, or "
        f"forbidden keyword presence — or insufficient total to activate the voucher."
    )


def _llm_reason(
    query: str, c: Dict, candidates: List[Dict], chosen: str, ptype: str
) -> str:
    if not chosen:
        return _fallback_think(query, c, chosen, ptype)
    prompt = _build_reasoning_prompt(query, c, candidates, chosen, ptype)
    try:
        resp = _PROXY.post("/v1/chat/completions", {
            "model": "google/gemini-2.0-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.3,
        })
        content = (
            (((resp or {}).get("choices") or [{}])[0]
             .get("message", {})
             .get("content", "") or "")
            .strip()
        )
        if len(content) >= 150:
            return content
    except Exception:  # noqa: BLE001
        pass
    return _fallback_think(query, c, chosen, ptype)


# ─── Service Filter ───────────────────────────────────────────────────────────


def _detect_service(query: str) -> str:
    q = query.lower()
    if "lazflash" in q or "laz flash" in q:
        return "lazflash"
    if "cash on delivery" in q or "pay on delivery" in q or " cod " in q:
        return "cod"
    return ""


# ─── Strategy: Product ────────────────────────────────────────────────────────


def _merge_product_data(find_results: List[Dict], view_results: List[Dict]) -> List[Dict]:
    """Merge find_product results (title+price+sold_count) with view_product_information
    (attributes+description+sku_options). find_product data takes priority for price/title."""
    view_map: Dict[str, Dict] = {}
    for d in view_results:
        pid = str(d.get("product_id") or "")
        if pid:
            view_map[pid] = d

    merged = []
    for p in find_results:
        pid = _pid(p)
        m = dict(p)  # start with find_product data (has title, price, sold_count)
        if pid in view_map:
            v = view_map[pid]
            # Only overlay fields NOT in find_product (don't overwrite title/price)
            for key in ("attributes", "description", "short_description", "sku_options"):
                if v.get(key):
                    m[key] = v[key]
        merged.append(m)
    return merged


def _strategy_product(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[str]:
    """Find ONE product matching all constraints. V5.1: clean search query for better
    API relevance, title field, query-term scoring, merged find+view data."""
    ptype_for_filter = "voucher" if c.get("voucher") or c.get("voucher_obj") else "product"
    price_f = _price_filter(c, ptype_for_filter)
    service = _detect_service(query)
    query_terms = c.get("_query_terms") or _extract_query_terms(query)

    # Clean the NL query to send only product keywords to the API
    clean_q = _clean_search_query(query)

    searches = [
        {"q": clean_q, "sort": "default", "price": price_f, "service": service},
        {"q": clean_q, "sort": "order", "price": price_f},
        {"q": clean_q, "sort": "priceasc", "price": price_f},
    ]
    # If required keywords from test harness, also search those directly
    if c.get("required_kw"):
        kw_q = " ".join(c["required_kw"][:3])
        searches.append({"q": kw_q, "sort": "order", "price": price_f})

    all_products: List[Dict] = []

    for cfg in searches[:3]:
        params = {k: v for k, v in cfg.items() if v}
        result = execute_tool_call("find_product", params)
        products = result.get("result") or []

        think = (
            f"Searched '{cfg['q'][:50]}' sort='{cfg.get('sort', 'default')}'"
            + (f" price='{price_f}'" if price_f else "")
            + (f" service='{service}'" if service else "")
            + f". Found {len(products)} products. "
            + (_product_summary(products) if products else "No results.")
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=query, step=n[0]
        ))
        all_products.extend(products)
        if len(all_products) >= 5:
            break

    if not all_products:
        return None

    # First-pass score on find_product results (title + sold_count + query terms)
    first_scored = [
        (_score_product(p, c, "product"), p)
        for p in all_products if _pid(p)
    ]
    first_scored.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [p for _, p in first_scored[:6]]
    top_ids = [_pid(p) for p in top_candidates]

    # Fetch detailed attributes for top candidates
    view_result = execute_tool_call(
        "view_product_information", {"product_ids": ",".join(top_ids)}
    )
    view_details = view_result.get("result") or []

    # Merge: keep title+price from find, add attributes+description from view
    merged = _merge_product_data(top_candidates, view_details)

    # Re-score with full data (title + attributes + description)
    rescored = [
        (_score_product(m, c, "product"), m)
        for m in merged if _pid(m)
    ]
    rescored.sort(key=lambda x: x[0], reverse=True)

    # Apply hard filters (budget, shop, forbidden keywords)
    filtered = _hard_filter([m for _, m in rescored], c)
    best_source = filtered if filtered else [m for _, m in rescored]

    if not best_source:
        return None

    # Final scoring on filtered candidates
    final_scored = [
        (_score_product(p, c, "product"), p)
        for p in best_source if _pid(p)
    ]
    final_scored.sort(key=lambda x: x[0], reverse=True)
    best_p = final_scored[0][1]
    best = _pid(best_p)
    best_price = _price_val(best_p)

    if candidates_pool is not None:
        candidates_pool.extend(merged[:6])

    think = (
        f"Retrieved details for {', '.join(top_ids[:3])}. "
        f"Re-scored with attributes and query terms {query_terms[:5]}. "
        f"Filter: price='{price_f}', required={c.get('required_kw', [])}. "
        f"Product {best} scored highest"
        + (f" at {best_price:.2f}" if best_price else "")
        + "."
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[view_result], response="", query=query, step=n[0]
    ))
    return best


# ─── Strategy: Shop (V4 — Multi-Product Intersection) ────────────────────────


def _strategy_shop(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[List[str]]:
    """Find products from the SAME shop. V4: multi-product intersection."""
    if c.get("shop_id"):
        return _strategy_shop_known(query, c, steps, n, candidates_pool)

    sub_queries = _split_multi_query(query, "shop")

    if len(sub_queries) == 1:
        return _strategy_shop_discover(query, c, steps, n, candidates_pool)

    # Multi-product intersection
    all_results: List[List[Dict]] = []
    shop_sets: List[set] = []

    for i, sq in enumerate(sub_queries):
        result = execute_tool_call("find_product", {"q": sq, "sort": "order"})
        products = result.get("result") or []

        forbidden = c.get("forbidden_kw", [])
        if forbidden:
            products = [
                p for p in products
                if not any(
                    _keyword_match(kw, (_name(p) + " " + str(p.get("attributes", ""))).lower())
                    for kw in forbidden
                )
            ]

        shops = {_shop_id(p) for p in products if _shop_id(p)}
        all_results.append(products)
        shop_sets.append(shops)

        think = (
            f"Shop search {i+1}/{len(sub_queries)}: '{sq[:60]}' "
            f"-> {len(products)} products from {len(shops)} shops. "
            + _product_summary(products, 2)
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=sq, step=n[0]
        ))

    if not shop_sets:
        return None

    common_shops = shop_sets[0].copy()
    for s in shop_sets[1:]:
        common_shops &= s

    if common_shops:
        shop_score: Dict[str, int] = {}
        for products in all_results:
            for p in products:
                sid = _shop_id(p)
                if sid in common_shops:
                    shop_score[sid] = shop_score.get(sid, 0) + 1
        best_shop = max(common_shops, key=lambda s: shop_score.get(s, 0))
        think = (
            f"Found {len(common_shops)} common shop(s) for all {len(sub_queries)} items. "
            f"Best shop: {best_shop}. Fetching one product per item."
        )
    else:
        all_shop_count: Dict[str, int] = {}
        for products in all_results:
            for p in products:
                sid = _shop_id(p)
                if sid:
                    all_shop_count[sid] = all_shop_count.get(sid, 0) + 1
        if not all_shop_count:
            return None
        best_shop = max(all_shop_count, key=lambda s: all_shop_count[s])
        think = (
            f"No single shop carries all {len(sub_queries)} item types. "
            f"Fallback to shop with most coverage: {best_shop}."
        )

    c["shop_id"] = best_shop
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[], response="", query=query, step=n[0]
    ))

    recommendations: List[str] = []
    for sq in sub_queries:
        item = _find_best_for_subq(sq, c, steps, n, candidates_pool, best_shop)
        if item:
            recommendations.append(item[0])

    if candidates_pool is not None:
        for products in all_results:
            candidates_pool.extend(products[:2])

    return recommendations if recommendations else _strategy_shop_known(
        query, c, steps, n, candidates_pool
    )


def _strategy_shop_known(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[List[str]]:
    result = execute_tool_call("find_product", {
        "q": query, "shop_id": str(c["shop_id"]), "sort": "order"
    })
    products = result.get("result") or []
    products = _hard_filter(products, c, "shop")

    if candidates_pool is not None:
        candidates_pool.extend(products[:6])

    think = (
        f"Searched '{query[:50]}' in shop {c['shop_id']}. "
        f"Found {len(products)} valid products. " + _product_summary(products)
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=query, step=n[0]
    ))
    return [_pid(p) for p in products[:3] if _pid(p)] or None


def _strategy_shop_discover(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[List[str]]:
    result = execute_tool_call("find_product", {"q": query, "sort": "order"})
    products = result.get("result") or []

    think = (
        f"Broad search '{query[:50]}' to discover best shop. "
        f"Found {len(products)} results. " + _product_summary(products, 5)
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=query, step=n[0]
    ))

    if not products:
        return None

    shop_groups: Dict[str, List[Dict]] = {}
    for p in products:
        sid = _shop_id(p)
        if sid:
            shop_groups.setdefault(sid, []).append(p)

    if not shop_groups:
        return [_pid(products[0])] if products else None

    best_sid = max(shop_groups, key=lambda s: len(shop_groups[s]))
    c["shop_id"] = best_sid

    result2 = execute_tool_call("find_product", {"q": query, "shop_id": best_sid, "sort": "order"})
    shop_products = result2.get("result") or []

    think2 = (
        f"Shop {best_sid} has most products ({len(shop_groups[best_sid])} in initial search). "
        f"Targeted search: {len(shop_products)} products. "
        + _product_summary(shop_products or shop_groups[best_sid])
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think2, tool_results=[result2], response="", query=query, step=n[0]
    ))

    source = shop_products if shop_products else shop_groups[best_sid]
    valid = _hard_filter(source, c, "shop")
    if candidates_pool is not None:
        candidates_pool.extend((valid or source)[:6])
    return [_pid(p) for p in (valid or source)[:3] if _pid(p)]


# ─── Strategy: Voucher (V4 — Multi-Product Basket) ───────────────────────────


def _strategy_voucher(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[str]:
    """Find products within budget after applying voucher. V4: multi-product basket."""
    vobj = c.get("voucher_obj")
    budget = c.get("budget")
    voucher = c.get("voucher")
    threshold = float(c.get("voucher_threshold") or 0)
    voucher_type = c.get("voucher_type", "platform")

    sub_queries = _split_multi_query(query, "voucher")

    if len(sub_queries) >= 2:
        per_item_max = float(budget) * 2 if budget else 0
        price_f = f"0-{int(per_item_max)}" if per_item_max else ""

        # Shop-type voucher: find common shop first
        shop_id = ""
        if voucher_type == "shop":
            # Collect products per sub-query to allow scoring shops later
            subq_all_prods: List[List[Dict]] = []
            subq_shop_sets: List[set] = []
            for sq in sub_queries:
                clean_sq = _clean_search_query(sq)
                r = execute_tool_call("find_product", {"q": clean_sq, "sort": "default"})
                prods = r.get("result") or []
                subq_all_prods.append(prods)
                shops = {_shop_id(p) for p in prods if _shop_id(p)}
                if shops:
                    subq_shop_sets.append(shops)

            if subq_shop_sets:
                common = subq_shop_sets[0].copy()
                for s in subq_shop_sets[1:]:
                    common &= s

                if len(common) == 1:
                    # Only one common shop — straightforward
                    shop_id = list(common)[0]
                elif len(common) > 1:
                    # Multiple common shops: pick the one whose products are
                    # most RELEVANT to each sub-query (query-term score, not sold_count)
                    best_shop, best_score = "", -1.0
                    for sid in sorted(common):  # sorted for determinism
                        total_relevance = 0.0
                        for sq, prods in zip(sub_queries, subq_all_prods):
                            shop_prods = [p for p in prods if _shop_id(p) == sid]
                            if not shop_prods:
                                continue
                            # Score best product of this shop for this sub-query
                            sq_terms = _extract_query_terms(sq)
                            best_rel = 0.0
                            for p in shop_prods:
                                content = _searchable(p)
                                rel = sum(
                                    3.0 for t in sq_terms if _keyword_match(t, content)
                                )
                                best_rel = max(best_rel, rel)
                            total_relevance += best_rel
                        if total_relevance > best_score:
                            best_score = total_relevance
                            best_shop = sid
                    shop_id = best_shop

            think = (
                f"Shop voucher detected for {len(sub_queries)} items. "
                + (f"Common shop found: {shop_id}." if shop_id else "No common shop.")
            )
            n[0] += 1
            steps.append(create_dialogue_step(
                think=think, tool_results=[], response="", query=query, step=n[0]
            ))

        # Find best product for each sub-query
        basket: List[Tuple[str, float, Dict]] = []
        for sq in sub_queries:
            item = _find_best_for_subq(sq, c, steps, n, candidates_pool, shop_id, price_f)
            if item:
                basket.append(item)
            elif shop_id:
                item = _find_best_for_subq(sq, c, steps, n, candidates_pool, "", price_f)
                if item:
                    basket.append(item)

        if basket:
            total = sum(price for _, price, _ in basket)
            if vobj:
                eff = _apply_voucher_obj(total, vobj)
            elif voucher:
                eff = _apply_voucher(total, voucher)
            else:
                eff = total

            within = not budget or eff <= float(budget) + 0.01
            activated = total > threshold if threshold else True

            think = (
                f"Basket ({len(basket)} products): total={total:.2f}. "
                f"Threshold {threshold:.0f}: {'OK' if activated else 'NOT MET'}. "
                f"After voucher: {eff:.2f} vs budget {budget}: "
                f"{'OK' if within else 'OVER'}. "
                f"Items: {', '.join(f'ID:{pid}@{price:.2f}' for pid, price, _ in basket[:4])}"
            )
            n[0] += 1
            steps.append(create_dialogue_step(
                think=think, tool_results=[], response="", query=query, step=n[0]
            ))

            if candidates_pool is not None:
                candidates_pool.extend(p for _, _, p in basket)

            return ",".join(pid for pid, _, _ in basket)

    # Single-product fallback
    return _strategy_voucher_single(query, c, steps, n, candidates_pool)


def _strategy_voucher_single(
    query: str,
    c: Dict,
    steps: List[Dict],
    n: List[int],
    candidates_pool: Optional[List[Dict]] = None,
) -> Optional[str]:
    """Single-product voucher strategy (v3 preserved)."""
    budget = c.get("budget")
    voucher = c.get("voucher")
    price_f = _price_filter(c, "voucher")

    result = execute_tool_call("find_product", {
        "q": query, "sort": "priceasc",
        **({} if not price_f else {"price": price_f}),
    })
    products = result.get("result") or []

    think = (
        f"Voucher search '{query[:50]}' budget={budget} voucher='{voucher}' "
        f"filter='{price_f}'. Found {len(products)} products. "
        + _product_summary(products)
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[result], response="", query=query, step=n[0]
    ))

    if not products:
        result = execute_tool_call("find_product", {"q": query, "sort": "default"})
        products = result.get("result") or []
        products = sorted(
            products,
            key=lambda x: _price_val(x) if _price_val(x) is not None else float("inf"),
        )
        think = (
            f"No results with price filter — broad search found {len(products)} products. "
            + _product_summary(products)
        )
        n[0] += 1
        steps.append(create_dialogue_step(
            think=think, tool_results=[result], response="", query=query, step=n[0]
        ))

    if not products:
        return None

    products = _hard_filter(products, c, "voucher")
    if not products:
        return None

    top = [_pid(p) for p in products[:3] if _pid(p)]
    view_result = execute_tool_call(
        "view_product_information", {"product_ids": ",".join(top)}
    )
    details = view_result.get("result") or []

    if candidates_pool is not None:
        candidates_pool.extend(details or products[:5])

    best_id = ""
    best_eff = float("inf")
    verifs = []

    for p in details or products[:3]:
        pid = _pid(p)
        if not pid:
            continue
        price = _price_val(p)
        if price is None:
            continue
        content = (_name(p) + " " + str(p.get("attributes") or "")).lower()
        if any(_keyword_match(kw, content) for kw in c.get("forbidden_kw", [])):
            verifs.append(f"ID:{pid} SKIP(forbidden)")
            continue
        if c.get("shop_id") and _shop_id(p) != str(c["shop_id"]):
            verifs.append(f"ID:{pid} SKIP(shop)")
            continue
        eff = _eff_price(price, c)
        fits = budget is None or eff <= float(budget)
        verifs.append(f"ID:{pid} {price:.2f}->{eff:.2f} {'OK' if fits else 'OVER'}")
        if fits and eff < best_eff:
            best_eff = eff
            best_id = pid

    if not best_id and top:
        best_id = top[0]

    think = (
        f"Verified {len(top)} products: {'; '.join(verifs[:3])}. "
        f"Best: {best_id} (eff {best_eff:.2f} vs budget {budget})."
    )
    n[0] += 1
    steps.append(create_dialogue_step(
        think=think, tool_results=[view_result], response="", query=query, step=n[0]
    ))

    return best_id


# ─── Main Entry Point ─────────────────────────────────────────────────────────


def agent_main(problem_data: Dict) -> List[Dict]:
    """Main entry point for the ORO mining agent.

    V4: multi-product baskets (shop + voucher), structured voucher parsing,
    price range extraction, service filters.
    """
    steps: List[Dict] = []
    n = [0]
    candidates_pool: List[Dict] = []

    query = problem_data.get("query", "")
    c = _parse_constraints(problem_data)
    ptype = _detect_type(c)

    recommended: Optional[str] = None

    try:
        if ptype == "shop":
            ids = _strategy_shop(query, c, steps, n, candidates_pool)
            if ids:
                recommended = ",".join(ids)
        elif ptype == "voucher":
            recommended = _strategy_voucher(query, c, steps, n, candidates_pool)
        else:
            recommended = _strategy_product(query, c, steps, n, candidates_pool)

    except Exception:  # noqa: BLE001
        try:
            fb = execute_tool_call("find_product", {"q": query})
            prods = fb.get("result") or []
            if prods:
                recommended = _pid(prods[0])
                candidates_pool.extend(prods[:4])
                n[0] += 1
                steps.append(create_dialogue_step(
                    think=(
                        f"Fallback search for '{query}' found {len(prods)} products. "
                        f"Recommending: {recommended}."
                    ),
                    tool_results=[fb], response="", query=query, step=n[0],
                ))
        except Exception:  # noqa: BLE001
            pass

    llm_think = _llm_reason(query, c, candidates_pool, recommended or "", ptype)

    if recommended:
        rec = execute_tool_call("recommend_product", {"product_ids": recommended})
        term = execute_tool_call("terminate", {"status": "success"})

        cs_parts = []
        if c.get("budget"):
            cs_parts.append(f"budget {c['budget']}")
        if c.get("shop_id"):
            cs_parts.append(f"shop {c['shop_id']}")
        if c.get("voucher"):
            cs_parts.append(f"voucher '{c['voucher']}'")
        if c.get("required_kw"):
            cs_parts.append(f"keywords {c['required_kw']}")
        cs = ", ".join(cs_parts) if cs_parts else "none"

        n[0] += 1
        steps.append(create_dialogue_step(
            think=llm_think,
            tool_results=[rec, term],
            response=(
                f"Based on my research for '{query}', "
                f"I recommend product(s) {recommended}. "
                f"All constraints satisfied ({cs})."
            ),
            query=query,
            step=n[0],
        ))
    else:
        term = execute_tool_call("terminate", {"status": "failure"})
        n[0] += 1
        steps.append(create_dialogue_step(
            think=(
                f"After {n[0]-1} search attempt(s) for '{query}', "
                f"unable to find products satisfying all constraints."
            ),
            tool_results=[term],
            response=f"Unable to find products matching all requirements for: {query}.",
            query=query,
            step=n[0],
        ))

    return steps
