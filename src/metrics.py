from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Dict, List


def summarize_metrics(path: Path) -> Dict:
    rows: List[Dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))

    if not rows:
        return {
            "runs": 0,
            "avg_as": 0.0,
            "escalation_rate": 0.0,
            "auto_execute_rate": 0.0,
            "avg_confidence_delta": 0.0,
            "avg_integration_quality": 0.0,
            "avg_support_docs": 0.0,
            "disagreement_rate": 0.0,
            "estimated_minutes_saved_per_run": 0.0,
            "by_model": {},
            "by_context_mode": {},
        }

    by_model: Dict[str, List[Dict]] = {}
    by_context: Dict[str, List[Dict]] = {}
    for row in rows:
        by_model.setdefault(row.get("model_name", "unknown"), []).append(row)
        by_context.setdefault(row["context_mode"], []).append(row)

    return {
        "runs": len(rows),
        "avg_as": round(mean(r["adaptability_score"] for r in rows), 4),
        "escalation_rate": round(sum(1 for r in rows if r["escalated"]) / len(rows), 4),
        "auto_execute_rate": round(sum(1 for r in rows if not r["escalated"]) / len(rows), 4),
        "avg_confidence_delta": round(mean(r["confidence_delta"] for r in rows), 4),
        "avg_integration_quality": round(mean(r.get("integration_quality", 0.0) for r in rows), 4),
        "avg_support_docs": round(mean(r.get("support_docs_count", 0.0) for r in rows), 2),
        "disagreement_rate": round(sum(1 for r in rows if r.get("disagreement_detected")) / len(rows), 4),
        # Rough demo KPI: 12 min saved for safe auto-exec, 4 min for escalated pre-triage package.
        "estimated_minutes_saved_per_run": round(
            mean(12.0 if not r["escalated"] else 4.0 for r in rows), 2
        ),
        "by_model": {
            k: {
                "count": len(v),
                "avg_as": round(mean(x["adaptability_score"] for x in v), 4),
                "escalation_rate": round(sum(1 for x in v if x["escalated"]) / len(v), 4),
            }
            for k, v in by_model.items()
        },
        "by_context_mode": {
            k: {
                "count": len(v),
                "avg_as": round(mean(x["adaptability_score"] for x in v), 4),
                "escalation_rate": round(sum(1 for x in v if x["escalated"]) / len(v), 4),
            }
            for k, v in by_context.items()
        },
    }
