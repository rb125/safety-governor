# Reliability Layer Incident MVP

A hackathon-ready MVP implementing:

- `Plan`: propose remediation action from retrieved runbooks.
- `Stress` (DDFT): verify claims with supporting and contradicting evidence, include policy counterfactual and fabricated-authority rejection.
- `Compress` (CDCT): choose `full_context` vs `compressed_context` and enforce output discipline.
- `Gate` (AGT): compute `ACT`, `AS`, and block unsafe actions.
- `Execute` (Action): trigger external action endpoints via webhook and persist action events.

## Why this is demo-ready

- Shows baseline behavior vs reliability-layer behavior on the same incident.
- Produces structured run traces, tool traces, and reliability metrics in JSONL.
- Exposes concrete outputs aligned with your research abstractions.

## Project Layout

- `run_demo.py`: offline run using local sample data.
- `run_live.py`: run against real Elasticsearch indices.
- `load_to_elastic.py`: ingest local sample docs into Elasticsearch.
- `src/reliability_layer.py`: core `Plan -> Stress -> Compress -> Gate -> Execute` pipeline.
- `src/elastic_mock.py`: offline in-memory adapter.
- `src/elastic_rest.py`: real Elasticsearch REST adapter.
- `src/workflow_client.py`: action trigger client (Kibana workflow endpoint when available, otherwise webhook).
- `data/sample_data.json`: runbooks, evidence, policies, incidents.
- `scenarios/*.json`: incident payloads for live runs.
- `contracts/*.schema.json`: stage output contracts.
- `mappings/*.mapping.json`: Elasticsearch index mappings.
- `output/*.jsonl`: generated run logs and metrics.
- `output/tool_trace.jsonl`: per-run Search/ES|QL/Agent/Workflow call trace.
- `output/workflow_events.jsonl`: action trigger outcomes (`triggered` / `skipped` / `failed`).
- `tests/test_pipeline_unittest.py`: dependency-free behavioral tests.

## Run (Offline)

```bash
cd agentic/reliability_layer_incident_mvp
python3 run_demo.py
```

## Run With Real Elasticsearch

Set env vars:

```bash
export ELASTIC_URL="https://<your-cluster>:443"
export ELASTIC_API_KEY="<api-key>"
export ELASTIC_RUNBOOKS_INDEX="runbooks-demo"
export ELASTIC_EVIDENCE_INDEX="evidence-demo"
export ELASTIC_POLICIES_INDEX="policies-demo"
export ELASTIC_INCIDENTS_INDEX="incidents-demo"
export ELASTIC_WORKFLOW_ID="<optional-kibana-workflow-id>"
export WORKFLOW_WEBHOOK_URL="<optional-webhook-url>"
export RELIABILITY_PROFILE_MODEL="gpt-oss-120b"
export RELIABILITY_PROFILE_SOURCE="direct_api" # or "agent_builder_mcp"
```

Load documents (replace with your real data later):

```bash
cd agentic/reliability_layer_incident_mvp
python3 load_to_elastic.py
```

Run live pipeline:

```bash
cd agentic/reliability_layer_incident_mvp
python3 run_live.py --incident-file scenarios/incident_high.json
python3 run_live.py --incident-file scenarios/incident_medium.json
```

Run from live logs (dynamic incident generation):

```bash
cd agentic/reliability_layer_incident_mvp
python3 run_live_from_logs.py --window-minutes 30 --index kibana_sample_data_logs --service elastic-downloads
```

Judge-facing demo (clean agentic summary across multiple incidents):

```bash
cd agentic/reliability_layer_incident_mvp
./demo_hackathon.sh
```

Run naive baseline for comparison:

```bash
cd agentic/reliability_layer_incident_mvp
python3 run_live.py --incident-file scenarios/incident_high.json --baseline
```

## Test

```bash
cd agentic/reliability_layer_incident_mvp
python3 -m unittest -q tests/test_pipeline_unittest.py
```

## Demo Script (3 minutes)

1. Run naive baseline on high-severity incident.
2. Show it executes directly with no contradiction checks.
3. Run reliability layer on same incident.
4. Show contradiction evidence, confidence deltas, and gate decision.
5. Show action trigger output (`workflow` field + `output/workflow_events.jsonl`).
6. Show tool trace (`output/tool_trace.jsonl`) proving Search + ES|QL + Agent Builder + Action calls.
7. Show summary from `output/reliability_metrics.jsonl`.

## Hackathon Fit

- Requirement: multi-step AI agent with reasoning model + one or more Agent Builder tools.
- This project uses:
- `Agent Builder Converse` for planning/verification reasoning.
- `Search` for runbooks and evidence retrieval.
- `ES|QL` for policy conflict checks.
- `Action execution` via webhook trigger (real external call), with workflow endpoint support when enabled in deployment.

Notes:
- In some Elastic deployments, direct `/api/workflows/*` CRUD APIs may be disabled by configuration even if Agent Builder is enabled.
- This implementation stays compliant by using Search + ES|QL + reliable external action execution with full audit traces.

## MCP Mode (CDCT/DDFT/EECT as Agent Tools)

To make CDCT/DDFT/EECT visible as Agent Builder tool usage:

1. Configure MCP server from `mcp/reliability_framework_mcp_server.py` in Agent Builder.
2. Attach MCP tools to your agent (`ELASTIC_AGENT_ID`).
3. Set:

```bash
export RELIABILITY_PROFILE_SOURCE="agent_builder_mcp"
```

Behavior:
- Agent Builder is prompted to call `ddft_score`, `cdct_score`, and `eect_score` tools.
- Returned profile is used in gate logic.
- If MCP tool path is unavailable/empty, code falls back to direct API fetch for reliability.
