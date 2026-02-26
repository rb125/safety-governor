# Safety Governor — Reliability Layer Agent

An autonomous SRE agent that intercepts AI-generated remediation plans, stress-tests them against live evidence, and enforces a deterministic safety gate before any production action is taken. Built on Elastic Agent Builder with MCP tool calling.

---

## Problem Statement

Naive AI agents in SRE contexts are dangerous. Given an incident, a vanilla LLM will:
- Propose an action based on training data, not live evidence
- Execute immediately with no contradiction checks
- Have no mechanism to detect policy violations or fabricated authority
- Leave no auditable trace of its reasoning

This project wraps every AI-generated plan in a **Plan → Stress → Gate → Execute** pipeline before anything touches production. The agent is genuinely agentic — it retrieves its own evidence via MCP tools — but the final safety gate is deterministically enforced by Python, independent of the LLM.

---

## High-Level Architecture

```
Elasticsearch Cluster (Elastic Cloud)
├── runbooks-demo        ← remediation procedures
├── evidence-demo        ← supporting / contradicting docs
├── policies-demo        ← blocked actions per service/severity
├── kibana_sample_data_logs  ← live error telemetry
├── incidents-demo       ← incident records
├── workflow_events      ← gate decision audit trail
└── action_executions    ← execution records

                    ┌─────────────────────────────┐
                    │   demo_rich_agentic.py       │
                    │   Rich TUI — 4 worker threads│
                    │   logs / audit / agent /slack│
                    └────────────┬────────────────┘
                                 │
                    ┌────────────▼────────────────┐
                    │   ReliabilityLayerAgent      │
                    │                              │
                    │  plan()  ──→ Agent Builder   │
                    │  stress() ─→ Agent Builder   │
                    │  compress() ← Python only    │
                    │  gate()  ──← Python only     │
                    └────────────┬────────────────┘
                                 │ POST /api/agent_builder/converse
                    ┌────────────▼────────────────┐
                    │   Kibana Agent Builder       │
                    │   (Elastic Cloud)            │
                    │                              │
                    │   calls MCP tools ──────────────→ MCP Server (hosted)
                    │                              │    ├── search_runbooks
                    └─────────────────────────────┘    ├── search_evidence
                                                        ├── check_policy_conflicts
                                                        ├── query_live_logs
                                                        ├── ddft_score
                                                        ├── cdct_score
                                                        ├── eect_score
                                                        └── reliability_profile

                    ┌─────────────────────────────┐
                    │   External Integrations      │
                    │   Jira   — ticket lifecycle  │
                    │   Slack  — approval workflow │
                    └─────────────────────────────┘
```

---

## Agent Builder Workflow

### Phase 1 — Plan

Python sends a **goal-oriented prompt** to Agent Builder:

> "Use the `search_runbooks` tool to find relevant runbooks for service `payment-service` with problem `checkout 5xx spike`. Then propose a remediation plan."

Agent Builder autonomously calls `search_runbooks` via MCP → gets matching runbooks from Elasticsearch → reasons over them → returns structured JSON with `proposed_action`, `rationale`, `key_claims`, `confidence_initial`.

### Phase 2 — Stress

Python sends a second goal-oriented prompt:

> "For each claim, use `search_evidence` to find supporting and contradicting docs. Use `check_policy_conflicts` to check the proposed action. Use `query_live_logs` for live error telemetry."

Agent Builder autonomously calls all four SRE tools in whatever order it decides, synthesises the results, and returns `claim_results`, `policy_conflicts`, `fabricated_authority_rejected`, `confidence_post_stress`, `position_after_stress`.

### Phase 3 — Gate (deterministic Python — never delegated to LLM)

Python reads `PlanOutput` + `StressOutput` and enforces hard rules:

| Condition | Decision |
|---|---|
| Any policy conflict | `block_and_escalate` |
| `contradiction_count >= 2` | `block_and_escalate` |
| `evidence_coverage < 0.34` and zero support docs | `block_and_escalate` |
| Critical severity | `block_and_escalate`, requires `FORCE_OVERRIDE` |
| All checks pass | `execute` |

The gate is deliberately not delegated to Agent Builder. The LLM already influenced the outcome through plan and stress outputs. The gate is an independent check on those outputs — if it also ran in the same LLM context, you would lose the audit guarantee.

CDCT, DDFT, and EECT framework scores flow into the gate indirectly:
- **EECT ECS score** → adjusts `confidence_initial` in plan phase
- **DDFT CI score** → scales contradiction penalty in stress phase
- **CDCT u-curve magnitude** → determines `full_context` vs `compressed_context` in compress phase

### Phase 4 — Slack Approval Loop

For blocked incidents, Python posts a structured Slack message with evidence summary, confidence shift, Jira link, and Elastic links. Worker polls for `APPROVE` or `FORCE_OVERRIDE` in the thread. Critical incidents refuse `APPROVE` and require `FORCE_OVERRIDE`, which is permanently logged.

### Phase 5 — Learn

After successful execution, Agent Builder is prompted to summarise the resolution into a runbook entry, which is indexed back into `runbooks-demo`. Closed-loop knowledge base.

---

## MCP Tool Flow

```
Python → POST /api/agent_builder/converse (goal prompt)
              │
         Kibana Agent Builder
              │
              ├── tool call: search_runbooks(query, service)
              │        → hosted MCP server → ES runbooks-demo/_search
              │
              ├── tool call: search_evidence(query, service)
              │        → hosted MCP server → ES evidence-demo/_search
              │
              ├── tool call: check_policy_conflicts(service, action, severity)
              │        → hosted MCP server → ES policies-demo/_search
              │
              ├── tool call: query_live_logs()
              │        → hosted MCP server → ES kibana_sample_data_logs/_search
              │
              └── synthesise → return JSON
```

The MCP server also exposes `ddft_score`, `cdct_score`, `eect_score`, `reliability_profile` — these call the CDCT/DDFT/EECT APIs hosted on Vercel.

The MCP server lives in a separate repository: [reliability-framework-mcp](https://github.com/rahulbaxi/reliability-framework-mcp).

---

## Project Structure

```
safety-governor/
├── demo_rich_agentic.py          # Rich TUI demo — main entry point
├── load_to_elastic.py            # Seed ES indices from sample_data.json
├── src/
│   ├── reliability_layer.py      # Plan → Stress → Compress → Gate → Execute pipeline
│   ├── elastic_rest.py           # Elasticsearch REST adapter
│   ├── elastic_agent_client.py   # Kibana Agent Builder converse API client
│   ├── workflow_client.py        # Slack messaging + approval polling
│   ├── jira_client.py            # Jira ticket lifecycle
│   ├── models.py                 # PlanOutput, StressOutput, GateOutput, etc.
│   └── api_client.py             # CDCT/DDFT/EECT direct API client
├── mcp/
│   ├── reliability_framework_mcp_server.py  # MCP server (8 tools, HTTP + stdio)
│   └── README.md
├── contracts/                    # JSON schemas for pipeline outputs
├── data/
│   └── sample_data.json          # Runbooks, evidence, policies for ES seeding
├── scenarios/                    # Incident payloads for scripted runs
├── mappings/                     # Elasticsearch index mappings
└── output/                       # JSONL audit trail (gitignored, directory preserved)
```

---

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your Elastic Cloud, Slack, and Jira credentials. See `.env.example` for all available variables.

### 3. Load sample data into Elasticsearch

```bash
export $(grep -v '^#' .env | grep -v '^$' | xargs)
.venv/bin/python3 load_to_elastic.py
```

### 4. Configure MCP tools in Kibana Agent Builder

The MCP server is hosted — no local setup required.

1. Kibana → AI Assistant → Agent Builder → open your agent → **Tools** tab
2. **New tool** → **MCP** → paste the hosted MCP endpoint URL
3. Add `Authorization: Bearer <token>` as a custom header
4. Import all 8 tools

See [reliability-framework-mcp](https://github.com/rahulbaxi/reliability-framework-mcp) for MCP server details.

### 5. Run the demo

**Fast mode** (default — no LLM calls, pre-computed responses):

```bash
export $(grep -v '^#' .env | grep -v '^$' | xargs)
.venv/bin/python3 demo_rich_agentic.py
```

**Presenter mode** (deliberate pauses at key moments for live demos):

```bash
.venv/bin/python3 demo_rich_agentic.py --present
```

**Live mode** (real Agent Builder + MCP tool calling):

```bash
DEMO_FAST_MODE=false .venv/bin/python3 demo_rich_agentic.py
```

---

## Slack Approval Workflow

When the safety gate blocks an incident:

1. Bot posts a structured message to `SLACK_CHANNEL_LABEL` with evidence summary, confidence shift, and Jira + Elastic links
2. Reply **in the thread** with `APPROVE` to proceed with remediation
3. Reply with `FORCE_OVERRIDE` to bypass the safety gate (logged permanently)

**Critical incidents** (severity: critical): `APPROVE` is refused by the Safety Governor. Only `FORCE_OVERRIDE` proceeds. This refusal and the override are both written to the audit trail.

---

## Audit Trail

Every run appends to `output/`:

| File | Contents |
|---|---|
| `tool_trace.jsonl` | Every MCP/search/agent call with timestamps |
| `agent_runs.jsonl` | Full pipeline record per incident |
| `reliability_metrics.jsonl` | Gate decision, framework scores, confidence delta |
| `workflow_events.jsonl` | Slack/webhook trigger outcomes |

---

## Why the Gate Stays in Python

The gate is the trust boundary between the agentic system and production. It is deliberately not delegated to Agent Builder:

- **Auditability**: every block decision maps to an exact threshold comparison in code, not LLM reasoning
- **Independence**: the LLM already influenced the outcome through plan and stress outputs; the gate is an independent check on those outputs
- **Consistency**: same inputs always produce the same decision regardless of model temperature or version
- **Tamper resistance**: a crafted incident prompt cannot influence the gate's threshold enforcement
