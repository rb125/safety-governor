# MCP Tools for CDCT/DDFT/EECT

This folder provides an MCP server so Agent Builder can call framework scores as tools instead of relying only on direct Python API calls.

## Server

- File: `mcp/reliability_framework_mcp_server.py`
- Transport: `stdio` JSON-RPC
- Tools exposed:
- `ddft_score(model)`
- `cdct_score(model)`
- `eect_score(model)`
- `reliability_profile(model)` (merged)

## Local Run (for manual check)

```bash
cd agentic/reliability_layer_incident_mvp
python3 mcp/reliability_framework_mcp_server.py
```

## Configure in Agent Builder (MCP)

Create an MCP tool server in Agent Builder that launches:

```bash
python3 /home/rahul/arXiv/agentic/reliability_layer_incident_mvp/mcp/reliability_framework_mcp_server.py
```

Environment for that MCP server process:

- `CDCT_API_URL=http://localhost:8001`
- `DDFT_API_URL=http://localhost:8002`
- `EECT_API_URL=http://localhost:8003`

Attach the tool server to your Agent Builder agent (`ELASTIC_AGENT_ID`).

## Enable MCP profile mode in this project

Set:

```bash
export RELIABILITY_PROFILE_SOURCE="agent_builder_mcp"
```

In this mode, the reliability layer asks Agent Builder to call MCP tools and return merged profile JSON.  
If MCP tools are unavailable or return empty values, the code falls back to direct API fetch.
