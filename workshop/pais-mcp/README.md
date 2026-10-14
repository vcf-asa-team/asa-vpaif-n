# pais-mcp

An MCP server (Streamable HTTP transport) that exposes read/delete access to
[Private AI Services](https://www.vmware.com/) (PAIS) by VMware/Broadcom —
agents, knowledge bases, and data sources.

## Tools

| Tool | PAIS endpoint |
| --- | --- |
| `list_agents` | `GET /api/v1/compatibility/openai/v1/agents` |
| `delete_agent` | `DELETE /api/v1/compatibility/openai/v1/agents/{agent_id}` |
| `list_knowledge_bases` | `GET /api/v1/control/knowledge-bases` |
| `delete_knowledge_base` | `DELETE /api/v1/control/knowledge-bases/{knowledge_base_id}` |
| `list_data_sources` | `GET /api/v1/control/data-sources` |
| `delete_data_source` | `DELETE /api/v1/control/data-sources/{data_source_id}` |

`list_*` tools accept optional `limit` and `after` (cursor) parameters and
return the raw PAIS list envelope (`object`, `has_more`, `num_objects`,
`data`, `first_id`, `last_id`).

## Adding another PAIS endpoint

The client (`client.py`) only knows generic `list_resource(path)` and
`delete_resource(path)` verbs. To add a new resource type:

1. Add a `ResourceSpec` to `resources.py` with its list/delete paths.
2. Add a `list_<x>` / `delete_<x>` tool pair to `server.py` that calls the
   shared `_list` / `_delete` helpers with your new spec.

No other files need to change.

## Setup

Requires Python 3.10+.

```bash
cd workshop/pais-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env: set PAIS_BASE_URL and PAIS_API_TOKEN
```

`PAIS_API_TOKEN` is sent as `Authorization: Bearer <token>` on every request
to PAIS. Set `PAIS_VERIFY_SSL=false` if the instance uses a self-signed lab
certificate.

## Running

Load `.env` into the environment, then run either:

```bash
# via the installed console script
export $(grep -v '^#' .env | xargs)
pais-mcp
```

or, for local development with the MCP Inspector:

```bash
uv run mcp dev src/pais_mcp/server.py
```

By default the server listens on `0.0.0.0:8000` (override with `MCP_HOST` /
`MCP_PORT`) and serves the Streamable HTTP endpoint at `/mcp`, i.e.
`http://localhost:8000/mcp`.

## Running in Docker

```bash
cd workshop/pais-mcp
docker build -t pais-mcp .
docker run --rm -p 8000:8000 --env-file .env pais-mcp
```

The image runs as a non-root user and serves Streamable HTTP on
`http://localhost:8000/mcp` (override the container's internal port with
`-e MCP_PORT=...` plus a matching `-p` mapping).

## Connecting a client

Point any MCP client that supports Streamable HTTP at the server URL, for
example a Claude Code / Claude Desktop MCP config entry:

```json
{
  "mcpServers": {
    "pais": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Notes

- The server itself does not enforce authentication on incoming MCP
  connections — if you expose it beyond localhost, put it behind a reverse
  proxy / VPN / auth layer you control.
- Delete operations are irreversible; there is no confirmation step in the
  tool itself, so any client wired to these tools should confirm with the
  user before calling `delete_*`.
- **"421 Misdirected Request" / "Invalid Host header" in the logs**: the
  Streamable HTTP transport's DNS-rebinding protection rejects any request
  whose `Host` header isn't localhost. This server ships with that
  protection disabled (`MCP_ENABLE_DNS_REBINDING_PROTECTION=false`) since
  it's normally reached over a private network, not a browser. To re-enable
  it, set `MCP_ENABLE_DNS_REBINDING_PROTECTION=true` and list every
  host:port / origin clients connect through in `MCP_ALLOWED_HOSTS` /
  `MCP_ALLOWED_ORIGINS` (see `.env.example`).
