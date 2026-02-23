#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict


SERVER_NAME = "reliability-framework-mcp"
SERVER_VERSION = "0.1.0"


def _api_base(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


CDCT_API = _api_base("CDCT_API_URL", "http://localhost:8001")
DDFT_API = _api_base("DDFT_API_URL", "http://localhost:8002")
EECT_API = _api_base("EECT_API_URL", "http://localhost:8003")


TOOLS = [
    {
        "name": "ddft_score",
        "description": "Fetch DDFT robustness metrics for a model (HOC/CI).",
        "inputSchema": {
            "type": "object",
            "properties": {"model": {"type": "string"}},
            "required": ["model"],
        },
    },
    {
        "name": "cdct_score",
        "description": "Fetch CDCT context-discipline metrics for a model (u_curve_magnitude).",
        "inputSchema": {
            "type": "object",
            "properties": {"model": {"type": "string"}},
            "required": ["model"],
        },
    },
    {
        "name": "eect_score",
        "description": "Fetch EECT/AGT action-gating metrics for a model (AS/ACT/ECS).",
        "inputSchema": {
            "type": "object",
            "properties": {"model": {"type": "string"}},
            "required": ["model"],
        },
    },
    {
        "name": "reliability_profile",
        "description": "Fetch merged CDCT + DDFT + EECT profile for a model.",
        "inputSchema": {
            "type": "object",
            "properties": {"model": {"type": "string"}},
            "required": ["model"],
        },
    },
]


def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def extract_ddft(payload: Any) -> Dict[str, float]:
    if not isinstance(payload, dict):
        return {"hoc": 0.0, "ci": 0.0}
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    hoc = payload.get("HOC", payload.get("AS", details.get("HOC", 0.0)))
    ci = payload.get("CI", payload.get("ER", details.get("CI", 0.0)))
    try:
        hoc_f = float(hoc)
    except Exception:
        hoc_f = 0.0
    try:
        ci_f = float(ci)
    except Exception:
        ci_f = 0.0
    return {"hoc": hoc_f, "ci": ci_f}


def extract_cdct(payload: Any) -> Dict[str, float]:
    if isinstance(payload, dict):
        try:
            return {"u_curve_magnitude": float(payload.get("u_curve_magnitude", 0.0))}
        except Exception:
            return {"u_curve_magnitude": 0.0}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and "u_curve_magnitude" in item:
                try:
                    return {"u_curve_magnitude": float(item.get("u_curve_magnitude", 0.0))}
                except Exception:
                    return {"u_curve_magnitude": 0.0}
    return {"u_curve_magnitude": 0.0}


def extract_eect(payload: Any) -> Dict[str, float]:
    if not isinstance(payload, dict):
        return {"as_score": 0.0, "act_rate": 0.0, "ecs": 0.0}
    as_raw = payload.get("AS", payload.get("as_score", 0.0))
    act_raw = payload.get("ACT Rate", payload.get("act_rate", payload.get("stability_index", 0.0)))
    ecs_raw = payload.get("ECS", payload.get("ecs", 0.0))
    try:
        as_f = float(as_raw)
    except Exception:
        as_f = 0.0
    try:
        act_f = float(act_raw)
    except Exception:
        act_f = 0.0
    try:
        ecs_f = float(ecs_raw)
    except Exception:
        ecs_f = 0.0
    return {"as_score": as_f, "act_rate": act_f, "ecs": ecs_f}


def tool_response(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data)}],
        "structuredContent": data,
    }


def call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    model = str(args.get("model", "")).strip()
    if not model:
        raise ValueError("tool argument 'model' is required")
    if name == "ddft_score":
        raw = fetch_json(f"{DDFT_API}/score/{model}")
        out = {"model": model, **extract_ddft(raw)}
        return tool_response(out)
    if name == "cdct_score":
        raw = fetch_json(f"{CDCT_API}/score/{model}")
        out = {"model": model, **extract_cdct(raw)}
        return tool_response(out)
    if name == "eect_score":
        raw = fetch_json(f"{EECT_API}/score/{model}")
        out = {"model": model, **extract_eect(raw)}
        return tool_response(out)
    if name == "reliability_profile":
        d = extract_ddft(fetch_json(f"{DDFT_API}/score/{model}"))
        c = extract_cdct(fetch_json(f"{CDCT_API}/score/{model}"))
        e = extract_eect(fetch_json(f"{EECT_API}/score/{model}"))
        out = {"model": model, **d, **c, **e}
        return tool_response(out)
    raise ValueError(f"unknown tool: {name}")


def handle_request(req: Dict[str, Any]) -> Dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            name = str(params.get("name", "")).strip()
            arguments = params.get("arguments", {}) or {}
            result = call_tool(name, arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e)},
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if not isinstance(req, dict):
            continue
        try:
            response = handle_request(req)
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            response = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "error": {"code": -32001, "message": f"HTTP {e.code}: {details}"},
            }
        except Exception as e:
            response = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "error": {"code": -32000, "message": str(e)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
