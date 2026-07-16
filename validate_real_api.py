#!/usr/bin/env python3
"""
validate_real_api.py — Validateur contre l'API ORO réelle (suite 3)
=====================================================================
Contrairement à test_harness.py (qui mocke tout), ce script :
  1. Audit statique complet (modèle, endpoint, LLM, imports dangereux)
  2. Charge les 30 vrais problèmes depuis l'API ORO publique
  3. Exécute l'agent avec de VRAIES requêtes find_product / view_product
  4. Vérifie que les recommandations correspondent aux vraies réponses attendues

Usage:
    python3 validate_real_api.py              # Audit + 30 problèmes réels
    python3 validate_real_api.py --audit-only # Audit statique uniquement
    python3 validate_real_api.py --cat product   # Seulement Product
    python3 validate_real_api.py --n 5           # 5 premiers seulement
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import types
import urllib.parse
import urllib.request

# ── Config ───────────────────────────────────────────────────────────────────

SUITE_ID = 3
PROBLEMS_URL = f"https://api.oroagents.com/v1/public/suites/{SUITE_ID}/problems"

ALLOWED_MODELS = [
    "deepseek-ai/DeepSeek-V3.2-TEE", "deepseek-ai/DeepSeek-V3.1-TEE",
    "deepseek-ai/DeepSeek-V3-0324-TEE", "deepseek-ai/DeepSeek-R1-0528-TEE",
    "Qwen/Qwen3-32B-TEE", "Qwen/Qwen3.5-397B-A17B-TEE", "Qwen/Qwen3.6-27B-TEE",
    "google/gemma-4-31B-turbo-TEE", "zai-org/GLM-5-TEE", "zai-org/GLM-5.1-TEE",
    "moonshotai/Kimi-K2.5-TEE", "moonshotai/Kimi-K2.6-TEE",
    "MiniMaxAI/MiniMax-M2.5-TEE", "XiaomiMiMo/MiMo-V2-Flash-TEE",
    "openai/gpt-oss-120b-TEE",
]


# ── Real ProxyClient (hits ORO public search API) ────────────────────────────

class RealProxyClient:
    """Uses the real ORO public search endpoints."""

    ENDPOINTS = {
        "find_product": "https://api.oroagents.com/v1/public/search/find_product",
        "view_product": "https://api.oroagents.com/v1/public/search/view_product_information",
    }

    def __init__(self, timeout=25, max_retries=1):
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls = []

    def _get_url(self, path):
        for k, url in self.ENDPOINTS.items():
            if k in path:
                return url
        return None

    def get(self, path: str, params: dict = None) -> list:
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = self._get_url(path)
        if not url:
            self.calls.append({"path": path, "status": "unknown_path"})
            return []

        qs = urllib.parse.urlencode(params)
        full_url = f"{url}?{qs}" if qs else url

        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(full_url, headers={"Accept": "application/json", "User-Agent": "oro-agent-validator/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                    result = data if isinstance(data, list) else []
                    self.calls.append({"path": path, "params": params, "status": "ok", "n": len(result)})
                    return result
            except urllib.error.HTTPError as e:
                self.calls.append({"path": path, "params": params, "status": f"http_{e.code}"})
                if e.code in (429, 503) and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return []
            except Exception as ex:
                self.calls.append({"path": path, "params": params, "status": f"err:{str(ex)[:50]}"})
                if attempt < self.max_retries:
                    time.sleep(1)
        return []

    def post(self, path: str, data: dict = None) -> dict:
        # LLM calls need the sandbox proxy — mock locally with a valid structure
        self.calls.append({"path": path, "status": "mocked_llm"})
        return {
            "choices": [{
                "message": {
                    "content": (
                        "I carefully analyzed the available search results for this query. "
                        "The selected product best matches the user's requirements based on "
                        "keyword alignment, price range compliance, and product attributes. "
                        "Alternative candidates were eliminated due to constraint violations "
                        "or insufficient relevance scores."
                    )
                }
            }]
        }


# ── Fake agent_interface ──────────────────────────────────────────────────────

_registered_tools = {}


def _tool_decorator(fn):
    _registered_tools[fn.__name__] = fn
    return fn


def _execute_tool_call(tool_name: str, parameters: dict) -> dict:
    fn = _registered_tools.get(tool_name)
    if not fn:
        return {"name": tool_name, "result": None, "error": "Tool not found"}
    try:
        result = fn(**parameters)
        return {"name": tool_name, "result": result, "error": None}
    except Exception as e:
        return {"name": tool_name, "result": None, "error": str(e)}


def _create_dialogue_step(think, tool_results, response, query, step):
    return {
        "completion": {
            "reasoning_content": think,
            "content": f"<think>{think}</think>\n<tool_call>{json.dumps(tool_results)}</tool_call>\n<response>{response}</response>",
            "message": {"think": think, "tool_call": tool_results, "response": response},
        },
        "extra_info": {"step": step, "query": query, "timestamp": int(time.time() * 1000)},
    }


# ── Load agent ────────────────────────────────────────────────────────────────

def load_agent(proxy_client):
    _registered_tools.clear()

    ai_mod = types.ModuleType("src.agent.agent_interface")
    ai_mod.Tool = _tool_decorator
    ai_mod.execute_tool_call = _execute_tool_call
    ai_mod.create_dialogue_step = _create_dialogue_step
    sys.modules["src.agent.agent_interface"] = ai_mod

    pc_mod = types.ModuleType("src.agent.proxy_client")

    def _build_proxy(**kw):
        return proxy_client

    pc_mod.ProxyClient = _build_proxy
    sys.modules["src.agent.proxy_client"] = pc_mod

    # Remove cached module if reloading
    if "agent_real" in sys.modules:
        del sys.modules["agent_real"]

    agent_path = os.path.join(os.path.dirname(__file__), "agent.py")
    spec = importlib.util.spec_from_file_location("agent_real", agent_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.agent_main


# ── Extract recommendations ───────────────────────────────────────────────────

def extract_recommended_ids(steps: list) -> list:
    ids = []
    for step in steps:
        msg = step.get("completion", {}).get("message", {})
        for tr in (msg.get("tool_call") or []):
            if isinstance(tr, dict) and tr.get("name") == "recommend_product":
                result_str = str(tr.get("result") or "")
                m = re.search(r"user:\s*(.+)", result_str)
                if m:
                    ids.extend([x.strip() for x in m.group(1).split(",")])
    return ids


# ── Static audit ──────────────────────────────────────────────────────────────

def run_audit() -> bool:
    print("\n" + "=" * 70)
    print("  AUDIT STATIQUE agent.py")
    print("=" * 70)

    with open("agent.py") as f:
        code = f.read()

    import ast as ast_mod
    errors = []
    warnings = []

    def check(ok, msg_ok, msg_fail, fatal=True):
        if ok:
            print(f"✅ {msg_ok}")
        else:
            print(f"❌ {msg_fail}")
            if fatal:
                errors.append(msg_fail)
            else:
                warnings.append(msg_fail)

    # Syntaxe
    try:
        ast_mod.parse(code)
        print("✅ Syntaxe Python OK")
    except SyntaxError as e:
        errors.append(f"Syntaxe: {e}")
        print(f"❌ Syntaxe: {e}")

    # Taille
    size_kb = len(code.encode()) / 1024
    check(size_kb < 1024, f"Taille OK ({size_kb:.0f} KB)", f"Trop grand ({size_kb:.0f} KB > 1MB)")

    # agent_main
    check("def agent_main" in code, "agent_main() présent", "agent_main() INTROUVABLE")

    # Endpoint LLM
    ep_match = re.search(r'_LLM_ENDPOINT\s*=\s*["\']([^"\']+)["\']', code)
    if ep_match:
        ep = ep_match.group(1)
        check(ep == "/inference/chat/completions",
              f"LLM endpoint correct: '{ep}'",
              f"LLM endpoint INCORRECT: '{ep}' (doit être /inference/chat/completions)")
    else:
        warnings.append("_LLM_ENDPOINT non trouvé (LLM peut ne pas être appelé)")
        print("⚠️  _LLM_ENDPOINT non trouvé dans le code")

    # Modèle allowlisté
    model_match = re.search(r'_LLM_MODEL\s*=\s*["\']([^"\']+)["\']', code)
    if model_match:
        model = model_match.group(1)
        check(model in ALLOWED_MODELS,
              f"LLM model allowlisté: '{model}'",
              f"LLM model NON ALLOWLISTÉ: '{model}' → 403 en prod → Gate 1 FAIL")
    else:
        warnings.append("_LLM_MODEL non trouvé")
        print("⚠️  _LLM_MODEL non trouvé dans le code")

    # LLM utilisé dans les 2 branches
    n_llm_think = code.count("think=llm_think")
    check(n_llm_think >= 2,
          f"think=llm_think utilisé {n_llm_think}x (success + failure)",
          f"think=llm_think utilisé seulement {n_llm_think}x (manque la branche failure)")

    # max_tokens
    tok_match = re.search(r'"max_tokens"\s*:\s*(\d+)', code)
    if tok_match:
        tok = int(tok_match.group(1))
        check(tok >= 50,
              f"max_tokens={tok} (suffisant pour Gate 1)",
              f"max_tokens={tok} trop faible (Gate 1 besoin >= 30 completion_tokens)")

    # Bypass LLM (early return avant appel)
    has_bypass = bool(re.search(r'if not chosen:\s*\n\s*return _fallback', code))
    check(not has_bypass,
          "Pas de bypass LLM (early return supprimé)",
          "BYPASS LLM détecté: 'if not chosen: return _fallback' → Gate 1 fail si no results")

    # Imports dangereux (règles ORO Code Requirements)
    DANGEROUS = ["base64", "binascii", "codecs", "zlib", "subprocess",
                 "os.system", "eval(", "exec(", "__import__"]
    found_dangerous = [d for d in DANGEROUS if d in code]
    check(not found_dangerous,
          "Aucun import dangereux",
          f"Imports dangereux: {found_dangerous} → rejet immédiat ORO")

    # IDs hardcodés
    # Only flag 10-13 digit numbers not in comments/strings that look like timestamps
    product_ids = re.findall(r'(?<!["\'\w])\b(\d{10,13})\b(?!["\'\w])', code)
    suspicious = [x for x in product_ids if not x.startswith("202")]
    check(not suspicious,
          "Pas d'IDs produits hardcodés",
          f"IDs numériques suspects: {suspicious[:5]}",
          fatal=False)

    print()
    if errors:
        print(f"🔴 {len(errors)} ERREUR(S) BLOQUANTE(S) — NE PAS SOUMETTRE:")
        for e in errors:
            print(f"   • {e}")
    else:
        print("🟢 Audit réussi — aucune erreur bloquante")
    if warnings:
        print(f"🟡 {len(warnings)} avertissement(s):")
        for w in warnings:
            print(f"   • {w}")
    print("=" * 70 + "\n")
    return len(errors) == 0


# ── Real validation ───────────────────────────────────────────────────────────

def fetch_problems():
    req = urllib.request.Request(PROBLEMS_URL, headers={"Accept": "application/json", "User-Agent": "oro-validator/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data.get("problems", [])


def run_validation(problems: list):
    proxy = RealProxyClient()
    agent_main = load_agent(proxy)

    total = len(problems)
    passed = 0
    by_cat = {}

    print(f"{'='*70}")
    print(f"  VALIDATION RÉELLE — {total} problèmes (API ORO live)")
    print(f"{'='*70}\n")

    for i, p in enumerate(problems, 1):
        m = p.get("metadata", {})
        reward = m.get("reward") or {}
        if isinstance(reward, list):
            reward = {}

        query = m.get("query", "")
        cat = m.get("category", "?")
        diff = m.get("difficulty", "?")
        title = m.get("title", f"P{i}")
        expected_id = reward.get("product_id") if isinstance(reward, dict) else None

        print(f"[{i:02d}/{total}] {title} ({cat}/{diff})")
        print(f"  Q: {query[:100]}")

        proxy.calls = []
        t0 = time.time()

        try:
            steps = agent_main(p)
            elapsed = time.time() - t0
            rec_ids = extract_recommended_ids(steps)

            n_api_ok = sum(1 for c in proxy.calls if c.get("status") == "ok")
            n_api_fail = sum(1 for c in proxy.calls if "err" in str(c.get("status", "")) or "http" in str(c.get("status", "")))
            n_api_results = sum(c.get("n", 0) for c in proxy.calls if c.get("status") == "ok" and "find_product" in str(c.get("path", "")))

            # Score
            if expected_id and rec_ids and expected_id in rec_ids:
                score = 1.0
                match_str = "✅ MATCH"
            elif not rec_ids:
                score = 0.0
                match_str = "❌ PAS DE RECOMMANDATION"
            else:
                score = 0.0
                match_str = f"❌ MISS (attendu {expected_id}, reçu {rec_ids})"

            status_icon = "✅" if score >= 1.0 else "❌"
            print(f"  {status_icon} Steps:{len(steps)} | API:{n_api_ok} calls ({n_api_results} résultats) | {elapsed:.1f}s")
            if expected_id:
                print(f"  ID attendu: {expected_id} → {match_str}")
            elif not reward:
                print(f"  ℹ️  Pas d'ID attendu (Shop/Voucher — reward vide dans API publique)")
                score = 0.5  # neutral for shop/voucher without public answer
            print(f"  Recommandé: {rec_ids}")
            if n_api_fail:
                fail_info = [c for c in proxy.calls if "err" in str(c.get("status","")) or "http" in str(c.get("status",""))]
                print(f"  ⚠️  {n_api_fail} erreur(s) API: {fail_info[:2]}")

            if score >= 1.0:
                passed += 1
            by_cat.setdefault(cat, {"ok": 0, "total": 0})
            by_cat[cat]["total"] += 1
            if score >= 1.0:
                by_cat[cat]["ok"] += 1

        except Exception as ex:
            elapsed = time.time() - t0
            print(f"  💥 EXCEPTION: {ex}")
            by_cat.setdefault(cat, {"ok": 0, "total": 0})
            by_cat[cat]["total"] += 1

        print()

    # Summary
    product_total = by_cat.get("Product", {}).get("total", 0)
    product_ok = by_cat.get("Product", {}).get("ok", 0)

    print(f"{'='*70}")
    print(f"  RÉSULTAT GLOBAL: {product_ok}/{product_total} Product corrects (IDs vérifiables)")
    print(f"  (Shop/Voucher: rewards non exposés dans l'API publique)")
    print()
    for cat, d in sorted(by_cat.items()):
        bar = "█" * d["ok"] + "░" * (d["total"] - d["ok"])
        print(f"  {cat:10} {d['ok']}/{d['total']} [{bar}]")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validation réelle API ORO + audit statique")
    parser.add_argument("--cat", choices=["product","shop","voucher","Product","Shop","Voucher"])
    parser.add_argument("--n", type=int, help="Limiter à N problèmes")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()

    audit_ok = run_audit()
    if not audit_ok:
        print("🛑 AUDIT ÉCHOUÉ — corrige les erreurs avant de soumettre")
        sys.exit(1)

    if args.audit_only:
        print("✅ Audit uniquement demandé — fin.")
        sys.exit(0)

    print("📡 Chargement des 30 problèmes depuis l'API ORO...")
    try:
        problems = fetch_problems()
        print(f"   {len(problems)} problèmes chargés (suite {SUITE_ID})\n")
    except Exception as ex:
        print(f"❌ Impossible de charger les problèmes: {ex}")
        sys.exit(1)

    if args.id if hasattr(args, "id") else False:
        problems = [p for p in problems if p["problem_id"] == args.id]
    if args.cat:
        problems = [p for p in problems if p.get("metadata", {}).get("category", "").lower() == args.cat.lower()]
    if args.n:
        problems = problems[:args.n]

    if not problems:
        print("❌ Aucun problème sélectionné")
        sys.exit(1)

    run_validation(problems)
