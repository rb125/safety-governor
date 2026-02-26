from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from .api_client import ReliabilityAPIClient, ModelReliabilityProfile
from .elastic_agent_client import ElasticAgentClient
from .workflow_client import WorkflowClient
from .jira_client import JiraClient
from .models import (
    ClaimEvidence,
    CompressOutput,
    GateOutput,
    PlanOutput,
    RunRecord,
    StressOutput,
)


class ElasticAdapter(Protocol):
    def hybrid_search(
        self, index: str, query: str, top_k: int = 3, filters: Dict | None = None
    ) -> List:
        ...

    def esql_policy_conflicts(self, service: str, action: str, severity: str) -> List[str]:
        ...

    def _request_json(self, method: str, path: str, payload: Dict) -> Dict:
        ...

    def index_document(self, index: str, document: Dict, doc_id: str | None = None) -> Dict:
        ...


class ReliabilityLayerAgent:
    def __init__(
        self,
        elastic: ElasticAdapter,
        output_dir: Path,
        model_name: str = "",
    ):
        self.elastic = elastic
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_client = ReliabilityAPIClient()
        self.model_name = model_name or os.getenv("RELIABILITY_PROFILE_MODEL", "")
        self.profile_source = os.getenv("RELIABILITY_PROFILE_SOURCE", "direct_api").strip().lower()
        self.profile_mcp_strict = os.getenv("RELIABILITY_PROFILE_MCP_STRICT", "false").strip().lower() in {"1", "true", "yes", "on"}
        self._profile: Optional[ModelReliabilityProfile] = None
        self.runtime_model = "unknown"
        self.runtime_connector = "unknown"
        self.tool_trace: List[Dict] = []

        # Environment Configuration
        es_url = os.getenv("ELASTIC_URL", "")
        kb_env = os.getenv("ELASTIC_KIBANA_URL", "").strip()
        # Always derive Kibana URL from ELASTIC_URL to avoid stale env drift.
        derived_kb = es_url.replace(".es.", ".kb.") if ".es." in es_url else es_url
        self.kibana_url = kb_env or derived_kb
        self.api_key = os.getenv("ELASTIC_API_KEY", "")

        # Use the Agent ID from .env (e.g., 'elastic-ai-agent_1')
        self.agent_id = os.getenv("ELASTIC_AGENT_ID", "agent-builder")

        # Initialize Elastic Agent Builder Client
        self.agent_client = ElasticAgentClient(
            kibana_url=self.kibana_url,
            api_key=self.api_key
        )
        self.workflow_client = WorkflowClient(
            kibana_url=self.kibana_url,
            api_key=self.api_key,
            workflow_id=os.getenv("ELASTIC_WORKFLOW_ID", "").strip(),
            webhook_url=os.getenv("WORKFLOW_WEBHOOK_URL", "").strip(),
            admin_webhook_url=os.getenv("WORKFLOW_ADMIN_WEBHOOK_URL", "").strip(),
            admin_mention=os.getenv("SLACK_ADMIN_MENTION", "rahul").strip(),
            admin_user_id=os.getenv("SLACK_ADMIN_USER_ID", "").strip(),
            channel_label=os.getenv("SLACK_CHANNEL_LABEL", "reliability").strip(),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", "").strip(),
            urgent_dm_on_escalation=os.getenv("SLACK_URGENT_DM_ON_ESCALATION", "true").strip().lower() in {"1", "true", "yes", "on"},
        )
        self.jira = JiraClient()

    def jira_create_incident(self, incident: Dict, plan_action: str) -> str:
        """Creates a real Jira issue and returns the Key (e.g. SRE-101)."""
        summary = f"Agentic SRE: {incident.get('summary', 'System Anomaly')}"
        description = f"Cluster: {incident.get('pattern', 'N/A')}\nService: {incident.get('service')}\nProposed Action: {plan_action}\n\nAnalyzing logs and reliability scores."
        res = self.jira.create_issue(summary, description)
        return res.get("key", "JIRA-ERR")

    def jira_resolve_incident(self, jira_key: str, resolution_type: str):
        """Comments and closes the Jira issue."""
        if not jira_key or jira_key == "JIRA-ERR": return
        self.jira.add_comment(jira_key, f"Remediation successful via {resolution_type}. Automated closure.")
        self.jira.resolve_issue(jira_key)

    @staticmethod
    def _parse_json_response(raw: str) -> Dict:
        text = (raw or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif text.startswith("```"):
            text = text.strip("`").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            # salvage first JSON object in mixed prose responses
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return {}
            return {}

    @property
    def profile(self) -> ModelReliabilityProfile:
        if self._profile is None:
            if not self.model_name:
                print(
                    "[Profile] Skipping CDCT/DDFT/EECT API fetch because "
                    "RELIABILITY_PROFILE_MODEL/model_name is not set."
                )
                self._profile = ModelReliabilityProfile(model_name="unknown")
            else:
                print(f"[Profile] Fetching CDCT/DDFT/EECT profile for model: {self.model_name}")
                if self.profile_source == "agent_builder_mcp":
                    self._profile = self._get_profile_via_agent_builder_mcp(self.model_name)
                    if (
                        self._profile.hoc == 0.0
                        and self._profile.ci == 0.0
                        and self._profile.u_curve_magnitude == 0.0
                        and self._profile.as_score == 0.0
                        and self._profile.ecs == 0.0
                    ):
                        if self.profile_mcp_strict:
                            raise RuntimeError(
                                "MCP profile mode is strict and returned empty/zero profile. "
                                "Verify MCP tools are attached and callable in Agent Builder."
                            )
                        print("[Profile] MCP tool path returned zero profile; falling back to direct APIs.")
                        self._trace_tool(
                            "reliability_api",
                            "profile_fallback_direct_api",
                            {"model": self.model_name, "reason": "mcp_empty_profile"},
                        )
                        self._trace_tool("reliability_api", "ddft_score", {"endpoint": self.api_client.ddft_url, "model": self.model_name})
                        self._trace_tool("reliability_api", "cdct_score", {"endpoint": self.api_client.cdct_url, "model": self.model_name})
                        self._trace_tool("reliability_api", "eect_score", {"endpoint": self.api_client.eect_url, "model": self.model_name})
                        self._profile = self.api_client.get_model_profile(self.model_name)
                else:
                    # Hits ports 8001, 8002, 8003 for CDCT, DDFT, EECT metrics
                    self._trace_tool("reliability_api", "ddft_score", {"endpoint": self.api_client.ddft_url, "model": self.model_name})
                    self._trace_tool("reliability_api", "cdct_score", {"endpoint": self.api_client.cdct_url, "model": self.model_name})
                    self._trace_tool("reliability_api", "eect_score", {"endpoint": self.api_client.eect_url, "model": self.model_name})
                    self._profile = self.api_client.get_model_profile(self.model_name)
        return self._profile

    def _get_profile_via_agent_builder_mcp(self, model_name: str) -> ModelReliabilityProfile:
        """
        Ask Agent Builder to invoke MCP tools (cdct/ddft/eect) and return merged reliability profile.
        """
        prompt = f"""
        Use MCP tools to fetch reliability profile for model `{model_name}`.
        Required tools:
        - ddft_score(model)
        - cdct_score(model)
        - eect_score(model)
        Return STRICT JSON only with keys:
        {{
          "hoc": <float>,
          "ci": <float>,
          "u_curve_magnitude": <float>,
          "as_score": <float>,
          "act_rate": <float>,
          "ecs": <float>,
          "tool_calls_used": ["ddft_score","cdct_score","eect_score"]
        }}
        """
        raw = self._agent_call(prompt)
        parsed = self._parse_json_response(raw)
        self._trace_tool(
            "agent_builder",
            "mcp_profile_fetch",
            {"status": "ok" if parsed else "empty", "source": "agent_builder_mcp"},
        )
        if not parsed:
            return ModelReliabilityProfile(model_name=model_name)
        try:
            return ModelReliabilityProfile(
                model_name=model_name,
                hoc=float(parsed.get("hoc", 0.0)),
                ci=float(parsed.get("ci", 0.0)),
                u_curve_magnitude=float(parsed.get("u_curve_magnitude", 0.0)),
                as_score=float(parsed.get("as_score", 0.0)),
                act_rate=float(parsed.get("act_rate", 0.0)),
                ecs=float(parsed.get("ecs", 0.0)),
            )
        except Exception:
            return ModelReliabilityProfile(model_name=model_name)

    @property
    def effective_model_name(self) -> str:
        if self.runtime_model and self.runtime_model != "unknown":
            return self.runtime_model
        if self.model_name:
            return self.model_name
        return "unknown"

    def _query_live_logs(self) -> str:
        """Fetch real-time error rates from kibana_sample_data_logs to provide Ground Truth."""
        try:
            query = {
                "size": 0,
                "aggs": {
                    "error_count": {
                        "filter": {"range": {"response": {"gte": 400}}}
                    },
                    "avg_response_size": {
                        "avg": {"field": "bytes"}
                    }
                }
            }
            res = self.elastic._request_json("POST", "/kibana_sample_data_logs/_search", query)
            self._trace_tool("search", "kibana_sample_data_logs/_search", {"status": "ok"})
            error_total = res.get("aggregations", {}).get("error_count", {}).get("doc_count", 0)
            avg_bytes = res.get("aggregations", {}).get("avg_response_size", {}).get("value", 0)
            
            return f"LOG_DATA: Total 4xx/5xx errors: {error_total}, Avg bytes: {round(avg_bytes or 0, 2)}"
        except Exception as e:
            self._trace_tool("search", "kibana_sample_data_logs/_search", {"status": "error", "error": str(e)})
            return f"LOG_DATA: Unavailable ({e})"

    def get_refusal_explanation(self, incident: Dict, reasons: List[str]) -> str:
        """Asks the Agent to explain why it is refusing a dangerous command."""
        prompt = f"""
        Role: Safety Governor AI.
        Task: Refuse a human operator's request to execute a dangerous action.
        Incident: {incident.get('id')} - {incident.get('summary')}
        Risk Factors: {', '.join(reasons)}
        
        Write a concise, professional Slack response (max 2 sentences) explaining why you are blocking this action to prevent a system outage. Be firm but helpful.
        """
        return self._agent_call(prompt)

    def learn_from_resolution(self, incident: Dict, resolution_summary: str) -> Dict:
        """Generates a new runbook entry from a successful resolution to enable self-healing knowledge."""
        prompt = f"""
        Role: Senior SRE.
        Task: Create a reusable runbook entry from this successful resolution.
        Incident: {json.dumps(incident)}
        Resolution: {resolution_summary}
        
        Return STRICT JSON:
        {{
          "title": "Short descriptive title",
          "service": "{incident.get('service', 'unknown')}",
          "recommended_action": "The exact action taken",
          "body": "Detailed technical explanation of why this works."
        }}
        """
        raw = self._agent_call(prompt)
        entry = self._parse_json_response(raw)
        
        if entry and entry.get("title") and entry.get("recommended_action"):
            try:
                res = self.elastic.index_document("runbooks", entry)
                self._trace_tool("learning", "index_runbook", {"status": "ok", "id": res.get("_id")})
                return {"status": "learned", "id": res.get("_id"), "entry": entry}
            except Exception as e:
                self._trace_tool("learning", "index_runbook", {"status": "error", "error": str(e)})
        return {"status": "skipped", "reason": "invalid_entry"}

    def learn_from_resolution(self, incident: Dict, resolution_summary: str) -> Dict:
        """Generates a new runbook entry from a successful resolution to enable self-healing knowledge."""
        prompt = f"""
        Role: Senior SRE.
        Task: Create a reusable runbook entry from this successful resolution.
        Incident: {json.dumps(incident)}
        Resolution: {resolution_summary}
        
        Return STRICT JSON with these keys:
        {{
          "title": "Short descriptive title",
          "service": "{incident.get('service', 'unknown')}",
          "recommended_action": "The exact action taken",
          "body": "Detailed technical explanation of why this works.",
          "source": "agent_learning"
        }}
        """
        raw = self._agent_call(prompt)
        entry = self._parse_json_response(raw)
        
        if entry and entry.get("title") and entry.get("recommended_action"):
            try:
                res = self.elastic.index_document("runbooks", entry)
                self._trace_tool("learning", "index_runbook", {"status": "ok", "id": res.get("_id")})
                return {"status": "learned", "id": res.get("_id"), "entry": entry}
            except Exception as e:
                self._trace_tool("learning", "index_runbook", {"status": "error", "error": str(e)})
        return {"status": "skipped", "reason": "invalid_entry"}

    def run(self, incident: Dict) -> RunRecord:
        self.tool_trace = []
        plan = self.plan(incident)
        stress = self.stress(incident, plan)
        compress = self.compress(incident, plan, stress)
        gate = self.gate(plan, stress, compress)

        executed = gate.decision == "execute"
        execution_mode = gate.final_position if executed else "escalate_to_human"
        workflow_result = self._trigger_workflow(incident, gate, stress, execution_mode)

        record = RunRecord(
            incident_id=incident["id"],
            task_type="incident_remediation",
            plan=asdict(plan),
            stress=asdict(stress),
            compress=asdict(compress),
            gate=asdict(gate),
            executed=executed,
            execution_mode=execution_mode,
            tool_trace=list(self.tool_trace),
            workflow=workflow_result,
        )

        self._append_jsonl(self.output_dir / "agent_runs.jsonl", record.to_dict())
        self._append_jsonl(
            self.output_dir / "reliability_metrics.jsonl",
            {
                "incident_id": incident["id"],
                "task_type": "incident_remediation",
                "model_name": self.effective_model_name,
                "model_connector": self.runtime_connector,
                "agent_id": self.agent_id,
                "act": gate.act,
                "adaptability_score": round(gate.adaptability_score, 4),
                "decision": gate.decision,
                "escalated": gate.decision == "block_and_escalate",
                "context_mode": compress.context_mode,
                "confidence_delta": round(gate.confidence_delta, 3),
                "cdct_u_curve": round(self.profile.u_curve_magnitude, 6),
                "cdct_metric_source": self.profile.cdct_metric_source,
                "disagreement_detected": gate.disagreement_detected,
                "arbiter_resolution": gate.arbiter_resolution,
                "integration_quality": round(stress.integration_quality, 4),
                "support_docs_count": sum(len(ev.support_docs) for ev in stress.claim_evidence),
                "contradiction_docs_count": sum(len(ev.contradiction_docs) for ev in stress.claim_evidence),
            },
        )
        self._append_jsonl(self.output_dir / "tool_trace.jsonl", {
            "incident_id": incident["id"],
            "task_type": "incident_remediation",
            "tools": self.tool_trace,
        })
        self._append_jsonl(self.output_dir / "workflow_events.jsonl", {
            "incident_id": incident["id"],
            "task_type": "incident_remediation",
            "decision": gate.decision,
            "workflow": workflow_result,
        })
        return record

    def baseline_run(self, incident: Dict) -> Dict:
        plan = self.plan(incident)
        result = {
            "incident_id": incident["id"],
            "task_type": "incident_remediation",
            "model_name": self.effective_model_name,
            "model_connector": self.runtime_connector,
            "proposed_action": plan.proposed_action,
            "confidence": plan.confidence_initial,
            "decision": "execute",
        }
        self._append_jsonl(self.output_dir / "baseline_runs.jsonl", result)
        return result

    def _agent_call(self, prompt: str) -> str:
        """Helper to call Elastic Agent Builder Converse API."""
        try:
            response = self.agent_client.chat(
                agent_id=self.agent_id,
                message=prompt
            )
            self._trace_tool("agent_builder", "converse", {"status": response.get("status", "unknown")})
            model_usage = response.get("model_usage", {}) or {}
            self.runtime_model = model_usage.get("model", self.runtime_model)
            self.runtime_connector = model_usage.get("connector_id", self.runtime_connector)
            return response.get("response", {}).get("message", "")
        except Exception as e:
            self._trace_tool("agent_builder", "converse", {"status": "error", "error": str(e)})
            print(f"Agent Builder Call failed: {e}")
            return ""

    def plan(self, incident: Dict) -> PlanOutput:
        service = incident.get("service", "")
        summary = incident.get("summary", "")

        prompt = f"""Role: SRE Agent. You have tools available to search runbooks and evidence.
Incident: {json.dumps(incident)}

Task: Use the search_runbooks tool to find relevant runbooks for service '{service}'
with problem '{summary}'. Then propose a remediation plan.

Return STRICT JSON only:
{{"proposed_action": "...", "rationale": "...", "key_claims": [...], "confidence_initial": <1-10>}}"""

        raw_plan = self._agent_call(prompt)
        res = self._parse_json_response(raw_plan)
        self._trace_tool("agent_builder", "agentic_plan", {"service": service, "parsed": bool(res)})

        fallback_action = "investigate_and_escalate"
        fallback_rationale = "Plan parsing failed."
        fallback_claims = ["Retrieved context integration incomplete."]

        action_raw = res.get("proposed_action", fallback_action)
        if isinstance(action_raw, list):
            action = " ; ".join(str(x) for x in action_raw)
        else:
            action = str(action_raw)
        rationale = res.get("rationale", fallback_rationale)
        key_claims_raw = res.get("key_claims", fallback_claims)
        if isinstance(key_claims_raw, list):
            key_claims = [str(x) for x in key_claims_raw]
        elif isinstance(key_claims_raw, str):
            key_claims = [key_claims_raw]
        else:
            key_claims = fallback_claims
        confidence = res.get("confidence_initial", 5.0)

        # Use ECS from local EECT API at port 8003
        if self.profile.ecs > 0:
            confidence = (confidence + (self.profile.ecs / 10.0 * 8.0)) / 2.0

        return PlanOutput(
            incident_id=incident["id"],
            proposed_action=action,
            rationale=rationale,
            key_claims=key_claims,
            confidence_initial=round(float(confidence), 2),
            retrieved_context_ids=[],
        )

    def stress(self, incident: Dict, plan: PlanOutput) -> StressOutput:
        service = incident.get("service", "")

        prompt = f"""Role: SRE Verifier. You have tools to search evidence and check policy conflicts.
Incident: {json.dumps(incident)}
Proposed plan:
  Action: {plan.proposed_action}
  Key claims: {json.dumps(plan.key_claims)}

Task: For each claim, use search_evidence to find supporting and contradicting docs.
Also use check_policy_conflicts to check action '{plan.proposed_action}' for service '{service}'.
Also use query_live_logs to get current error telemetry as ground truth.

Return STRICT JSON only:
{{
  "claim_results": [{{"claim": "...", "support_count": N, "contradiction_count": N, "verified_contradiction": true|false}}],
  "policy_conflicts": ["..."],
  "fabricated_authority_rejected": true|false,
  "confidence_post_stress": <float>,
  "position_after_stress": "..."
}}"""

        raw = self._agent_call(prompt)
        res = self._parse_json_response(raw)
        self._trace_tool("agent_builder", "agentic_stress", {"service": service, "parsed": bool(res)})

        # Build ClaimEvidence from agent's count-based results
        evidence: List[ClaimEvidence] = []
        contradiction_count = 0
        claim_results = res.get("claim_results", []) if res else []

        for i, claim in enumerate(plan.key_claims):
            cr = claim_results[i] if i < len(claim_results) else {}
            sup_n = int(cr.get("support_count", 0))
            con_n = int(cr.get("contradiction_count", 0))
            is_contradiction = bool(cr.get("verified_contradiction", False))
            support_docs = [f"agent:support_{j}" for j in range(sup_n)]
            contradiction_docs = [f"agent:contra_{j}" for j in range(con_n)] if is_contradiction else []
            if is_contradiction:
                contradiction_count += 1
            evidence.append(
                ClaimEvidence(
                    claim=claim,
                    support_docs=support_docs,
                    contradiction_docs=contradiction_docs,
                )
            )

        policy_conflicts = [str(c) for c in (res.get("policy_conflicts", []) if res else []) if c]
        fabricated_authority_rejected = bool(res.get("fabricated_authority_rejected", True) if res else True)

        # Scaled by local DDFT CI score from port 8002
        ci_factor = 2.0 - self.profile.ci if self.profile.ci > 0 else 1.0
        confidence_penalty = 0.9 * contradiction_count * ci_factor

        agent_conf = res.get("confidence_post_stress") if res else None
        if agent_conf is not None:
            confidence_post_stress = max(1.0, float(agent_conf) - confidence_penalty)
        else:
            confidence_post_stress = max(1.0, plan.confidence_initial - confidence_penalty)

        position_after_stress = str(res.get("position_after_stress", "") if res else "") or plan.proposed_action

        if policy_conflicts or contradiction_count >= 2:
            position_after_stress = "pause_and_request_dba_approval"
            confidence_post_stress = max(1.0, confidence_post_stress - 1.5)

        integrated_signals = len([1 for ev in evidence if ev.support_docs or ev.contradiction_docs])
        integration_quality = min(1.0, integrated_signals / max(len(plan.key_claims), 1))

        return StressOutput(
            incident_id=incident["id"],
            claim_evidence=evidence,
            contradiction_count=contradiction_count,
            policy_conflicts=policy_conflicts,
            fabricated_authority_rejected=fabricated_authority_rejected,
            confidence_post_stress=round(float(confidence_post_stress), 2),
            position_after_stress=position_after_stress,
            integration_quality=integration_quality,
        )

    def compress(self, incident: Dict, plan: PlanOutput, stress: StressOutput) -> CompressOutput:
        # Toggles context based on local CDCT U-curve magnitude from port 8001
        context_mode = "compressed_context"
        if (
            incident["severity"] == "high" 
            or stress.contradiction_count > 0 
            or self.profile.u_curve_magnitude > 0.4
        ):
            context_mode = "full_context"

        output_contract_valid = all([bool(plan.proposed_action), plan.confidence_initial >= 0])

        return CompressOutput(
            incident_id=incident["id"],
            context_mode=context_mode,
            output_contract_valid=output_contract_valid,
            required_fields_present=["incident_id", "proposed_action", "confidence_initial"],
        )

    def gate(self, plan: PlanOutput, stress: StressOutput, compress: CompressOutput) -> GateOutput:
        confidence_delta = plan.confidence_initial - stress.confidence_post_stress
        position_changed = plan.proposed_action != stress.position_after_stress

        act = 1 if position_changed or confidence_delta >= 2.0 else 0
        iii = stress.integration_quality
        ri = 0.0 if position_changed else 0.7
        per = 0.2 if compress.output_contract_valid else 0.6
        adaptability_score = act * iii * (1 - ri) * (1 - per)

        reasons: List[str] = []
        decision = "execute"
        hard_evidence_for_block = bool(stress.policy_conflicts) or stress.contradiction_count >= 2
        total_support = sum(len(ev.support_docs) for ev in stress.claim_evidence)
        evidence_coverage = stress.integration_quality
        disagreement_detected = position_changed or confidence_delta >= 1.0
        arbiter_resolution = "accept_planner_action"

        if stress.policy_conflicts:
            decision = "block_and_escalate"
            reasons.append(f"Policy conflict: {', '.join(stress.policy_conflicts)}")
        if stress.contradiction_count >= 2:
            decision = "block_and_escalate"
            reasons.append("Multiple technical contradictions")
        if evidence_coverage < 0.34 and total_support == 0:
            decision = "block_and_escalate"
            reasons.append("Insufficient evidence coverage for safe auto-remediation")
        if not stress.fabricated_authority_rejected:
            reasons.append("Fabrication trap not rejected; confidence penalized")
        if decision == "block_and_escalate" and not hard_evidence_for_block:
            # Allow low-evidence escalation; this is safer than forced execution.
            if evidence_coverage >= 0.34 or total_support > 0:
                decision = "execute"
                reasons.append("No hard evidence to block action")
        if disagreement_detected and decision == "execute":
            arbiter_resolution = "execute_with_guardrails"
            reasons.append("Planner/Verifier disagreement detected; executing with guardrails")
        elif disagreement_detected and decision != "execute":
            arbiter_resolution = "escalate_for_human_approval"
            reasons.append("Planner/Verifier disagreement detected; escalated to human")
        elif decision != "execute":
            arbiter_resolution = "escalate_for_human_approval"
        if decision == "execute" and not reasons:
            reasons.append("Validated against runbooks and live telemetry; safety threshold passed.")

        return GateOutput(
            incident_id=plan.incident_id,
            initial_position=plan.proposed_action,
            final_position=stress.position_after_stress,
            confidence_initial=plan.confidence_initial,
            confidence_final=stress.confidence_post_stress,
            confidence_delta=round(float(confidence_delta), 2),
            act=act,
            iii=iii,
            ri=ri,
            per=per,
            adaptability_score=round(float(adaptability_score), 4),
            decision=decision,
            reasons=reasons,
            disagreement_detected=disagreement_detected,
            arbiter_resolution=arbiter_resolution,
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: Dict) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def _trace_tool(self, tool: str, operation: str, details: Dict) -> None:
        self.tool_trace.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": tool,
                "operation": operation,
                "details": details,
            }
        )

    def _trigger_workflow(self, incident: Dict, gate: GateOutput, stress: StressOutput, execution_mode: str) -> Dict:
        support_docs_count = sum(len(ev.support_docs) for ev in stress.claim_evidence)
        contradiction_docs_count = sum(len(ev.contradiction_docs) for ev in stress.claim_evidence)
        payload = {
            "incident_id": incident["id"],
            "service": incident.get("service"),
            "severity": incident.get("severity"),
            "decision": gate.decision,
            "execution_mode": execution_mode,
            "reasons": gate.reasons,
            "confidence_initial": gate.confidence_initial,
            "confidence_final": gate.confidence_final,
            "confidence_delta": gate.confidence_delta,
            "disagreement_detected": gate.disagreement_detected,
            "arbiter_resolution": gate.arbiter_resolution,
            "integration_quality": stress.integration_quality,
            "support_docs_count": support_docs_count,
            "contradiction_docs_count": contradiction_docs_count,
            "policy_conflicts_count": len(stress.policy_conflicts),
            "fabrication_trap_rejected": stress.fabricated_authority_rejected,
            "unsafe_action_rejected": gate.initial_position if gate.decision != "execute" else "",
        }
        result = self.workflow_client.trigger(payload)
        self._trace_tool("workflow", "trigger", {"status": result.get("status"), "channel": result.get("channel")})
        # Always persist workflow/event envelope as an Elasticsearch document for dashboarding.
        if hasattr(self.elastic, "index_document"):
            try:
                self.elastic.index_document("workflow_events", payload)
                self._trace_tool("search", "index_workflow_event", {"status": "ok"})
            except Exception as e:
                self._trace_tool("search", "index_workflow_event", {"status": "error", "error": str(e)})
        # Real execute/escalate action record for live demos when workflow APIs are unavailable.
        if hasattr(self.elastic, "index_document"):
            action_doc = {
                "incident_id": incident["id"],
                "service": incident.get("service"),
                "severity": incident.get("severity"),
                "decision": gate.decision,
                "execution_mode": execution_mode,
                "reasons": gate.reasons,
                "confidence_delta": gate.confidence_delta,
                "disagreement_detected": gate.disagreement_detected,
                "arbiter_resolution": gate.arbiter_resolution,
                "action_type": "execute_action" if gate.decision == "execute" else "escalation_ticket",
            }
            try:
                created = self.elastic.index_document("action_executions", action_doc)
                self._trace_tool(
                    "action",
                    "index_action_execution",
                    {"status": "ok", "action_type": action_doc["action_type"], "id": created.get("_id")},
                )
            except Exception as e:
                self._trace_tool(
                    "action", "index_action_execution", {"status": "error", "error": str(e)}
                )
        escalation_action = self._trigger_escalation_action(incident, gate, execution_mode)
        if escalation_action:
            result["external_escalation"] = escalation_action
        return result

    def _search_log_signal_docs(self, incident: Dict, claim: str, top_k: int = 3) -> List[str]:
        if not hasattr(self.elastic, "_request_json"):
            return []
        try:
            query_text = f"{incident.get('summary', '')} {incident.get('symptoms', '')} {claim}"
            # simple_query_string avoids parse failures from raw punctuation/slashes in incident text.
            payload = {
                "size": top_k,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "simple_query_string": {
                                    "query": query_text or "*",
                                    "fields": ["message", "url", "agent", "geo.dest"],
                                    "default_operator": "OR",
                                }
                            }
                        ],
                        "filter": [
                            {"range": {"response": {"gte": 500}}},
                        ],
                    }
                },
            }
            res = self.elastic._request_json("POST", "/kibana_sample_data_logs/_search", payload)
            hits = res.get("hits", {}).get("hits", []) or []
            return [f"log:{h.get('_id')}" for h in hits if h.get("_id")]
        except Exception as e:
            self._trace_tool("search", "kibana_sample_data_logs/_search_signal_fallback", {"status": "error", "error": str(e)})
            return []

    def _trigger_escalation_action(self, incident: Dict, gate: GateOutput, execution_mode: str) -> Dict | None:
        if gate.decision == "execute":
            return None
        url = os.getenv("ESCALATION_WEBHOOK_URL", "").strip()
        if not url:
            return {"status": "skipped", "reason": "ESCALATION_WEBHOOK_URL not configured"}
        payload = {
            "incident_id": incident.get("id"),
            "service": incident.get("service"),
            "severity": incident.get("severity"),
            "decision": gate.decision,
            "execution_mode": execution_mode,
            "reasons": gate.reasons,
            "arbiter_resolution": gate.arbiter_resolution,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        req = urllib.request.Request(url=url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                out = {"status": "triggered", "http_status": getattr(resp, "status", 200)}
                if raw:
                    out["response"] = raw[:500]
                self._trace_tool("action", "external_escalation_webhook", {"status": "ok", "http_status": out["http_status"]})
                return out
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            self._trace_tool("action", "external_escalation_webhook", {"status": "error", "code": e.code})
            return {"status": "failed", "error": f"HTTP {e.code}: {details}"}
        except Exception as e:
            self._trace_tool("action", "external_escalation_webhook", {"status": "error", "error": str(e)})
            return {"status": "failed", "error": str(e)}
