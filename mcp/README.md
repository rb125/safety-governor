# MCP Server — Reliability Framework

Exposes 8 tools over Streamable HTTP (MCP 2025-03-26) so Kibana Agent Builder can call them autonomously during plan and stress phases.

## Tools

### SRE Operation Tools
| Tool | Description |
|---|---|
| `search_runbooks(query, service, top_k)` | Hybrid search over `runbooks-demo` filtered by service |
| `search_evidence(query, service, top_k)` | Search `evidence-demo` for supporting/contradicting docs |
| `check_policy_conflicts(service, action, severity)` | Query `policies-demo` for blocked actions |
| `query_live_logs()` | Live error count + avg bytes from `kibana_sample_data_logs` |

### Reliability Framework Tools
| Tool | Description |
|---|---|
| `ddft_score(model)` | DDFT robustness metrics (HOC, CI) from port 8002 |
| `cdct_score(model)` | CDCT context-discipline metric (u_curve_magnitude) from port 8001 |
| `eect_score(model)` | EECT action-gating metrics (AS, ACT, ECS) from port 8003 |
| `reliability_profile(model)` | Merged CDCT + DDFT + EECT profile |

## Running

### HTTP mode (for Kibana Agent Builder on Elastic Cloud)

```bash
export $(grep -v '^#' ../.env | grep -v '^$' | xargs)
../.venv/bin/python3 reliability_framework_mcp_server.py --http --port 8010
```

Endpoint: `http://0.0.0.0:8010/mcp`

Expose via ngrok so Elastic Cloud can reach it:

```bash
ngrok http 8010
# MCP URL: https://xxxx.ngrok-free.app/mcp
```

### stdio mode (for local testing)

```bash
export $(grep -v '^#' ../.env | grep -v '^$' | xargs)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  ../.venv/bin/python3 reliability_framework_mcp_server.py
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ELASTIC_URL` | required | Elasticsearch cluster URL |
| `ELASTIC_API_KEY` | required | Elasticsearch API key |
| `ES_RUNBOOKS_INDEX` | `runbooks-demo` | Runbooks index name |
| `ES_EVIDENCE_INDEX` | `evidence-demo` | Evidence index name |
| `ES_POLICIES_INDEX` | `policies-demo` | Policies index name |
| `CDCT_API_URL` | `http://localhost:8001` | CDCT scoring API |
| `DDFT_API_URL` | `http://localhost:8002` | DDFT scoring API |
| `EECT_API_URL` | `http://localhost:8003` | EECT scoring API |

## Adding to Kibana Agent Builder

1. Start server in HTTP mode and expose via ngrok
2. Kibana → AI Assistant → Agent Builder → your agent → **Tools** tab
3. **New tool** → **MCP** → paste `https://xxxx.ngrok-free.app/mcp`
4. Import all 8 tools
5. Set `ELASTIC_AGENT_ID` in `.env` to match your agent's ID
