#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

from fastmcp import FastMCP


# === API endpoints ===

def _api_base(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


CDCT_API = _api_base("CDCT_API_URL", "http://localhost:8001")
DDFT_API = _api_base("DDFT_API_URL", "http://localhost:8002")
EECT_API = _api_base("EECT_API_URL", "http://localhost:8003")

ELASTIC_URL = os.getenv("ELASTIC_URL", "").rstrip("/")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")
ES_RUNBOOKS_INDEX = os.getenv("ES_RUNBOOKS_INDEX", "runbooks-demo")
ES_EVIDENCE_INDEX = os.getenv("ES_EVIDENCE_INDEX", "evidence-demo")
ES_POLICIES_INDEX = os.getenv("ES_POLICIES_INDEX", "policies-demo")

mcp = FastMCP("reliability-framework-mcp")


# === HTTP helpers ===

def _es_post(path: str, payload: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    if not ELASTIC_URL:
        raise ValueError("ELASTIC_URL env var is not set")
    url = f"{ELASTIC_URL}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, method="POST")
    req.add_header("Authorization", f"ApiKey {ELASTIC_API_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def fetch_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


# === Reliability score extractors ===

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


# === SRE operation tools ===

@mcp.tool()
def search_runbooks(query: str, service: str, top_k: int = 3) -> List[Dict]:
    """Search the SRE runbooks index for remediation procedures matching a query and service."""
    filter_clauses = [{"term": {"service": service}}] if service else []
    payload = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [{"multi_match": {"query": query or "*", "fields": ["title^2", "body", "recommended_action"], "type": "best_fields"}}],
                "filter": filter_clauses,
            }
        },
    }
    data = _es_post(f"/{ES_RUNBOOKS_INDEX}/_search", payload)
    hits = data.get("hits", {}).get("hits", [])
    return [
        {"id": h["_id"], "title": h.get("_source", {}).get("title", ""), "action": h.get("_source", {}).get("recommended_action", ""), "body": h.get("_source", {}).get("body", "")}
        for h in hits
    ]


@mcp.tool()
def search_evidence(query: str, service: str, top_k: int = 3) -> List[Dict]:
    """Search the evidence index for supporting or contradicting documents for a claim."""
    filter_clauses = [{"terms": {"service": [service, "*"]}}] if service else []
    payload = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [{"multi_match": {"query": query or "*", "fields": ["text", "summary", "title"], "type": "best_fields"}}],
                "filter": filter_clauses,
            }
        },
    }
    data = _es_post(f"/{ES_EVIDENCE_INDEX}/_search", payload)
    hits = data.get("hits", {}).get("hits", [])
    return [
        {"id": h["_id"], "text": h.get("_source", {}).get("text", ""), "stance": h.get("_source", {}).get("stance", "")}
        for h in hits
    ]


@mcp.tool()
def check_policy_conflicts(service: str, action: str, severity: str) -> List[str]:
    """Check the policies index for conflicts that would block a proposed action for a given service and severity."""
    payload = {
        "size": 20,
        "query": {
            "bool": {
                "must": [
                    {"bool": {"should": [{"term": {"service": "*"}}, {"term": {"service": service}}], "minimum_should_match": 1}},
                    {"term": {"severities": severity.lower()}},
                    {"term": {"blocked_actions": action.lower()}},
                ]
            }
        },
    }
    data = _es_post(f"/{ES_POLICIES_INDEX}/_search", payload)
    hits = data.get("hits", {}).get("hits", [])
    return [h.get("_source", {}).get("id", h.get("_id", "unknown")) for h in hits]


@mcp.tool()
def query_live_logs() -> Dict[str, Any]:
    """Query live Kibana sample logs for real-time error counts and response size telemetry."""
    payload = {
        "size": 0,
        "aggs": {
            "error_count": {"filter": {"range": {"response": {"gte": 400}}}},
            "avg_response_size": {"avg": {"field": "bytes"}},
        },
    }
    data = _es_post("/kibana_sample_data_logs/_search", payload)
    error_count = data.get("aggregations", {}).get("error_count", {}).get("doc_count", 0)
    avg_bytes = data.get("aggregations", {}).get("avg_response_size", {}).get("value") or 0
    return {"error_count": error_count, "avg_bytes": round(float(avg_bytes), 2)}


# === Reliability scoring tools ===

@mcp.tool()
def ddft_score(model: str) -> Dict[str, Any]:
    """Fetch DDFT robustness metrics for a model (HOC/CI)."""
    raw = fetch_json(f"{DDFT_API}/score/{model}")
    return {"model": model, **extract_ddft(raw)}


@mcp.tool()
def cdct_score(model: str) -> Dict[str, Any]:
    """Fetch CDCT context-discipline metrics for a model (u_curve_magnitude)."""
    raw = fetch_json(f"{CDCT_API}/score/{model}")
    return {"model": model, **extract_cdct(raw)}


@mcp.tool()
def eect_score(model: str) -> Dict[str, Any]:
    """Fetch EECT/AGT action-gating metrics for a model (AS/ACT/ECS)."""
    raw = fetch_json(f"{EECT_API}/score/{model}")
    return {"model": model, **extract_eect(raw)}


@mcp.tool()
def reliability_profile(model: str) -> Dict[str, Any]:
    """Fetch merged CDCT + DDFT + EECT profile for a model."""
    d = extract_ddft(fetch_json(f"{DDFT_API}/score/{model}"))
    c = extract_cdct(fetch_json(f"{CDCT_API}/score/{model}"))
    e = extract_eect(fetch_json(f"{EECT_API}/score/{model}"))
    return {"model": model, **d, **c, **e}


if __name__ == "__main__":
    if "--http" in sys.argv:
        port = 8010
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i + 1])
                except ValueError:
                    pass
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")
