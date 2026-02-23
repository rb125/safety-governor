from __future__ import annotations

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
SCENARIOS_DIR = ROOT / "scenarios"


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def build_client() -> ElasticRestClient:
    return ElasticRestClient(
        base_url=env("ELASTIC_URL"),
        api_key=env("ELASTIC_API_KEY"),
        index_map={
            "runbooks": os.getenv("ELASTIC_RUNBOOKS_INDEX", "runbooks-*"),
            "evidence": os.getenv("ELASTIC_EVIDENCE_INDEX", "evidence-*"),
            "policies": os.getenv("ELASTIC_POLICIES_INDEX", "policies-*"),
            "incidents": os.getenv("ELASTIC_INCIDENTS_INDEX", "incidents-*"),
        },
    )


def build_live_incident(client: ElasticRestClient, window_minutes: int = 30) -> dict:
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
        "aggs": {"top_urls": {"terms": {"field": "url.keyword", "size": 2}}},
    }
    res = client._request_json("POST", "/kibana_sample_data_logs/_search", q)
    total_obj = res.get("hits", {}).get("total", 0)
    total = int(total_obj.get("value", 0)) if isinstance(total_obj, dict) else int(total_obj or 0)
    top_urls = res.get("aggregations", {}).get("top_urls", {}).get("buckets", []) or []
    top_url = top_urls[0]["key"] if top_urls else "unknown"
    sev = "critical" if total >= 200 else ("high" if total >= 50 else "medium")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return {
        "id": f"inc-live-auto-{ts}",
        "service": "elastic-downloads",
        "severity": sev,
        "summary": f"Live 5xx spike in last {window_minutes}m (count={total})",
        "symptoms": f"Top failing URL: {top_url}",
    }


def short(text: str, n: int = 150) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 3] + "..."


def first_steps(action: str, k: int = 3) -> list[str]:
    parts = [p.strip() for p in action.replace("\n", " ").split(" ; ") if p.strip()]
    if not parts:
        parts = [action]
    return [short(x, 120) for x in parts[:k]]


def print_run_summary(result: dict, incident: dict) -> None:
    plan = result["plan"]
    stress = result["stress"]
    gate = result["gate"]
    workflow = result.get("workflow", {}) or {}
    trace = result.get("tool_trace", []) or []

    support = sum(len(c.get("support_docs", [])) for c in stress.get("claim_evidence", []))
    contradictions = sum(len(c.get("contradiction_docs", [])) for c in stress.get("claim_evidence", []))
    print(f"\n=== Incident {incident['id']} | {incident['service']} | {incident['severity'].upper()} ===")
    print(f"Plan: {short(plan.get('proposed_action'))}")
    print(f"Verifier: support_docs={support}, contradictions={contradictions}, integration_quality={stress.get('integration_quality')}")
    print(
        "Gate: "
        f"{gate.get('decision')} | confidence {gate.get('confidence_initial')} -> {gate.get('confidence_final')} "
        f"(delta {gate.get('confidence_delta')}) | disagreement={gate.get('disagreement_detected')}"
    )
    print(f"Resolution: {gate.get('arbiter_resolution')}")
    print("Corrective Measures:")
    for s in first_steps(result.get("execution_mode", "")):
        print(f"- {s}")
    print("Reasons:")
    for r in gate.get("reasons", []):
        print(f"- {r}")

    elastic_ops = [f"{x.get('tool')}::{x.get('operation')}" for x in trace if str(x.get("tool")).lower() in {"search", "esql"}]
    api_ops = [f"{x.get('tool')}::{x.get('operation')}" for x in trace if str(x.get("tool")).lower() == "reliability_api"]
    agent_ops = [f"{x.get('tool')}::{x.get('operation')}" for x in trace if str(x.get("tool")).lower() == "agent_builder"]
    print("Tool Usage:")
    print(f"- Elastic tools: {', '.join(elastic_ops[:8])}{' ...' if len(elastic_ops) > 8 else ''}")
    print(f"- CDCT/DDFT/EECT API calls: {', '.join(api_ops) if api_ops else 'none'}")
    print(f"- Agent Builder calls: {', '.join(agent_ops)}")

    print("Slack / Action:")
    print(f"- Workflow channel post: {workflow.get('status')} ({workflow.get('channel')})")
    admin = (workflow.get("admin_delivery") or {}).get("status", "n/a")
    print(f"- Admin summary: {admin}")
    urgent = workflow.get("urgent_dm", {}) or {}
    if urgent:
        print(f"- Urgent DM: {urgent.get('status')} ({urgent.get('error', 'ok')})")


def main() -> None:
    print("Guardian Agentic Demo")
    print("Flow: Plan -> Verify -> Constrain -> Gate -> Act")
    print("No static dump output; operational summary only.\n")

    client = build_client()
    agent = ReliabilityLayerAgent(elastic=client, output_dir=OUTPUT_DIR)

    high = json.loads((SCENARIOS_DIR / "incident_high.json").read_text(encoding="utf-8"))
    medium = json.loads((SCENARIOS_DIR / "incident_medium.json").read_text(encoding="utf-8"))
    incidents = []
    try:
        live = build_live_incident(client, window_minutes=30)
        incidents.append(live)
    except Exception as e:
        print(f"Live-log incident generation unavailable: {e}")
        print("Falling back to static scenarios for this run.\n")
    incidents.extend([high, medium])

    # Force profile fetch once so the demo explicitly shows framework signals.
    p = agent.profile
    print(
        "Framework profile loaded: "
        f"HOC={p.hoc}, CI={p.ci}, CDCT={p.u_curve_magnitude} ({p.cdct_metric_source}), "
        f"AS={p.as_score}, ECS={p.ecs}"
    )

    outcomes = []
    for inc in incidents:
        record = agent.run(inc).to_dict()
        outcomes.append(record)
        print_run_summary(record, inc)

    approved = sum(1 for r in outcomes if r.get("executed"))
    blocked = len(outcomes) - approved
    print("\n=== Demo Outcome ===")
    print(f"- Incidents run: {len(outcomes)}")
    print(f"- Approved auto-remediation: {approved}")
    print(f"- Blocked/escalated: {blocked}")

    kpi = summarize_metrics(OUTPUT_DIR / "reliability_metrics.jsonl")
    print("KPI Snapshot:")
    for k in [
        "auto_execute_rate",
        "escalation_rate",
        "avg_confidence_delta",
        "avg_integration_quality",
        "avg_support_docs",
        "disagreement_rate",
        "estimated_minutes_saved_per_run",
    ]:
        print(f"- {k}: {kpi.get(k)}")


if __name__ == "__main__":
    main()
