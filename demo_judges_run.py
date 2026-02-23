from __future__ import annotations

import json
import os
from copy import deepcopy
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


def load_incidents() -> list[dict]:
    high = json.loads((SCENARIOS_DIR / "incident_high.json").read_text(encoding="utf-8"))
    medium = json.loads((SCENARIOS_DIR / "incident_medium.json").read_text(encoding="utf-8"))
    downloads = json.loads((SCENARIOS_DIR / "incident_downloads_503.json").read_text(encoding="utf-8"))

    critical = deepcopy(high)
    critical["id"] = "inc-live-003-critical"
    critical["severity"] = "critical"
    critical["summary"] = "Critical checkout outage with payment auth failures"
    critical["symptoms"] = "payment failures, spikes in 5xx, potential cascading outage"

    return [medium, high, downloads, critical]


def print_timeline(result: dict) -> None:
    plan = result["plan"]
    stress = result["stress"]
    gate = result["gate"]
    naive_action = plan.get("proposed_action", "n/a")
    naive_decision = "execute"

    print(f"\n=== {result['incident_id']} | severity={result.get('severity', 'n/a')} ===")
    print(f"Planner -> action: {compact(naive_action)}")
    print(f"Verifier -> support={sum(len(x['support_docs']) for x in stress['claim_evidence'])}, "
          f"contradictions={sum(len(x['contradiction_docs']) for x in stress['claim_evidence'])}, "
          f"integration_quality={stress['integration_quality']}")
    print(
        f"Stress Shift -> confidence {gate['confidence_initial']} -> {gate['confidence_final']} "
        f"(delta {gate['confidence_delta']})"
    )
    print(
        f"Gate -> decision={gate['decision']} | disagreement={gate.get('disagreement_detected')} "
        f"| resolution={gate.get('arbiter_resolution')}"
    )
    print(f"Action -> {'EXECUTED' if result['executed'] else 'BLOCKED/ESCALATED'}")
    print(f"Naive Counterfactual -> would have {naive_decision.upper()} action: {compact(naive_action)}")
    if not result["executed"]:
        print("Impact -> unsafe automation prevented by safety gate")
    print("Reasons:")
    for r in gate.get("reasons", []):
        print(f"- {r}")


def compact(text: str, limit: int = 130) -> str:
    t = " ".join(str(text).split())
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


def fetch_blocked_actions(client: ElasticRestClient, size: int = 10) -> list[dict]:
    q = {
        "size": size,
        "query": {"term": {"decision.keyword": "block_and_escalate"}},
    }
    try:
        res = client._request_json("POST", "/action_executions/_search", q)
        hits = res.get("hits", {}).get("hits", []) or []
        return [h.get("_source", {}) for h in hits]
    except Exception:
        return []


def main() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"Guardian Judges Demo Run @ {ts}")
    print("Flow: Planner -> Verifier -> Constraint -> Gate -> Execute/Block")

    client = build_client()
    agent = ReliabilityLayerAgent(elastic=client, output_dir=OUTPUT_DIR)

    outcomes = []
    for incident in load_incidents():
        result = agent.run(incident).to_dict()
        result["severity"] = incident.get("severity")
        outcomes.append(result)
        print_timeline(result)

    approved = sum(1 for r in outcomes if r["executed"])
    blocked = len(outcomes) - approved
    print("\n=== Demo Summary ===")
    print(f"Total incidents: {len(outcomes)}")
    print(f"Approved auto-remediation: {approved}")
    print(f"Blocked/escalated by safety gate: {blocked}")

    blocked_docs = fetch_blocked_actions(client, size=5)
    print(f"Blocked actions indexed in Elasticsearch (latest): {len(blocked_docs)}")

    metrics = summarize_metrics(OUTPUT_DIR / "reliability_metrics.jsonl")
    print("\n=== KPI Snapshot ===")
    keys = [
        "runs",
        "auto_execute_rate",
        "escalation_rate",
        "avg_confidence_delta",
        "avg_integration_quality",
        "avg_support_docs",
        "disagreement_rate",
        "estimated_minutes_saved_per_run",
    ]
    for k in keys:
        print(f"{k}: {metrics.get(k)}")


if __name__ == "__main__":
    main()
