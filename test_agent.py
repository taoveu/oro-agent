#!/usr/bin/env python3
"""
ORO Agent — Local Test Runner
Simulates the ORO sandbox environment for local testing.
Usage: python test_agent.py [--problem product|shop|voucher]
"""
import sys
import json
import time
import argparse
from typing import Dict, List

# ---------------------------------------------------------------------------
# Stub the ORO framework modules so we can test outside the Docker sandbox
# ---------------------------------------------------------------------------
from unittest.mock import MagicMock, patch
import types

# Create fake src.agent package
src_pkg = types.ModuleType("src")
src_agent_pkg = types.ModuleType("src.agent")
sys.modules.setdefault("src", src_pkg)
sys.modules.setdefault("src.agent", src_agent_pkg)

# ── Fake ProxyClient ────────────────────────────────────────────────────────
_MOCK_PRODUCTS = [
    {
        "product_id": "123456789",
        "name": "Red Waist Bag Motorcycle Sport",
        "price": 29900,  # in cents → 299.00
        "shop_id": "88001",
        "shop_name": "SportGearPH",
        "categories": ["bags", "motorcycle", "sport"],
    },
    {
        "product_id": "987654321",
        "name": "Black Waist Pack Outdoor",
        "price": 45000,
        "shop_id": "88001",
        "shop_name": "SportGearPH",
        "categories": ["bags", "outdoor"],
    },
    {
        "product_id": "111222333",
        "name": "Blue Running Waist Belt Bag",
        "price": 19900,
        "shop_id": "77005",
        "shop_name": "RunFastStore",
        "categories": ["bags", "running"],
    },
]

_MOCK_DETAILS = {
    "123456789": {
        "product_id": "123456789",
        "name": "Red Waist Bag Motorcycle Sport",
        "price": 29900,
        "shop_id": "88001",
        "shop_name": "SportGearPH",
        "description": "Durable waist bag for motorcycle riders. Waterproof.",
        "attributes": {"color": "red", "material": "nylon", "waterproof": "yes"},
    },
    "987654321": {
        "product_id": "987654321",
        "name": "Black Waist Pack Outdoor",
        "price": 45000,
        "shop_id": "88001",
        "shop_name": "SportGearPH",
        "description": "Heavy duty outdoor waist pack.",
        "attributes": {"color": "black", "material": "polyester"},
    },
    "111222333": {
        "product_id": "111222333",
        "name": "Blue Running Waist Belt Bag",
        "price": 19900,
        "shop_id": "77005",
        "shop_name": "RunFastStore",
        "description": "Lightweight running belt bag.",
        "attributes": {"color": "blue", "material": "spandex"},
    },
}


class FakeProxyClient:
    def __init__(self, timeout=90, max_retries=2):
        self.timeout = timeout
        self.max_retries = max_retries

    def get(self, path: str, params: Dict = None) -> List:
        params = params or {}
        if "find_product" in path:
            results = list(_MOCK_PRODUCTS)
            # Apply simple shop filter
            if params.get("shop_id"):
                results = [p for p in results if str(p.get("shop_id")) == str(params["shop_id"])]
            # Apply simple price filter
            if params.get("price"):
                try:
                    parts = str(params["price"]).split("-")
                    lo = float(parts[0]) * 100 if parts[0] else 0
                    hi = float(parts[1]) * 100 if len(parts) > 1 and parts[1] else float("inf")
                    results = [p for p in results if lo <= p.get("price", 0) <= hi]
                except (ValueError, IndexError):
                    pass
            return results

        if "view_product_information" in path:
            ids = str(params.get("product_ids", "")).split(",")
            return [_MOCK_DETAILS[i.strip()] for i in ids if i.strip() in _MOCK_DETAILS]

        return []

    def post(self, path: str, data: Dict = None) -> Dict:
        if "chat/completions" in path:
            messages = (data or {}).get("messages", [])
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"),
                "",
            )
            # Return a plausible JSON response
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "think": f"I analyzed the query and found relevant products.",
                                "selected_product_ids": "123456789",
                                "confidence": 0.85,
                                "reason": "Best match for all constraints.",
                            })
                        }
                    }
                ]
            }
        return {}


# ── Fake agent_interface ─────────────────────────────────────────────────────
_registered_tools = {}


def _tool_decorator(fn):
    _registered_tools[fn.__name__] = fn
    return fn


def _execute_tool_call(tool_name: str, parameters: Dict) -> Dict:
    fn = _registered_tools.get(tool_name)
    if not fn:
        return {"name": tool_name, "result": None, "error": "Tool not found"}
    try:
        result = fn(**parameters)
        return {"name": tool_name, "result": result, "error": None}
    except Exception as e:
        return {"name": tool_name, "result": None, "error": str(e)}


def _create_dialogue_step(
    think: str,
    tool_results: List,
    response: str,
    query: str,
    step: int,
) -> Dict:
    return {
        "completion": {
            "reasoning_content": think,
            "content": (
                f"<think>{think}</think>\n"
                f"<tool_call>{json.dumps(tool_results)}</tool_call>\n"
                f"<response>{response}</response>"
            ),
            "message": {
                "think": think,
                "tool_call": tool_results,
                "response": response,
            },
        },
        "extra_info": {
            "step": step,
            "query": query,
            "timestamp": int(time.time() * 1000),
        },
    }


# Inject into sys.modules BEFORE importing agent
_ai_module = types.ModuleType("src.agent.agent_interface")
_ai_module.Tool = _tool_decorator
_ai_module.execute_tool_call = _execute_tool_call
_ai_module.create_dialogue_step = _create_dialogue_step
sys.modules["src.agent.agent_interface"] = _ai_module

_pc_module = types.ModuleType("src.agent.proxy_client")
_pc_module.ProxyClient = FakeProxyClient
sys.modules["src.agent.proxy_client"] = _pc_module

# ── Now import the actual agent ──────────────────────────────────────────────
import importlib.util
import os

agent_path = os.path.join(os.path.dirname(__file__), "agent.py")
spec = importlib.util.spec_from_file_location("agent", agent_path)
agent_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mod)

agent_main = agent_mod.agent_main

# ─── Test Problems ────────────────────────────────────────────────────────────

PROBLEMS = {
    "product": {
        "query": "red waist bag for motorcycle under 400 PHP",
        "category": "product",
        "budget": 400.0,
        "constraint_check": {
            "keywords_present": ["waist bag", "motorcycle"],
            "keywords_missing": [],
            "price_fit": True,
        },
    },
    "shop": {
        "query": "waist bags and sport accessories",
        "category": "shop",
        "shop_id": "88001",
        "shop": {"id": "88001", "name": "SportGearPH"},
        "constraint_checks": [
            {"keywords_present": ["bag"], "keywords_missing": []},
            {"keywords_present": ["sport"], "keywords_missing": []},
        ],
    },
    "voucher": {
        "query": "running waist bag",
        "category": "voucher",
        "budget": 300.0,
        "voucher": "10%",
        "constraint_check": {
            "keywords_present": ["waist", "bag"],
            "keywords_missing": [],
            "price_fit": True,
        },
    },
}


def run_test(problem_type: str):
    """Run agent against a test problem and display results."""
    problem = PROBLEMS.get(problem_type)
    if not problem:
        print(f"Unknown problem type: {problem_type}")
        print(f"Available: {list(PROBLEMS.keys())}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Testing: {problem_type.upper()} problem")
    print(f"Query: {problem['query']}")
    print(f"{'='*60}\n")

    start = time.time()
    steps = agent_main(problem)
    elapsed = time.time() - start

    print(f"✅ Agent completed in {elapsed:.2f}s — {len(steps)} step(s)\n")

    for i, step in enumerate(steps, 1):
        completion = step.get("completion", {})
        msg = completion.get("message", {})
        extra = step.get("extra_info", {})

        print(f"--- Step {extra.get('step', i)} ---")

        think = msg.get("think", "")
        if think:
            print(f"THINK: {think[:300]}{'...' if len(think) > 300 else ''}")

        tool_results = msg.get("tool_call", [])
        for tr in tool_results:
            if isinstance(tr, dict):
                name = tr.get("name", "?")
                result = tr.get("result")
                print(f"TOOL:  {name}({_summarize_result(result)})")

        response = msg.get("response", "")
        if response:
            print(f"RESP:  {response[:200]}")

        print()

    print(f"{'='*60}")
    print(f"Total steps: {len(steps)} | Time: {elapsed:.2f}s")
    print(f"{'='*60}\n")


def _summarize_result(result) -> str:
    if result is None:
        return "None"
    if isinstance(result, list):
        return f"{len(result)} items"
    if isinstance(result, str):
        return result[:80]
    return str(result)[:80]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the ORO mining agent locally")
    parser.add_argument(
        "--problem",
        choices=["product", "shop", "voucher", "all"],
        default="all",
        help="Which problem type to test",
    )
    args = parser.parse_args()

    if args.problem == "all":
        for pt in ("product", "shop", "voucher"):
            run_test(pt)
    else:
        run_test(args.problem)
