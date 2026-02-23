from __future__ import annotations

import json
from pathlib import Path

from src.elastic_mock import ElasticMock
from src.metrics import summarize_metrics
from src.reliability_layer import ReliabilityLayerAgent

from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "sample_data.json"
OUT_DIR = ROOT / "output"


def load_data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def print_case(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


def main() -> None:
    data = load_data()
    elastic = ElasticMock(data)
    agent = ReliabilityLayerAgent(elastic, OUT_DIR)

    incident_block = data["incidents"][0]
    incident_pass = data["incidents"][1]

    print("\n--- Running Demo (runtime model resolved from Agent Builder when available) ---")
    
    baseline_result = agent.baseline_run(incident_block)
    print_case("Baseline Agent (naive execute)", baseline_result)

    layered_block = agent.run(incident_block)
    print_case("Reliability Layer Result (expected block)", layered_block.to_dict())

    layered_pass = agent.run(incident_pass)
    print_case("Reliability Layer Result (expected execute)", layered_pass.to_dict())

    summary = summarize_metrics(OUT_DIR / "reliability_metrics.jsonl")
    print_case("Reliability Metrics Summary", summary)


if __name__ == "__main__":
    main()
