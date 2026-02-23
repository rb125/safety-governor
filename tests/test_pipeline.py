from __future__ import annotations

import json
from pathlib import Path

from src.elastic_mock import ElasticMock
from src.reliability_layer import ReliabilityLayerAgent


def make_agent(tmp_path: Path) -> tuple[ReliabilityLayerAgent, dict]:
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "data" / "sample_data.json").read_text(encoding="utf-8"))
    return ReliabilityLayerAgent(ElasticMock(data), tmp_path), data


def test_high_severity_incident_is_blocked(tmp_path: Path) -> None:
    agent, data = make_agent(tmp_path)
    record = agent.run(data["incidents"][0])

    assert record.gate["decision"] == "block_and_escalate"
    assert record.executed is False
    assert record.gate["act"] == 1


def test_medium_severity_incident_executes(tmp_path: Path) -> None:
    agent, data = make_agent(tmp_path)
    record = agent.run(data["incidents"][1])

    assert record.gate["decision"] == "execute"
    assert record.executed is True
