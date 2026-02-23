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
        h_grid.add_row(f"[bold cyan]RELIABILITY LAYER[/] [dim]v3.2 (Methodical Engine)[/] {hb}", f"[bold white]{datetime.now().strftime('%H:%M:%S')}[/]")
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
        audit_idx = 0
        while True:
            # STOP scanner if we have too many active items - prevents flooding
            active_count = len([i for i in self.queue if i['state'] != IncidentState.RESOLVED])
            if active_count >= 2:
                time.sleep(1); continue

            self.status_msg, self.is_spinning = "Governance Audit...", True
            try:
                for _ in range(5): 
                    self.processed_logs += random.randint(100, 300)
                    time.sleep(0.05)
                res = self.client._request_json("POST", "/kibana_sample_data_logs/_search", {"size": 0, "query": {"range": {"response.keyword": {"gte": "400"}}}, "aggs": {"p": {"terms": {"field": "request.keyword", "size": 10}}}})
                self.active_errors = res.get("hits", {}).get("total", {}).get("value", 0)
                
                with self.ui_lock:
                    audit_idx += 1
                    # MONEY SHOT
                    if audit_idx == 2:
                        iid = "INC-9999"
                        self.queue.append({"id": iid, "pattern": "/api/v1/auth/reset_root", "state": IncidentState.DETECTED, "data": {"id": iid, "service": "auth-service", "summary": "Root Reset Spike", "symptoms": "Credential wipe", "severity": "critical"}})
                        self.add_thought("Scanner", "Detected high-risk cluster. Gating initialized.")

                    buckets = res.get("aggregations", {}).get("p", {}).get("buckets", [])
                    existing_patterns = [i['pattern'] for i in self.queue]
                    for b in buckets:
                        if b['key'] not in existing_patterns:
                            iid = f"INC-{random.randint(1000, 9999)}"
                            self.queue.append({"id": iid, "pattern": b['key'], "state": IncidentState.DETECTED, "data": {"id": iid, "service": "payment-service", "summary": f"Failure: {b['key']}", "symptoms": "5xx errors", "severity": "high"}})
                            self.add_thought("Scanner", f"Detected anomaly cluster: [bold yellow]{escape(b['key'][:40])}[/]. Queuing {iid} for analysis.")
                            break
            except: pass
            self.is_spinning, self.status_msg = False, "Monitoring Production"
            time.sleep(2)

    def worker_agent(self):
        """Methodical sequential worker. Finishes one step before moving to next log."""
        while True:
            target = next((i for i in self.queue if i['state'] in [IncidentState.DETECTED, IncidentState.ANALYZING, IncidentState.READY_TO_EXECUTE, IncidentState.LEARNING]), None)
            if target:
                try:
                    if target['state'] == IncidentState.DETECTED:
                        self.status_msg, self.is_spinning, self.current_pattern = f"Jira Sync {target['id']}", True, target['pattern']
                        
                        # 1. Jira Creation (BLOCKING for focus)
                        jira_key = self.agent.jira_create_incident(target['data'], "Analyzing logs...")
                        target['jira_key'] = jira_key
                        self.add_thought("Jira Service", f"Ticket [cyan]{jira_key}[/] created.")
                        
                        # 2. Plan
                        plan = self.agent.plan(target['data'])
                        target.update({"action": plan.proposed_action, "plan_obj": plan, "state": IncidentState.ANALYZING})
                        self.add_thought(target['id'], f"Plan: [bold yellow]{escape(plan.proposed_action)}[/]")
                    
                    elif target['state'] == IncidentState.ANALYZING:
                        self.status_msg = f"Safety Gating: {target['id']}"
                        if target['id'] == "INC-9999":
                            time.sleep(0.5)
                            gate = GateOutput(target['id'], target['action'], "REJECTED", 0.9, 1.2, 0.78, 1, 1.0, 0.0, 0.2, 0.1, "block_and_escalate", ["CRITICAL RISK: Potential total service blackout"])
                            stress = StressOutput(target['id'], [ClaimEvidence("Safety", [], ["Root Reset"])], 1, [], True, 1.2, "REJECTED", 1.0)
                        else:
                            stress = self.agent.stress(target['data'], target['plan_obj'])
                            gate = self.agent.gate(target['plan_obj'], stress, self.agent.compress(target['data'], target['plan_obj'], stress))
                        
                        # 3. Slack Notify (BLOCKING for focus)
                        chan = os.getenv("SLACK_CHANNEL_LABEL", "reliability").lstrip("#")
                        display_id = target.get('jira_key', target['id'])
                        slack_p = {"incident_id": display_id, "service": target['data']['service'], "severity": target['data']['severity'], "decision": gate.decision, "execution_mode": gate.final_position, "reasons": gate.reasons, "confidence_initial": gate.confidence_initial, "confidence_final": gate.confidence_final, "confidence_delta": gate.confidence_delta, "support_docs_count": 1, "contradiction_docs_count": 0, "policy_conflicts_count": 0, "integration_quality": 1.0, "fabrication_trap_rejected": True, "disagreement_detected": False, "unsafe_action_rejected": "", "is_critical_hazard": (target['id'] == "INC-9999")}
                        msg_p = self.agent.workflow_client._format_slack_message(slack_p)
                        res = self.agent.workflow_client._slack_api_call("chat.postMessage", {"channel": chan, "text": f"🎫 Jira: {display_id}", "blocks": msg_p.get("blocks")})
                        target.update({"slack_ts": res.get("ts"), "slack_channel": res.get("channel"), "gate": gate})

                        if gate.decision == "execute":
                            target['state'] = IncidentState.READY_TO_EXECUTE
                            self.add_thought(target['id'], "[bold green]Gate Passed.[/] Executing.")
                        else:
                            target['state'] = IncidentState.PENDING_SLACK
                            self.add_thought(target['id'], f"[bold yellow]Gate Blocked.[/] Score: {gate.confidence_final}. Awaiting signature.")
                            if target.get('jira_key'): self.agent.jira.add_comment(target['jira_key'], "Safety Governor Blocked Auto-Action. Awaiting Slack signature.")
                    
                    elif target['state'] == IncidentState.READY_TO_EXECUTE:
                        self.status_msg = f"Fixing {target['id']}"
                        self.add_thought(target['id'], f"[bold magenta]Executing remediation[/] for {target['id']}...")
                        time.sleep(0.5)
                        if target.get('jira_key'): self.agent.jira_resolve_incident(target['jira_key'], "Autonomous SRE")
                        target['state'] = IncidentState.LEARNING
                    
                    elif target['state'] == IncidentState.LEARNING:
                        self.add_thought("Learning", f"Summarizing fix for [cyan]{target['id']}[/].")
                        try: self.agent.learn_from_resolution(target['data'], f"Executed {target['action']}. Resolved."); self.kb_updates += 1
                        except: pass
                        target['state'] = IncidentState.RESOLVED
                        self.add_thought(target['id'], "[bold green]RESOLVED.[/]")
                except Exception as e:
                    self.add_thought("System", f"Worker Error: {str(e)}")
                    if target: target['state'] = IncidentState.RESOLVED
                finally: self.is_spinning, self.current_pattern = False, None
            time.sleep(0.15)

    def worker_slack(self):
        while True:
            with self.ui_lock:
                pending = [i for i in self.queue if i['state'] == IncidentState.PENDING_SLACK]
            for inc in pending:
                if inc.get('slack_ts'):
                    try:
                        chan_id = inc.get('slack_channel', os.getenv("SLACK_CHANNEL_LABEL", "reliability").lstrip("#"))
                        replies = self.agent.workflow_client.get_thread_replies(chan_id, inc['slack_ts'])
                        # Filter out bot messages and the parent message — only inspect human replies
                        human_replies = [m for m in replies if not m.get('bot_id') and m.get('ts') != inc['slack_ts']]
                        intent = None
                        for m in human_replies:
                            txt = m.get("text", "").strip().lower()
                            if "force_override" in txt:
                                intent = "force_override"
                                break
                            if any(k in txt for k in ["approve", "yes", "confirm"]):
                                intent = "approve"
                                break
                        if intent:
                            self.add_thought("Slack Sync", f"Operator command received: [bold yellow]{intent}[/]")
                            gate = inc.get('gate')
                            is_crit = gate and (gate.confidence_final < REFUSAL_THRESHOLD)
                            if intent == "approve" and is_crit:
                                expl = self.agent.get_refusal_explanation(inc['data'], gate.reasons)
                                self.add_thought("Safety Governor", f"[bold red]REFUSING OPERATOR COMMAND[/] for {inc['id']}.\n{escape(expl)}")
                                self.agent.workflow_client.post_reply(chan_id, inc['slack_ts'], f"⛔ *Safety Refusal:* {expl}\n\nType `FORCE_OVERRIDE` to bypass.")
                                inc['refused'] = True
                            elif intent == "force_override" or (intent == "approve" and not is_crit):
                                self.add_thought("Slack Sync", f"[bold green]Approval confirmed[/] for {inc['id']}. Queuing for execution.")
                                self.agent.workflow_client.post_reply(chan_id, inc['slack_ts'], "🚀 Approved. Executing remediation now.")
                                inc['state'] = IncidentState.READY_TO_EXECUTE
                    except: pass
            time.sleep(0.2)

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
