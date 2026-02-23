from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src.elastic_rest import ElasticRestClient
from src.reliability_layer import ReliabilityLayerAgent


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_incident(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_run(tag: str, record: dict) -> None:
    gate = record.get("gate", {})
    print(f"\n=== {tag} ===")
    print(f"incident_id: {record.get('incident_id')}")
    print(f"decision: {gate.get('decision')}")
    print(f"confidence_delta: {gate.get('confidence_delta')}")
    print(f"act: {gate.get('act')}")
    print(f"execution_mode: {record.get('execution_mode')}")
    print(f"workflow_status: {(record.get('workflow') or {}).get('status')}")
    print(f"workflow_channel: {(record.get('workflow') or {}).get('channel')}")
    tools = [f"{t.get('tool')}::{t.get('operation')}" for t in record.get("tool_trace", [])]
    print("tools:")
    for t in tools:
        print(f"  - {t}")


def fetch_recent(client: ElasticRestClient, index: str, size: int = 5) -> list[dict]:
    q = {
        "size": size,
        "query": {"match_all": {}},
    }
    data = client._request_json("POST", f"/{index}/_search", q)
    hits = data.get("hits", {}).get("hits", [])
    out = []
    for h in hits:
        src = h.get("_source", {})
        src["_id"] = h.get("_id")
        out.append(src)
    return out


def main() -> None:
    load_dotenv()

    base_url = env("ELASTIC_URL")
    api_key = env("ELASTIC_API_KEY")

    index_map = {
        "runbooks": os.getenv("ELASTIC_RUNBOOKS_INDEX", "runbooks-demo"),
        "evidence": os.getenv("ELASTIC_EVIDENCE_INDEX", "evidence-demo"),
        "policies": os.getenv("ELASTIC_POLICIES_INDEX", "policies-demo"),
        "incidents": os.getenv("ELASTIC_INCIDENTS_INDEX", "incidents-demo"),
    }

    client = ElasticRestClient(base_url=base_url, api_key=api_key, index_map=index_map)
    agent = ReliabilityLayerAgent(elastic=client, output_dir=OUTPUT_DIR)

    incident_high = load_incident(ROOT / "scenarios" / "incident_high.json")
    incident_medium = load_incident(ROOT / "scenarios" / "incident_medium.json")

    baseline = agent.baseline_run(incident_high)
    print("\n=== BASELINE ===")
    print(json.dumps(baseline, indent=2))

    high = agent.run(incident_high).to_dict()
    medium = agent.run(incident_medium).to_dict()

    summarize_run("RELIABILITY HIGH", high)
    summarize_run("RELIABILITY MEDIUM", medium)

    recent_actions = fetch_recent(client, "action_executions", size=5)
    print("\n=== ACTION EXECUTIONS (latest) ===")
    print(json.dumps(recent_actions, indent=2))

    recent_workflow_events = fetch_recent(client, "workflow_events", size=5)
    print("\n=== WORKFLOW EVENTS (latest) ===")
    print(json.dumps(recent_workflow_events, indent=2))


if __name__ == "__main__":
    main()
