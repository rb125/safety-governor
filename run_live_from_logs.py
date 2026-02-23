from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.elastic_rest import ElasticRestClient
from src.metrics import summarize_metrics
from src.reliability_layer import ReliabilityLayerAgent

load_dotenv()

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate incident from live Elasticsearch logs and run reliability agent")
    parser.add_argument("--window-minutes", type=int, default=30, help="Lookback window in minutes")
    parser.add_argument("--index", default="kibana_sample_data_logs", help="Logs index to query")
    parser.add_argument("--service", default="elastic-downloads", help="Service label for generated incident")
    parser.add_argument("--show-json", action="store_true", help="Print full JSON payload")
    return parser.parse_args()


def print_agentic_summary(result: dict, incident: dict) -> None:
    plan = result.get("plan", {})
    stress = result.get("stress", {})
    gate = result.get("gate", {})
    tool_trace = result.get("tool_trace", []) or []
    tool_ops = [f"{t.get('tool')}::{t.get('operation')}" for t in tool_trace]

    print("Agentic Workflow Summary")
    print(f"- Incident: {incident.get('id')} ({incident.get('severity')})")
    print(f"- Generated from live logs: {incident.get('summary')}")
    print(f"- Symptoms: {incident.get('symptoms')}")
    print(f"- Planner action: {str(plan.get('proposed_action', 'n/a'))[:140]}...")
    print(
        "- Stress verdict: "
        f"support_docs={sum(len(x.get('support_docs', [])) for x in stress.get('claim_evidence', []))}, "
        f"contradictions={sum(len(x.get('contradiction_docs', [])) for x in stress.get('claim_evidence', []))}, "
        f"integration_quality={stress.get('integration_quality')}"
    )
    print(
        "- Gate: "
        f"decision={gate.get('decision')}, confidence={gate.get('confidence_initial')} -> "
        f"{gate.get('confidence_final')} (delta={gate.get('confidence_delta')}), "
        f"disagreement={gate.get('disagreement_detected')}"
    )
    print(f"- Executed: {result.get('executed')}")
    print("- Elastic tool calls observed:")
    for op in tool_ops:
        if op.startswith("search::") or op.startswith("esql::"):
            print(f"  - {op}")
    print("- Agent Builder calls observed:")
    for op in tool_ops:
        if op.startswith("agent_builder::"):
            print(f"  - {op}")
    print("- Action/Workflow calls observed:")
    for op in tool_ops:
        if op.startswith("workflow::") or op.startswith("action::"):
            print(f"  - {op}")


def build_live_incident(client: ElasticRestClient, index: str, service: str, window_minutes: int) -> dict:
    q = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": f"now-{window_minutes}m", "lte": "now"}}},
                    {"range": {"response": {"gte": 500}}},
                ]
            }
        },
        "aggs": {
            "top_urls": {"terms": {"field": "url.keyword", "size": 3}},
            "top_responses": {"terms": {"field": "response", "size": 5}},
        },
    }
    res = client._request_json("POST", f"/{index}/_search", q)
    total_obj = res.get("hits", {}).get("total", 0)
    if isinstance(total_obj, dict):
        total_errors = int(total_obj.get("value", 0))
    else:
        total_errors = int(total_obj or 0)

    url_buckets = res.get("aggregations", {}).get("top_urls", {}).get("buckets", []) or []
    response_buckets = res.get("aggregations", {}).get("top_responses", {}).get("buckets", []) or []

    top_url = url_buckets[0]["key"] if url_buckets else "unknown_url"
    top_resp = response_buckets[0]["key"] if response_buckets else 500

    if total_errors >= 200:
        severity = "critical"
    elif total_errors >= 50:
        severity = "high"
    else:
        severity = "medium"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    incident = {
        "id": f"inc-live-auto-{ts}",
        "service": service,
        "severity": severity,
        "summary": f"Live {top_resp} error spike in last {window_minutes}m (count={total_errors})",
        "symptoms": f"Top failing URL: {top_url}; observed {total_errors} server errors",
    }
    return incident


def main() -> None:
    args = parse_args()
    base_url = env("ELASTIC_URL")
    api_key = env("ELASTIC_API_KEY")

    index_map = {
        "runbooks": os.getenv("ELASTIC_RUNBOOKS_INDEX", "runbooks-*"),
        "evidence": os.getenv("ELASTIC_EVIDENCE_INDEX", "evidence-*"),
        "policies": os.getenv("ELASTIC_POLICIES_INDEX", "policies-*"),
        "incidents": os.getenv("ELASTIC_INCIDENTS_INDEX", "incidents-*"),
    }
    client = ElasticRestClient(base_url=base_url, api_key=api_key, index_map=index_map)
    agent = ReliabilityLayerAgent(elastic=client, output_dir=OUTPUT_DIR)

    incident = build_live_incident(client, args.index, args.service, args.window_minutes)
    print("Generated incident from live logs:")
    print(json.dumps(incident, indent=2))

    result = agent.run(incident).to_dict()
    print_agentic_summary(result, incident)
    if args.show_json:
        print("\nFull JSON:")
        print(json.dumps(result, indent=2))

    summary = summarize_metrics(OUTPUT_DIR / "reliability_metrics.jsonl")
    print("\nMetrics summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
