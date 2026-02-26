import time
import json
import os
import random
import sys
import threading
import socket
import io
import queue
from datetime import datetime
from pathlib import Path
from rich.live import Live
from rich.panel import Panel
from rich.console import Group
from rich.text import Text
from rich.table import Table
from rich.spinner import Spinner
from rich.layout import Layout
from rich.align import Align
from rich.markup import escape
from dotenv import load_dotenv

# Import existing logic
from src.reliability_layer import ReliabilityLayerAgent
from src.elastic_rest import ElasticRestClient
from src.models import GateOutput, StressOutput, ClaimEvidence

# Set global timeout
socket.setdefaulttimeout(15)
load_dotenv()

# Safety Configuration
REFUSAL_THRESHOLD = 5.0
FAST_DEMO_MODE = os.getenv("DEMO_FAST_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}

# Presenter mode: --present flag adds deliberate pauses at key demo moments.
# Without the flag the script runs at normal (real-time) pace.
PRESENT_MODE = "--present" in sys.argv

class IncidentState:
    DETECTED = "DETECTED"
    ANALYZING = "ANALYZING"
    PENDING_SLACK = "PENDING_SLACK"
    READY_TO_EXECUTE = "READY_TO_EXECUTE"
    EXECUTING = "EXECUTING"
    LEARNING = "LEARNING"
    RESOLVED = "RESOLVED"

class AgentDemo:
    def __init__(self):
        # THREAD-SAFE QUEUES (Gold Standard for UI)
        self.log_queue = queue.Queue()
        self.thought_queue = queue.Queue()
        self.display_logs, self.display_thoughts = [], []
        
        # State
        self.queue = []
        self.status_msg = "Initializing..."
        self.is_spinning = False
        self.active_errors = 0
        self.processed_logs = 0
        self.kb_updates = 0
        self.heartbeat = 0
        self.current_pattern = None
        self.ui_lock = threading.Lock()
        
        # UI Assets
        self.spinner = Spinner("dots", style="bold yellow")
        self.layout = Layout()
        self.setup_layout()
        
        # Backend
        self.client = ElasticRestClient(
            base_url=os.getenv("ELASTIC_URL"), 
            api_key=os.getenv("ELASTIC_API_KEY"),
            index_map={"runbooks": "runbooks-demo", "evidence": "evidence-demo", "policies": "policies-demo", "incidents": "incidents-demo"}
        )
        self.agent = ReliabilityLayerAgent(elastic=self.client, output_dir=Path("output"))

    def setup_layout(self):
        self.layout.split(Layout(name="header", size=3), Layout(name="body", ratio=1), Layout(name="footer", size=3))
        self.layout["body"].split_row(Layout(name="sidebar", ratio=1), Layout(name="main", ratio=2))
        self.layout["sidebar"].split(Layout(name="integrations", size=10), Layout(name="logs", ratio=1))
        self.layout["main"].split(Layout(name="queue", size=10), Layout(name="agent_workspace", ratio=1), Layout(name="metrics", size=4))

    def _note_state(self, incident_id: str, state: str, detail: str = ""):
        msg = f"State: {state}"
        if detail:
            msg += f" | {detail}"
        self.add_thought(incident_id, msg)

    @staticmethod
    def _display_id(incident: dict) -> str:
        """Returns the Jira key (e.g. SRE-123) once available, falls back to INC-xxxx."""
        return incident.get('jira_key') or incident.get('id', 'UNKNOWN')

    def _set_state(self, target: dict, state: str, detail: str = ""):
        with self.ui_lock:
            target["state"] = state
        self._note_state(self._display_id(target), state, detail)

    def _sleep(self, seconds: float):
        if not FAST_DEMO_MODE:
            time.sleep(seconds)
            return
        time.sleep(max(0.05, seconds * 1.0))

    def _present_pause(self, label: str, seconds: float = 25.0):
        """Block the calling worker at a key demo moment. No-op when PRESENT_MODE is off."""
        if not PRESENT_MODE:
            return
        time.sleep(seconds)

    def _fast_gate(self, incident: dict, plan) -> GateOutput:
        severity = str(incident.get("severity", "")).upper()
        is_critical = severity in {"CRITICAL"} or incident.get("id") == "INC-9999"
        if is_critical:
            decision = "block_and_escalate"
            reasons = ["Critical severity requires human approval before remediation."]
            final_position = "escalate_to_human"
            confidence_final = max(1.0, float(plan.confidence_initial) - 2.0)
            arbiter_resolution = "escalate_for_human_approval"
        else:
            decision = "execute"
            reasons = ["Sufficient signal for safe auto-remediation (fast mode)."]
            final_position = plan.proposed_action
            confidence_final = float(plan.confidence_initial)
            arbiter_resolution = "accept_planner_action"
        confidence_delta = round(float(plan.confidence_initial) - float(confidence_final), 2)
        return GateOutput(
            incident_id=incident.get("id", ""),
            initial_position=plan.proposed_action,
            final_position=final_position,
            confidence_initial=float(plan.confidence_initial),
            confidence_final=float(confidence_final),
            confidence_delta=confidence_delta,
            act=1 if confidence_delta >= 2.0 or decision != "execute" else 0,
            iii=1.0,
            ri=0.0,
            per=0.2,
            adaptability_score=0.8 if decision == "execute" else 0.2,
            decision=decision,
            reasons=reasons,
            disagreement_detected=False,
            arbiter_resolution=arbiter_resolution,
        )

    def _fast_plan(self, incident: dict):
        from src.models import PlanOutput
        iid = incident.get("id", "")
        sev = str(incident.get("severity", "")).lower()
        if iid == "INC-9999" or sev == "critical":
            return PlanOutput(
                incident_id=iid,
                proposed_action="block_root_reset_and_escalate_to_security_team",
                rationale="Mass credential reset spike detected in auth-service. Root-level modification blocked pending human review.",
                key_claims=["Critical severity requires human approval before any remediation", "Root credential reset poses a total service blackout risk"],
                confidence_initial=7.5,
                retrieved_context_ids=[],
            )
        return PlanOutput(
            incident_id=iid,
            proposed_action="restart_api_pods_and_reset_connection_pool",
            rationale="5xx error cluster detected. Pod restart clears connection saturation without data loss risk.",
            key_claims=["Restarting API pods is safe and resolves connection saturation", "No data loss risk from a clean pod restart"],
            confidence_initial=7.0,
            retrieved_context_ids=[],
        )

    def add_thought(self, title, message):
        ts = datetime.now().strftime("%H:%M:%S")
        clean_msg = str(message).replace("\n", "\n  ")
        self.thought_queue.put(f"[bold cyan]{ts} ● {title}[/]\n  {clean_msg}")

    def add_log(self, message):
        self.log_queue.put(message)

    def sync_queues(self):
        try:
            while True:
                self.display_logs.append(self.log_queue.get_nowait())
                if len(self.display_logs) > 15: self.display_logs.pop(0)
        except queue.Empty: pass
        try:
            while True:
                self.display_thoughts.append(self.thought_queue.get_nowait())
                if len(self.display_thoughts) > 20: self.display_thoughts.pop(0)
        except queue.Empty: pass

    def generate_dashboard(self) -> Layout:
        self.sync_queues()
        self.heartbeat = (self.heartbeat + 1) % 100
        hb = "⚡" if self.heartbeat % 2 == 0 else "  "
        
        # Header
        h_grid = Table.grid(expand=True)
        h_grid.add_column(ratio=1); h_grid.add_column(justify="right")
        present_tag = " [bold yellow]⏸ PRESENTER MODE[/]" if PRESENT_MODE else ""
        h_grid.add_row(f"[bold cyan]RELIABILITY LAYER[/] [dim]v3.2 (Methodical Engine)[/]{present_tag} {hb}", f"[bold white]{datetime.now().strftime('%H:%M:%S')}[/]")
        self.layout["header"].update(Panel(h_grid, style="white on blue"))

        # Sidebar
        it = Table(show_header=False, box=None, padding=(0, 1))
        it.add_row("Elasticsearch", "[green]Online[/]")
        it.add_row("Jira Sync", "[green]Active[/]")
        it.add_row("Logs Processed", f"[bold cyan]{self.processed_logs:,}[/]")
        it.add_row("Knowledge Base", f"[bold cyan]Learned {self.kb_updates}[/]")
        pending = any(i['state'] in [IncidentState.PENDING_SLACK] for i in self.queue)
        it.add_row("Slack Loop", "[bold yellow]Action Needed[/]" if pending else "[green]Watching[/]")
        self.layout["integrations"].update(Panel(it, title="[bold]System Status[/]", border_style="cyan"))

        lt = Text()
        for l in self.display_logs:
            lt.append(f"{l}\n", style="bold red" if " 5" in l or " 4" in l else "green")
        self.layout["logs"].update(Panel(lt, title="[bold]ES Telemetry[/]", border_style="blue"))

        # Queue
        qt = Table(expand=True, box=None)
        qt.add_column("Jira ID", style="cyan", width=12); qt.add_column("Resource", style="white"); qt.add_column("Status", style="bold")
        with self.ui_lock:
            active_q = [i for i in self.queue if i['state'] != IncidentState.RESOLVED][-5:]
            if not active_q and self.queue: active_q = self.queue[-5:]
        for i in active_q:
            c = "white"
            if i['state'] == IncidentState.PENDING_SLACK: c = "bold yellow"
            if i['state'] == IncidentState.EXECUTING: c = "bold magenta"
            if i['state'] == IncidentState.RESOLVED: c = "bold green"
            qt.add_row(i.get('jira_key', 'SYNCING...'), i.get('pattern', 'N/A')[:30], f"[{c}]{i['state']}[/]")
        self.layout["queue"].update(Panel(qt, title="[bold]Remediation Queue[/bold]", border_style="white"))

        rt = Text.from_markup("\n\n".join(self.display_thoughts))
        self.layout["agent_workspace"].update(Panel(Align(rt, vertical="bottom"), title="[bold]Safety Governor Reasoning[/bold]", border_style="magenta"))

        mt = Table.grid(expand=True)
        mt.add_column(justify="center", ratio=1); mt.add_column(justify="center", ratio=1)
        mt.add_row(f"Unsafe Actions Blocked: [bold red]{sum(1 for i in self.queue if i.get('refused'))}[/]", f"Active Outages: [bold red]{self.active_errors}[/]")
        self.layout["metrics"].update(Panel(mt, title="[bold]Operational Metrics[/bold]", border_style="blue"))

        # Footer
        fg = Table.grid(expand=True)
        fg.add_column(width=4); fg.add_column(ratio=1); fg.add_column(justify="right")
        fmsg = f" TASK: {self.status_msg}"
        if self.current_pattern: fmsg += f" [dim]({self.current_pattern[:25]}...)[/]"
        fg.add_row(self.spinner if self.is_spinning else "", Text.from_markup(fmsg, style="bold white"), "[dim]Ctrl+C[/]")
        self.layout["footer"].update(Panel(fg, border_style="white"))

        return self.layout

    # --- Workers ---

    def worker_logs(self):
        while True:
            try:
                res = self.client._request_json("POST", "/kibana_sample_data_logs/_search", {"size": 1, "sort": [{"timestamp": {"order": "desc"}}]})
                hits = res.get("hits", {}).get("hits", [])
                if hits:
                    s = hits[0].get("_source", {})
                    self.add_log(f"[{datetime.now().strftime('%H:%M:%S')}] {s.get('verb')} {s.get('request')} -> {s.get('response')}")
                    self.processed_logs += 1
            except: pass
            time.sleep(1.5)

    def worker_audit(self):
        if PRESENT_MODE:
            # Hold for 60 s so presenter can walk through the TUI panels before
            # the first incident fires. No status message — TUI looks normal.
            time.sleep(30)
        audit_idx = 0
        while True:
            # STOP scanner if we have too many active items - prevents flooding
            active_count = len([i for i in self.queue if i['state'] != IncidentState.RESOLVED])
            if active_count >= 2:
                self._sleep(2); continue

            self.status_msg, self.is_spinning = "Governance Audit...", True
            try:
                for _ in range(5): 
                    self.processed_logs += random.randint(100, 300)
                    self._sleep(0.05)
                res = self.client._request_json("POST", "/kibana_sample_data_logs/_search", {"size": 0, "query": {"range": {"response.keyword": {"gte": "400"}}}, "aggs": {"p": {"terms": {"field": "request.keyword", "size": 10}}}})
                self.active_errors = res.get("hits", {}).get("total", {}).get("value", 0)
                
                with self.ui_lock:
                    audit_idx += 1
                    # MONEY SHOT — fires after ~15 s of quiet monitoring (6 × 3 s audit cycles)
                    if audit_idx == 6:
                        iid = "INC-9999"
                        self.queue.append({"id": iid, "pattern": "/api/v1/auth/reset_root", "state": IncidentState.DETECTED, "data": {"id": iid, "service": "auth-service", "summary": "Root Reset Spike", "symptoms": "Credential wipe", "severity": "critical"}})
                        self.add_thought("Scanner", "Detected high-risk cluster. Gating initialized.")
                        self._note_state(iid, IncidentState.DETECTED, "Critical incident queued.")

                    buckets = res.get("aggregations", {}).get("p", {}).get("buckets", [])
                    existing_patterns = [i['pattern'] for i in self.queue]
                    for b in buckets:
                        if b['key'] not in existing_patterns:
                            iid = f"INC-{random.randint(1000, 9999)}"
                            self.queue.append({"id": iid, "pattern": b['key'], "state": IncidentState.DETECTED, "data": {"id": iid, "service": "payment-service", "summary": f"Failure: {b['key']}", "symptoms": "5xx errors", "severity": "high"}})
                            self._note_state(iid, IncidentState.DETECTED, f"New incident from telemetry: {b['key']}")
                            break
            except: pass
            self.is_spinning, self.status_msg = False, "Monitoring Production"
            self._sleep(3)

    def worker_agent(self):
        """Methodical sequential worker. Finishes one step before moving to next log."""
        while True:
            with self.ui_lock:
                target = next((i for i in self.queue if i['state'] in [IncidentState.DETECTED, IncidentState.ANALYZING, IncidentState.READY_TO_EXECUTE, IncidentState.LEARNING]), None)
            if target:
                try:
                    if target['state'] == IncidentState.DETECTED:
                        self.status_msg, self.is_spinning, self.current_pattern = f"Jira Sync {target['id']}", True, target['pattern']

                        # 1. Jira Creation
                        jira_key = self.agent.jira_create_incident(target['data'], "Analyzing logs...")
                        target['jira_key'] = jira_key
                        self.add_thought("Jira", f"Ticket [cyan]{jira_key}[/] created for [bold]{target['data'].get('service')}[/].\n  Severity: {target['data'].get('severity', 'N/A').upper()} | Pattern: {escape(target['pattern'])}")
                        self._sleep(2)  # Pause — let viewer read the Jira creation

                        # 2. Plan — bypass LLM call in fast mode for instant Slack delivery
                        if FAST_DEMO_MODE:
                            plan = self._fast_plan(target['data'])
                            # Simulated agentic tool traces: what Agent Builder would call in live mode
                            self.agent._trace_tool("agent_builder", "agentic_plan", {
                                "tool_calls": ["search_runbooks"],
                                "service": target['data'].get('service'),
                                "simulated": True,
                            })
                            self.agent._trace_tool("search", "search_runbooks", {
                                "query": f"{target['data'].get('service')} {target['data'].get('summary', '')}",
                                "service": target['data'].get('service'),
                                "hits": 3,
                                "simulated": True,
                            })
                        else:
                            plan = self.agent.plan(target['data'])
                        target.update({"action": plan.proposed_action, "plan_obj": plan})
                        self._set_state(target, IncidentState.ANALYZING, "Plan generated.")
                        self.add_thought(self._display_id(target), f"Remediation plan:\n  Action: [bold yellow]{escape(plan.proposed_action)}[/]\n  Rationale: {escape(plan.rationale)}")
                        self._sleep(2)  # Pause — let viewer read the plan
                    
                    elif target['state'] == IncidentState.ANALYZING:
                        did = self._display_id(target)
                        self.status_msg = f"Safety Gate: {did}"
                        self.add_thought(did, "[dim]Running Safety Governor: stress-testing plan against evidence…[/]")
                        self._sleep(2)  # Pause — let viewer read "running checks"
                        if target['id'] == "INC-9999":
                            gate = GateOutput(target['id'], target['action'], "REJECTED", 0.9, 1.2, 0.78, 1, 1.0, 0.0, 0.2, 0.1, "block_and_escalate", ["CRITICAL RISK: Potential total service blackout"])
                            stress = StressOutput(target['id'], [ClaimEvidence("Safety", [], ["Root Reset"])], 1, [], True, 1.2, "REJECTED", 1.0)
                        else:
                            if FAST_DEMO_MODE:
                                gate = self._fast_gate(target['data'], target['plan_obj'])
                                # Simulated agentic tool traces: what Agent Builder would call in live mode
                                self.agent._trace_tool("agent_builder", "agentic_stress", {
                                    "tool_calls": ["search_evidence", "check_policy_conflicts", "query_live_logs"],
                                    "service": target['data'].get('service'),
                                    "simulated": True,
                                })
                                self.agent._trace_tool("search", "search_evidence", {
                                    "claims_checked": len(target['plan_obj'].key_claims),
                                    "service": target['data'].get('service'),
                                    "simulated": True,
                                })
                                self.agent._trace_tool("search", "check_policy_conflicts", {
                                    "service": target['data'].get('service'),
                                    "action": target['plan_obj'].proposed_action,
                                    "simulated": True,
                                })
                                self.agent._trace_tool("search", "query_live_logs", {
                                    "error_count": 142,
                                    "avg_bytes": 5820.0,
                                    "simulated": True,
                                })
                            else:
                                stress = self.agent.stress(target['data'], target['plan_obj'])
                                gate = self.agent.gate(target['plan_obj'], stress, self.agent.compress(target['data'], target['plan_obj'], stress))
                        decision_color = "bold green" if gate.decision == "execute" else "bold red"
                        self.add_thought(did, f"Safety Gate verdict: [{decision_color}]{gate.decision.upper()}[/]\n  Reason: {escape(' '.join(gate.reasons))}\n  Confidence: {gate.confidence_initial:.2f} → {gate.confidence_final:.2f}")
                        self._sleep(2)  # Pause — let viewer read the verdict
                        # Pause on critical gate block so presenter can explain the safety framework
                        if target['id'] == "INC-9999":
                            self._present_pause("Critical gate BLOCKED — explain CDCT/DDFT/EECT safety framework + gate logic", 35)

                        # 3. Slack Notify
                        chan = os.getenv("SLACK_CHANNEL_LABEL", "reliability").lstrip("#")
                        slack_p = {"incident_id": did, "service": target['data']['service'], "severity": target['data']['severity'], "decision": gate.decision, "execution_mode": gate.final_position, "reasons": gate.reasons, "confidence_initial": gate.confidence_initial, "confidence_final": gate.confidence_final, "confidence_delta": gate.confidence_delta, "support_docs_count": 1, "contradiction_docs_count": 0, "policy_conflicts_count": 0, "integration_quality": 1.0, "fabrication_trap_rejected": True, "disagreement_detected": False, "unsafe_action_rejected": "", "is_critical_hazard": (target['id'] == "INC-9999")}
                        msg_p = self.agent.workflow_client._format_slack_message(slack_p)
                        res = self.agent.workflow_client._slack_api_call(
                            "chat.postMessage",
                            {
                                "channel": chan,
                                "text": msg_p.get("text", f"Incident {did} update."),
                                "blocks": msg_p.get("blocks"),
                            },
                        )
                        target.update({"slack_ts": res.get("ts"), "slack_channel": res.get("channel"), "gate": gate})
                        # For the critical incident, hold so presenter can explain the
                        # Slack message, approval workflow, and Jira link.
                        if target['id'] == "INC-9999":
                            self._present_pause("", 45)

                        if gate.decision == "execute":
                            self._set_state(target, IncidentState.READY_TO_EXECUTE, "Auto-remediation approved.")
                            self.add_thought(did, "[bold green]✓ Safety Gate passed.[/] Queuing automated remediation.")
                        else:
                            self._set_state(target, IncidentState.PENDING_SLACK, "Awaiting Slack approval.")
                            self.add_thought(did, f"[bold yellow]⚠ Safety Gate blocked.[/] Confidence dropped to {gate.confidence_final:.2f}.\nSlack alert sent to #{chan} — waiting for human approval.")
                            if target.get('jira_key'): self.agent.jira.add_comment(target['jira_key'], "Safety Governor blocked auto-remediation. Awaiting Slack approval.")
                    
                    elif target['state'] == IncidentState.READY_TO_EXECUTE:
                        did = self._display_id(target)
                        self.status_msg = f"Executing fix: {did}"
                        self.add_thought(did, f"[bold magenta]▶ EXECUTING REMEDIATION[/]\n  Action: {escape(target.get('action', 'N/A'))}\n  Service: {target['data'].get('service')}")
                        self._sleep(3)  # Pause — let viewer see execution in progress
                        if target.get('jira_key'): self.agent.jira_resolve_incident(target['jira_key'], "Autonomous SRE")
                        self.add_thought(did, "[bold green]✓ Remediation applied.[/] Jira ticket resolved. Indexing resolution.")
                        self._sleep(2)  # Pause — let viewer read the outcome
                        self._set_state(target, IncidentState.LEARNING, "Remediation complete.")

                    elif target['state'] == IncidentState.LEARNING:
                        did = self._display_id(target)
                        self.add_thought("Learning Engine", f"Indexing resolution for [cyan]{did}[/] into knowledge base.")
                        self._sleep(2)  # Pause — let viewer read the learning step
                        if not FAST_DEMO_MODE:
                            try: self.agent.learn_from_resolution(target['data'], f"Executed {target['action']}. Resolved.")
                            except: pass
                        self.kb_updates += 1
                        self._set_state(target, IncidentState.RESOLVED, "Incident closed.")
                        self.add_thought(did, "[bold green]✓ RESOLVED.[/] Knowledge base updated. Incident closed.")
                except Exception as e:
                    self.add_thought("System", f"Worker Error: {str(e)}")
                    if target: target['state'] = IncidentState.RESOLVED
                finally: self.is_spinning, self.current_pattern = False, None
            self._sleep(0.5)

    def worker_slack(self):
        """Tight polling loop — state is updated immediately before any API calls."""
        bot_id = self.agent.workflow_client._slack_bot_user_id()

        while True:
            with self.ui_lock:
                targets = [i for i in self.queue if i['state'] == IncidentState.PENDING_SLACK]

            for inc in targets:
                if not inc.get('slack_ts'): continue
                try:
                    chan_id = inc.get('slack_channel')
                    if not chan_id: continue

                    replies = self.agent.workflow_client.get_thread_replies(chan_id, inc['slack_ts'])

                    user_cmd = None
                    for m in replies:
                        if m.get("user") == bot_id: continue
                        t = m.get("text", "").lower()
                        if "force_override" in t: user_cmd = "force"; break
                        if any(k in t for k in ["approve", "yes", "confirm", "ok"]): user_cmd = "approve"; break

                    # Fallback: channel history (non-threaded replies)
                    if not user_cmd:
                        history = self.agent.workflow_client.get_channel_history(chan_id)
                        for m in history:
                            if m.get("user") == bot_id: continue
                            t = m.get("text", "").lower()
                            jk = str(inc.get('jira_key', '')).lower()
                            if jk and jk in t:
                                if "force_override" in t: user_cmd = "force"; break
                                if any(k in t for k in ["approve", "yes", "ok"]): user_cmd = "approve"; break

                    if user_cmd:
                        gate = inc.get('gate')
                        is_crit = gate and (gate.confidence_final < REFUSAL_THRESHOLD or "CRITICAL" in " ".join(gate.reasons))

                        did = self._display_id(inc)
                        if user_cmd == "approve" and is_crit and not inc.get('refused'):
                            # Mark immediately to prevent re-processing on next poll cycle
                            inc['refused'] = True
                            self.add_thought("Safety Governor", f"[bold red]⛔ REFUSED:[/] {did} is a critical incident.\n  `APPROVE` is blocked — reply [bold]FORCE_OVERRIDE[/] to proceed.")
                            self.agent.workflow_client.post_reply(
                                chan_id, inc['slack_ts'],
                                "⛔ *Safety Refusal:* This incident involves a critical system risk (potential total service outage).\n\n"
                                "Auto-approval is blocked by the Safety Governor.\n\n"
                                "Reply `FORCE_OVERRIDE` in this thread to manually override the safety gate.\n"
                                "_This override will be permanently logged for audit._"
                            )
                            # Pause so presenter can explain why APPROVE was refused and
                            # walk the audience through the FORCE_OVERRIDE requirement.
                            self._present_pause("APPROVE refused — explain critical gating + FORCE_OVERRIDE requirement", 35)
                        else:
                            # Update state IMMEDIATELY — before the post_reply API call
                            self._set_state(inc, IncidentState.READY_TO_EXECUTE, "Slack approval received.")
                            msg = "🚀 *Override accepted.*" if user_cmd == "force" else "✅ *Approval received.*"
                            self.add_thought("Slack", f"{msg} Resuming execution of [cyan]{did}[/].")
                            self.agent.workflow_client.post_reply(chan_id, inc['slack_ts'], f"{msg} Resuming execution.")

                except Exception: pass
            self._sleep(0.1)

    def run(self):
        for t in [self.worker_logs, self.worker_audit, self.worker_agent, self.worker_slack]:
            threading.Thread(target=t, daemon=True).start()
        self.add_thought("System", "Reliability Layer Agent initialized.")
        with Live(self.layout, refresh_per_second=15, screen=True) as live:
            while True:
                live.update(self.generate_dashboard())
                time.sleep(0.06)

if __name__ == "__main__":
    try: AgentDemo().run()
    except KeyboardInterrupt: sys.exit(0)
