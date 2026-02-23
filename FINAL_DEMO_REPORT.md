# Final Demo Report

Date: 2026-02-21
Project: `Reliability Layer Agent for Incident Remediation`

## Demo Entry Point

One-command live demo:

```bash
python3 demo_live_agentic_workflow.py
```

This runs:
1. Baseline high-severity incident
2. Reliability-layer high-severity incident
3. Reliability-layer medium-severity incident
4. Fetches latest `action_executions` and `workflow_events` from Elasticsearch

## Live Results (Latest Run)

### Baseline (`inc-live-001`)
- Decision: `execute`
- Model: `openai-gpt-oss-120b`
- Confidence: `8.0`

### Reliability High (`inc-live-001`)
- Decision: `execute`
- `confidence_delta`: `0.0`
- `act`: `0`
- Tool trace includes:
- `search::runbooks_hybrid`
- `agent_builder::converse`
- `search::kibana_sample_data_logs/_search`
- `search::evidence_hybrid`
- `search::evidence_fallback`
- `esql::policy_conflicts`
- `workflow::trigger`
- `action::index_action_execution`

### Reliability Medium (`inc-live-002`)
- Decision: `execute`
- `confidence_delta`: `2.0`
- `act`: `1`
- Tool trace includes same full tool chain as above.

## Slack + Workflow Updates (New)

- Slack channel delivery is active through `WORKFLOW_WEBHOOK_URL`.
- Message format was upgraded to be demo-friendly:
- Human-readable decision labels (`Auto-remediation Approved`, `Human Escalation Required`)
- Structured sections (`What Happened`, `Why This Decision`, `Next Actions`)
- Clean newline rendering (no literal `\n` artifacts)
- Action steps expanded (no clipped one-liners)
- Urgent DM path added for `HIGH`/`CRITICAL` incidents and escalation-like decisions.
- DM routing now supports explicit target via `SLACK_ADMIN_USER_ID`.
- DM path includes fail-closed safety checks:
- Rejects target if it resolves to bot/app user
- Fails if user identity cannot be verified by Slack API
- Reports exact DM diagnostics in run output (`target_user_id`, `bot_user_id`, `dm_channel_id`).

## Action Execution Proof

From live Elasticsearch `action_executions` index (latest docs):
- Records exist with `action_type: execute_action`
- Includes `incident_id`, `decision`, `execution_mode`, `reasons`, `confidence_delta`
- Confirms real externalized action records were created during demo runs.

## Workflow/Event Proof

From live Elasticsearch `workflow_events` index:
- Event docs were indexed for each run
- Includes `incident_id`, `decision`, `execution_mode`, `reasons`, `confidence_delta`

## Elastic Linking Notes

- Slack links now prioritize generally available Kibana apps (`Discover`, index management) rather than optional Observability/APM pages.
- Kibana URL can be overridden with `ELASTIC_KIBANA_URL` to avoid bad host derivation from `ELASTIC_URL`.

## Known Runtime Requirement (Slack DM)

- For strict DM-to-human verification, bot token must include `users:read`.
- Without `users:read`, urgent DM fails with:
- `missing_scope` (`needed: users:read`)
- Current required scopes for this demo path:
- `chat:write`
- `im:write`
- `users:read`

## Compliance Summary

This live demo demonstrates:
- Multi-step agentic workflow (`Plan -> Stress -> Compress -> Gate -> Execute`)
- Agent Builder reasoning (`converse`)
- Elasticsearch `Search` and policy checks (`ES|QL` path)
- Real action execution path with persisted, queryable evidence in Elasticsearch
- Production-style operator notification flow (Slack channel + urgent DM path) with explicit safety and delivery diagnostics
