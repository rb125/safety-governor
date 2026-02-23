from __future__ import annotations

import argparse
import json
import os
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
    parser = argparse.ArgumentParser(description="Run reliability layer against real Elasticsearch data")
    parser.add_argument("--incident-file", required=True, help="Path to incident JSON file")
    parser.add_argument("--baseline", action="store_true", help="Run baseline only")
    parser.add_argument("--show-json", action="store_true", help="Print full JSON payload")
    return parser.parse_args()


def print_agentic_summary(result: dict) -> None:
    plan = result.get("plan", {})
    stress = result.get("stress", {})
    gate = result.get("gate", {})
    tool_trace = result.get("tool_trace", []) or []
    tool_ops = [f"{t.get('tool')}::{t.get('operation')}" for t in tool_trace]

    print("Agentic Workflow Summary")
    print(f"- Incident: {result.get('incident_id')}")
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


def main() -> None:
    args = parse_args()
    incident_path = Path(args.incident_file)
    incident = json.loads(incident_path.read_text(encoding="utf-8"))

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

    if args.baseline:
        result = agent.baseline_run(incident)
        print(json.dumps(result, indent=2))
        return

    result = agent.run(incident)
    result_dict = result.to_dict()
    print_agentic_summary(result_dict)
    if args.show_json:
        print("\nFull JSON:")
        print(json.dumps(result_dict, indent=2))

    summary = summarize_metrics(OUTPUT_DIR / "reliability_metrics.jsonl")
    print("\\nMetrics summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
