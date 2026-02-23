from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.elastic_mock import ElasticMock
from src.reliability_layer import ReliabilityLayerAgent


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.data = json.loads((root / "data" / "sample_data.json").read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.agent = ReliabilityLayerAgent(
            ElasticMock(self.data),
            Path(self.tmp.name),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_high_severity_incident_is_blocked(self) -> None:
        record = self.agent.run(self.data["incidents"][0])
        self.assertEqual(record.gate["decision"], "block_and_escalate")
        self.assertFalse(record.executed)
        self.assertEqual(record.gate["act"], 1)

    def test_medium_severity_incident_executes(self) -> None:
        record = self.agent.run(self.data["incidents"][1])
        self.assertEqual(record.gate["decision"], "execute")
        self.assertTrue(record.executed)


if __name__ == "__main__":
    unittest.main()
