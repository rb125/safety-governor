from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict


class WorkflowClient:
    """
    Optional workflow trigger client.

    If `workflow_id` is configured, attempts Kibana Workflows endpoint first.
    If `webhook_url` is configured, sends the same payload to the webhook as fallback.
    """

    def __init__(
        self,
        kibana_url: str,
        api_key: str,
        workflow_id: str = "",
        webhook_url: str = "",
        admin_webhook_url: str = "",
        admin_mention: str = "rahul",
        admin_user_id: str = "",
        channel_label: str = "reliability",
        slack_bot_token: str = "",
        urgent_dm_on_escalation: bool = True,
    ):
        self.kibana_url = kibana_url.rstrip("/")
        self.api_key = api_key
        self.workflow_id = workflow_id
        self.webhook_url = webhook_url
        self.admin_webhook_url = admin_webhook_url
        self.admin_mention = admin_mention
        self.admin_user_id = admin_user_id
        self.channel_label = channel_label
        self.slack_bot_token = slack_bot_token
        self.urgent_dm_on_escalation = urgent_dm_on_escalation
        self._bot_user_id: str | None = None

    def trigger(self, payload: Dict) -> Dict:
        # Detect if this is a Slack webhook for better formatting
        is_slack = "hooks.slack.com" in self.webhook_url
        final_payload = self._format_slack_message(payload) if is_slack else payload

        if self.workflow_id:
            path = f"/api/workflows/{self.workflow_id}/_execute"
            try:
                data = self._request_json(self.kibana_url + path, payload, kibana=True)
                return {"status": "triggered", "channel": "kibana_workflow", "response": data}
            except Exception as e:
                if self.webhook_url:
                    try:
                        data = self._request_json(self.webhook_url, final_payload, kibana=False)
                        return {
                            "status": "triggered",
                            "channel": "webhook_fallback",
                            "warning": f"Kibana workflow failed: {e}",
                            "response": data,
                        }
                    except Exception as e2:
                        return {"status": "failed", "channel": "none", "error": f"{e}; webhook: {e2}"}
                return {"status": "failed", "channel": "kibana_workflow", "error": str(e)}

        if self.webhook_url:
            try:
                data = self._request_json(self.webhook_url, final_payload, kibana=False)
                admin_result = self._send_admin_summary(payload)
                dm_result = self._send_urgent_dm(payload)
                out = {"status": "triggered", "channel": "webhook", "response": data}
                if admin_result:
                    out["admin_delivery"] = admin_result
                if dm_result:
                    out["urgent_dm"] = dm_result
                return out
            except Exception as e:
                return {"status": "failed", "channel": "webhook", "error": str(e)}

        return {
            "status": "skipped",
            "channel": "none",
            "reason": "No WORKFLOW_ID or WORKFLOW_WEBHOOK_URL configured",
        }

    def _format_slack_message(self, p: Dict) -> Dict:
        """Formats reliability gate results into a concise, actionable Slack message."""
        severity = str(p.get("severity", "medium")).upper()
        decision = str(p.get("decision", "unknown")).upper()
        decision_label = self._decision_label(decision)
        action_mode = self._normalize_text(str(p.get("execution_mode", "n/a")))
        reasons = p.get("reasons", ["No specific reasons logged."])
        top_reason = self._normalize_text(str(reasons[0])) if reasons else "No reason captured."
        incident_id = str(p.get("incident_id", "unknown"))
        service = str(p.get("service", "unknown"))
        confidence_initial = p.get("confidence_initial", "n/a")
        confidence_final = p.get("confidence_final", "n/a")
        confidence_delta = p.get("confidence_delta", "n/a")
        support_docs_count = int(p.get("support_docs_count", 0) or 0)
        contradiction_docs_count = int(p.get("contradiction_docs_count", 0) or 0)
        policy_conflicts_count = int(p.get("policy_conflicts_count", 0) or 0)
        integration_quality = p.get("integration_quality", 0.0)
        trap_rejected = bool(p.get("fabrication_trap_rejected", False))
        disagreement = bool(p.get("disagreement_detected", False))
        risk_level = self._risk_level(
            decision=decision,
            severity=severity,
            support_docs_count=support_docs_count,
            contradiction_docs_count=contradiction_docs_count,
            confidence_delta=float(confidence_delta) if isinstance(confidence_delta, (int, float)) else 0.0,
        )
        steps = self._extract_steps(action_mode, max_steps=3)
        links = self._build_elastic_links(service=service, incident_id=incident_id)
        why_not = self._why_not_lines(p)
        verifier_result = "No critical contradictions detected" if contradiction_docs_count == 0 else f"Critical contradictions detected ({contradiction_docs_count})"
        fallback_text = (
            f"Incident Update - {service} ({severity})\n"
            f"Incident: {incident_id}\n"
            f"Decision: {decision_label}\n"
            f"Confidence Before/After: {confidence_initial} -> {confidence_final} (delta {confidence_delta})\n"
            f"Risk: {risk_level}\n"
            f"Reason: {top_reason}\n"
            f"Next:\n" + "\n".join(f"- {s}" for s in steps)
        )

        next_actions = "\n".join(f"• {s}" for s in steps) if steps else "• Follow runbook and verify service health."
        link_text = " | ".join(f"<{u}|{label}>" for label, u in links.items() if u)
        channel_name = self.channel_label.lstrip("#")
        status_emoji = "✅" if decision == "EXECUTE" else "⛔"
        is_hazard = bool(p.get("is_critical_hazard", False))
        hazard_prefix = "🚨 [CRITICAL HAZARD] " if is_hazard else ""
        status_header = f"{status_emoji} {hazard_prefix}Incident Update - {service} ({severity})"
        confidence_line = f"{confidence_initial} -> {confidence_final} (delta {confidence_delta})"
        evidence_lines = [
            f"• Supporting documents found: {support_docs_count}",
            f"• Contradictions found: {contradiction_docs_count}",
            f"• Policy conflicts: {policy_conflicts_count}",
            f"• Integration quality score: {integration_quality}",
            f"• Verifier result: {verifier_result}",
            f"• Risk level: {risk_level}",
        ]
        if disagreement:
            evidence_lines.append("• Planner/Verifier disagreement detected")
        evidence_summary = "\n".join(evidence_lines)
        safety_reason = "Fabrication trap triggered — evidence flagged as suspicious" if trap_rejected else "Fabrication check passed — no fabricated evidence detected"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": status_header}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident*\n`{incident_id}`"},
                    {"type": "mrkdwn", "text": f"*Service*\n`{service}`"},
                    {"type": "mrkdwn", "text": f"*Severity*\n`{severity}`"},
                    {"type": "mrkdwn", "text": f"*Decision*\n`{decision_label}`"},
                    {"type": "mrkdwn", "text": f"*Confidence (Before → After)*\n`{confidence_line}`"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Safety Gate*\nAGT Protocol 1.0"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Why This Decision*\n{top_reason}\n• {safety_reason}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Evidence Summary*\n{evidence_summary}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Actions {'Executing' if decision == 'EXECUTE' else 'Recommended'}*\n{next_actions}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Rejected Alternatives*\n{why_not}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Open in Elastic*\n{link_text}"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Channel: #{channel_name}"},
                    {"type": "mrkdwn", "text": f"Admin: {self._slack_mention_token()}"},
                    {"type": "mrkdwn", "text": "Safety Gate: AGT Protocol 1.0"},
                ]
            }
        ]
        return {
            "channel": f"#{channel_name}",
            "text": fallback_text,
            "blocks": blocks,
            "link_names": 1,
            "unfurl_links": False,
            "unfurl_media": False,
        }

    def _send_admin_summary(self, payload: Dict) -> Dict | None:
        target_url = self.admin_webhook_url or self.webhook_url
        if not target_url:
            return None
        summary = self._format_admin_summary(payload)
        try:
            data = self._request_json(target_url, summary, kibana=False)
            return {"status": "triggered", "channel": "admin_webhook", "response": data}
        except Exception as e:
            return {"status": "failed", "channel": "admin_webhook", "error": str(e)}

    def _format_admin_summary(self, p: Dict) -> Dict:
        decision = str(p.get("decision", "unknown")).upper()
        decision_label = self._decision_label(decision)
        admin_mention = self._slack_mention_token()
        action = self._normalize_text(str(p.get("execution_mode", "n/a")))
        steps = self._extract_steps(action, max_steps=3)
        top_reason = self._normalize_text(str((p.get("reasons") or ["No reason captured."])[0]))
        confidence_initial = p.get("confidence_initial", "n/a")
        confidence_final = p.get("confidence_final", "n/a")
        confidence_delta = p.get("confidence_delta", "n/a")
        channel_name = self.channel_label.lstrip("#")
        summary_text = (
            f"{admin_mention} {decision_label} for incident `{p.get('incident_id')}` "
            f"on `{p.get('service')}` ({str(p.get('severity', 'n/a')).upper()}). "
            f"Confidence: {confidence_initial} -> {confidence_final} (delta {confidence_delta})."
        )
        actions_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps)) or "1. Follow service runbook and verify health."
        return {
            "channel": f"#{channel_name}",
            "text": summary_text,
            "link_names": 1,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "Admin Summary"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": summary_text},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Reason*\n{top_reason}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Recommended Actions*\n{actions_text}"},
                },
            ],
        }

    def _format_admin_line(self) -> str:
        mention = (self.admin_mention or "rahul").strip()
        # If already a Slack mention token like <@U123>, preserve it.
        if mention.startswith("<@") and mention.endswith(">"):
            return f"*Admin:* {mention}"
        # If env has raw user id (e.g., U012ABCDEF), convert to mention token.
        if mention.startswith("U") and " " not in mention:
            return f"*Admin:* <@{mention}>"
        # Otherwise keep readable handle.
        if mention.startswith("@"):
            return f"*Admin:* {mention}"
        return f"*Admin:* @{mention}"

    def _slack_mention_token(self) -> str:
        mention = (self.admin_mention or "rahul").strip()
        if mention.startswith("<@") and mention.endswith(">"):
            return mention
        if mention.startswith("U") and " " not in mention:
            return f"<@{mention}>"
        if mention.startswith("@"):
            return mention
        return f"@{mention}"

    @staticmethod
    def _compact_action_text(action: str, limit: int = 240) -> str:
        clean = " ".join(action.split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 3] + "..."

    @staticmethod
    def _extract_steps(action: str, max_steps: int = 3) -> list[str]:
        text = WorkflowClient._normalize_text(action).strip()
        if not text:
            return []
        # Split on numbered steps and semicolons.
        parts = re.split(r"(?:\s*\d+\.\s+|\s*;\s*|\n+)", text)
        steps = [p.strip() for p in parts if p.strip()]
        if not steps:
            steps = [text]
        return [WorkflowClient._compact_action_text(s, limit=280) for s in steps[:max_steps]]

    def _build_elastic_links(self, service: str, incident_id: str) -> Dict[str, str]:
        kb = self.kibana_url.rstrip("/")
        service_q = urllib.parse.quote(service or "", safe="")
        discover_query = f"service.name:\"{service}\" OR service:\"{service}\" OR incident_id:\"{incident_id}\""
        discover_query_q = urllib.parse.quote(discover_query, safe="")
        wf_query_q = urllib.parse.quote(f"incident_id:\"{incident_id}\"", safe="")
        return {
            "Discover (Service Logs)": f"{kb}/app/discover#/?_a=(query:(language:kuery,query:'{discover_query_q}'))",
            "Discover (Workflow Events)": f"{kb}/app/discover#/?_a=(query:(language:kuery,query:'{wf_query_q}'))",
            "Stack Management": f"{kb}/app/management/data/index_management/indices",
            "Discover Home": f"{kb}/app/discover",
        }

    @staticmethod
    def _normalize_text(value: str) -> str:
        # Some model/tool outputs contain literal '\n' sequences instead of newlines.
        return value.replace("\\n", "\n").replace("\\t", "\t").strip()

    @staticmethod
    def _decision_label(decision: str) -> str:
        d = (decision or "").upper()
        if d == "EXECUTE":
            return "Auto-remediation Approved"
        if d in {"ESCALATE", "REVIEW", "BLOCK_AND_ESCALATE"}:
            return "Human Escalation Required"
        if d == "BLOCK":
            return "Action Blocked by Safety Gate"
        return f"Decision: {d or 'UNKNOWN'}"

    def _is_urgent(self, payload: Dict) -> bool:
        if not self.urgent_dm_on_escalation:
            return False
        decision = str(payload.get("decision", "")).upper()
        severity = str(payload.get("severity", "")).upper()
        return decision in {"ESCALATE", "REVIEW", "BLOCK", "BLOCK_AND_ESCALATE"} or severity in {"HIGH", "CRITICAL"}

    def _send_urgent_dm(self, payload: Dict) -> Dict | None:
        if not self._is_urgent(payload):
            return None
        user_id = self._slack_user_id()
        if not user_id or not self.slack_bot_token:
            return {
                "status": "skipped",
                "reason": "Missing SLACK_BOT_TOKEN or Slack user id for urgent DM",
            }
        bot_user_id = self._slack_bot_user_id()
        if bot_user_id and user_id == bot_user_id:
            return {
                "status": "failed",
                "channel": "slack_dm",
                "error": (
                    "Target Slack user id matches bot user id. "
                    "Set SLACK_ADMIN_USER_ID to Rahul's member ID (e.g., U...)."
                ),
            }
        target_info = self._slack_users_info(user_id)
        if not target_info.get("ok"):
            return {
                "status": "failed",
                "channel": "slack_dm",
                "target_user_id": user_id,
                "error": (
                    "Unable to verify SLACK_ADMIN_USER_ID via Slack users.info. "
                    f"error={target_info.get('error')}"
                ),
            }
        user_obj = target_info.get("user", {}) or {}
        if bool(user_obj.get("is_bot", False)):
            return {
                "status": "failed",
                "channel": "slack_dm",
                "target_user_id": user_id,
                "error": (
                    "SLACK_ADMIN_USER_ID resolves to a bot/app user, not Rahul. "
                    "Use Rahul's human member ID from Slack profile."
                ),
            }
        decision_raw = str(payload.get("decision", "unknown")).upper()
        decision_label = self._decision_label(decision_raw)
        steps = self._extract_steps(self._normalize_text(str(payload.get("execution_mode", "n/a"))), max_steps=3)
        reason = self._normalize_text(str((payload.get("reasons") or ["No reason captured."])[0]))
        why_not = self._why_not_lines(payload)
        confidence_initial = payload.get("confidence_initial", "n/a")
        confidence_final = payload.get("confidence_final", "n/a")
        confidence_delta = payload.get("confidence_delta", "n/a")
        links = self._build_elastic_links(str(payload.get("service", "")), str(payload.get("incident_id", "")))
        links_line = " | ".join(f"<{u}|{label}>" for label, u in links.items() if u)
        status_prefix = "✅" if decision_raw == "EXECUTE" else "⛔"
        text = (
            f"{status_prefix} {decision_label}\n"
            f"Incident `{payload.get('incident_id')}` on `{payload.get('service')}` "
            f"({str(payload.get('severity', '')).upper()}).\n"
            f"Confidence: {confidence_initial} → {confidence_final} (delta {confidence_delta})\n"
            f"Reason: {reason}\n"
            f"Rejected alternatives: {why_not}\n"
            f"Immediate next steps:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) + "\n"
            f"Links: {links_line}"
        )
        try:
            dm_channel = self._slack_open_dm(user_id)
            dm_info = self._slack_conversation_info(dm_channel)
            response = self._slack_post_message(dm_channel, {"text": text, "link_names": 1})
            return {
                "status": "triggered",
                "channel": "slack_dm",
                "target_user_id": user_id,
                "bot_user_id": bot_user_id,
                "dm_channel_id": dm_channel,
                "dm_channel_user_id": dm_info.get("channel", {}).get("user"),
                "response": response,
            }
        except Exception as e:
            return {"status": "failed", "channel": "slack_dm", "error": str(e)}

    def _slack_user_id(self) -> str:
        explicit = (self.admin_user_id or "").strip()
        if explicit.startswith("U") and " " not in explicit:
            return explicit
        mention = (self.admin_mention or "").strip()
        if mention.startswith("<@") and mention.endswith(">"):
            return mention[2:-1]
        if mention.startswith("U") and " " not in mention:
            return mention
        return ""

    def _slack_bot_user_id(self) -> str:
        if self._bot_user_id:
            return self._bot_user_id
        try:
            data = self._slack_api_call("auth.test", {})
            self._bot_user_id = str(data.get("user_id") or "")
            return self._bot_user_id
        except Exception:
            return ""

    def _slack_open_dm(self, user_id: str) -> str:
        data = self._slack_api_call("conversations.open", {"users": user_id})
        channel = data.get("channel", {}) or {}
        channel_id = channel.get("id")
        if not channel_id:
            raise RuntimeError(f"Slack conversations.open missing channel id: {data}")
        return channel_id

    def _slack_post_message(self, channel_id: str, payload: Dict) -> Dict:
        body = dict(payload)
        body["channel"] = channel_id
        return self._slack_api_call("chat.postMessage", body)

    def _slack_conversation_info(self, channel_id: str) -> Dict:
        try:
            return self._slack_api_call("conversations.info", {"channel": channel_id})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def post_reply(self, channel_id: str, thread_ts: str, text: str) -> Dict:
        """Sends a reply to a specific Slack thread."""
        if not self.slack_bot_token:
            return {"ok": False, "error": "No bot token"}
        return self._slack_api_call("chat.postMessage", {
            "channel": channel_id,
            "thread_ts": thread_ts,
            "text": text
        })

    def get_thread_replies(self, channel_id: str, thread_ts: str) -> List[Dict]:
        """Fetches all messages in a specific Slack thread using GET."""
        if not self.slack_bot_token:
            return []
        try:
            params = urllib.parse.urlencode({"channel": channel_id, "ts": thread_ts})
            return self._slack_api_call(f"conversations.replies?{params}", None).get("messages", [])
        except Exception:
            return []

    def get_channel_history(self, channel_id: str, limit: int = 10) -> List[Dict]:
        """Fetches recent messages from a channel to look for non-threaded approvals."""
        if not self.slack_bot_token:
            return []
        try:
            params = urllib.parse.urlencode({"channel": channel_id, "limit": limit})
            return self._slack_api_call(f"conversations.history?{params}", None).get("messages", [])
        except Exception:
            return []

    def _slack_api_call(self, method: str, payload: Dict | None) -> Dict:
        if not self.slack_bot_token:
            raise RuntimeError("SLACK_BOT_TOKEN is not configured")
        url = f"https://slack.com/api/{method}"
        
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url=url, data=body, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
        else:
            req = urllib.request.Request(url=url, method="GET")
            
        req.add_header("Authorization", f"Bearer {self.slack_bot_token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if not data.get("ok", False):
                # Silently log error to console for debugging but return empty
                print(f"Slack API {method} failed: {data.get('error')}")
                return {"ok": False}
            return data

    def _slack_users_info(self, user_id: str) -> Dict:
        try:
            return self._slack_api_call("users.info", {"user": user_id})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _why_not_lines(payload: Dict) -> str:
        rejected = WorkflowClient._normalize_text(str(payload.get("unsafe_action_rejected", "")))
        contradictions = int(payload.get("contradiction_docs_count", 0) or 0)
        policy = int(payload.get("policy_conflicts_count", 0) or 0)
        lines = []
        if rejected:
            lines.append(f"Rejected broad action: {WorkflowClient._compact_action_text(rejected, limit=160)}")
        if contradictions > 0:
            lines.append(f"Contradictions detected: {contradictions}")
        if policy > 0:
            lines.append(f"Policy conflicts detected: {policy}")
        if not lines:
            lines.append("No risky alternatives were considered.")
        return "\n".join(f"• {x}" for x in lines)

    @staticmethod
    def _risk_level(
        decision: str,
        severity: str,
        support_docs_count: int,
        contradiction_docs_count: int,
        confidence_delta: float,
    ) -> str:
        if decision in {"BLOCK_AND_ESCALATE", "BLOCK"}:
            return "High"
        if contradiction_docs_count > 0 or confidence_delta >= 3.0:
            return "Medium"
        if severity == "CRITICAL" and support_docs_count == 0:
            return "Medium"
        return "Controlled"

    def _request_json(self, url: str, payload: Dict, kibana: bool) -> Dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if kibana:
            req.add_header("Authorization", f"ApiKey {self.api_key}")
            req.add_header("kbn-xsrf", "true")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except Exception:
                    # Slack incoming webhooks return plain text like "ok".
                    return {"raw": raw}
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {details}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error: {e}") from e
