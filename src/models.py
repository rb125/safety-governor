from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ClaimEvidence:
    claim: str
    support_docs: List[str]
    contradiction_docs: List[str]


@dataclass
class PlanOutput:
    incident_id: str
    proposed_action: str
    rationale: str
    key_claims: List[str]
    confidence_initial: float
    retrieved_context_ids: List[str]


@dataclass
class StressOutput:
    incident_id: str
    claim_evidence: List[ClaimEvidence]
    contradiction_count: int
    policy_conflicts: List[str]
    fabricated_authority_rejected: bool
    confidence_post_stress: float
    position_after_stress: str
    integration_quality: float


@dataclass
class CompressOutput:
    incident_id: str
    context_mode: str
    output_contract_valid: bool
    required_fields_present: List[str]


@dataclass
class GateOutput:
    incident_id: str
    initial_position: str
    final_position: str
    confidence_initial: float
    confidence_final: float
    confidence_delta: float
    act: int
    iii: float
    ri: float
    per: float
    adaptability_score: float
    decision: str
    reasons: List[str] = field(default_factory=list)
    disagreement_detected: bool = False
    arbiter_resolution: str = ""


@dataclass
class RunRecord:
    incident_id: str
    task_type: str
    plan: Dict[str, Any]
    stress: Dict[str, Any]
    compress: Dict[str, Any]
    gate: Dict[str, Any]
    executed: bool
    execution_mode: str
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    workflow: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
