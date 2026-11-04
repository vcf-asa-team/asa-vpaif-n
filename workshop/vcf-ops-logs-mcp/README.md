# vcf-ops-logs-mcp

An MCP server (Streamable HTTP transport) that lets an agent query
**VMware Cloud Foundation (VCF) Operations for Logs** — formerly
Aria Operations for Logs / vRealize Log Insight — and runs a background loop
that proactively watches for error patterns between queries.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_logs` | Free-text search over raw log messages within a time window. |
| `query_logs` | Structured field/operator/value query (advanced; multiple constraints are ANDed). |
| `list_log_fields` | List field names ingested by this instance, to help build queries. |
| `count_errors` | Count matches per error term over a time window (capped at `limit` per term). |
| `get_recent_findings` | Read findings already captured by the background monitor — no new query. |

## Auth: token exchange, not a plain bearer token

VCF Operations 9.1 unified several products (Operations Manager, Operations
for Logs, Operations for Networks) behind one API gateway, and log queries
don't accept a plain VCF Operations API token directly. Instead:

1. `VCF_OPS_LOGS_API_TOKEN` is POSTed to `VCF_OPS_LOGS_AUTH_EXCHANGE_PATH`
   (default `/suite-api/api/auth/token/exchange`) as
   `Authorization: <VCF_OPS_LOGS_AUTH_HEADER_SCHEME> <token>` with body
   `{"serviceKeys": ["<VCF_OPS_LOGS_SERVICE_KEY>"]}` (default service key
   `ops-li`).
2. The JWT in that response is cached and sent as `Authorization: Bearer
   <jwt>` on every actual log query, and transparently re-exchanged if a
   query comes back `401` (expired JWT).

`VCF_OPS_LOGS_AUTH_HEADER_SCHEME` defaults to `OpsToken` (the classic
vROps/VCF Operations API-token scheme) — flip it to `Bearer` in `.env` if the
*exchange* call itself 401s. See `client.py`'s `_ensure_jwt` for this flow.

## How queries are built

Queries use the Log Insight REST API "constraint path" convention:
`GET /api/v2/events/<field>/<op>/<value>/<field>/<op>/<value>/...`, where
each `field/operator/value` triple is **ANDed** together server-side (there
is no OR across constraints). Supported operators: `CONTAINS`,
`NOT_CONTAINS`, `EQ`, `NOT_EQ`, `GT`, `GTE`, `LT`, `LTE`. Timestamps are
epoch milliseconds. The path defaults to `/api/v2/events`
(`VCF_OPS_LOGS_EVENTS_PATH`) — the classic standalone Log Insight API used a
`/api/v1/...` prefix, but VCF Operations 9.1's unified gateway uses `v2`.

`list_log_fields` hits `VCF_OPS_LOGS_FIELDS_PATH` (default `/api/v2/fields`),
which is an **unconfirmed guess by analogy** with the events path, not
something I found documented directly — if it 404s, that's the one to fix
via env var once you find the real path in your Swagger UI.

If your instance exposes a still-different query shape (VCF Operations also
exposes a separate async query API under `/vrlic/api/v1/query/{post,get,delete}`
that submits a query and polls for results by ID, rather than the
synchronous `GET /api/v2/events` used here), the place to adapt is
`client.py` (`query_events` / `_constraint_path`). Run `list_log_fields` and
a simple `search_logs` call first to sanity-check compatibility before
relying on this for a demo.

## Background monitor loop

On startup, the server launches an asyncio background task (see
`monitor.py`) that polls for each term in `MONITOR_ERROR_TERMS` (default
`ERROR,Exception,CRITICAL,FATAL`) every `MONITOR_INTERVAL_SECONDS` (default
60s). Each poll only covers the window since the previous poll ended, so
nothing is double-counted. Matches are:

- printed as a one-line summary to stdout (visible via `docker logs`), and
- kept in an in-memory ring buffer (`MONITOR_MAX_FINDINGS` entries), readable
  on demand via the `get_recent_findings` tool.

This gives you both halves of the demo: an agent that can be asked "what
happened in the last hour?" (pull, via `search_logs`/`count_errors`), and a
server that is already watching and can be asked "what have you noticed?"
(push-then-pull, via `get_recent_findings`). Set `MONITOR_ENABLED=false` to
disable the loop and run purely on-demand.

The buffer is in-memory only — it resets on restart and isn't shared across
replicas. That's fine for a single-instance workshop demo; don't rely on it
as a durable alert store.

## Setup

Requires Python 3.10+.

```bash
cd workshop/vcf-ops-logs-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env: set VCF_OPS_LOGS_BASE_URL and VCF_OPS_LOGS_API_TOKEN
```

`VCF_OPS_LOGS_API_TOKEN` is exchanged for a short-lived JWT before use —
see "Auth: token exchange, not a plain bearer token" above. Set
`VCF_OPS_LOGS_VERIFY_SSL=false` if the instance uses a self-signed lab
certificate.

## Running

Load `.env` into the environment, then run either:

```bash
# via the installed console script
export $(grep -v '^#' .env | xargs)
vcf-ops-logs-mcp
```

or, for local development with the MCP Inspector:

```bash
uv run mcp dev src/vcf_ops_logs_mcp/server.py
```

By default the server listens on `0.0.0.0:8000` (override with `MCP_HOST` /
`MCP_PORT`) and serves the Streamable HTTP endpoint at `/mcp`, i.e.
`http://localhost:8000/mcp`.

## Running in Docker

```bash
cd workshop/vcf-ops-logs-mcp
docker build -t vcf-ops-logs-mcp .
docker run --rm -p 8000:8000 --env-file .env vcf-ops-logs-mcp
```

The image runs as a non-root user and serves Streamable HTTP on
`http://localhost:8000/mcp` (override the container's internal port with
`-e MCP_PORT=...` plus a matching `-p` mapping). The monitor loop starts
automatically with the server; watch `docker logs -f` to see it working.

## Connecting a client

Point any MCP client that supports Streamable HTTP at the server URL, for
example a Claude Code / Claude Desktop MCP config entry:

```json
{
  "mcpServers": {
    "vcf-ops-logs": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

To wire this into Open WebUI alongside the other workshop demos, add it as
an MCP tool server the same way `pais-mcp` is connected (see
`workshop/open-webui/README.md`).

## Notes

- The server itself does not enforce authentication on incoming MCP
  connections — if you expose it beyond localhost, put it behind a reverse
  proxy / VPN / auth layer you control.
- **"421 Misdirected Request" / "Invalid Host header" in the logs**: see
  `workshop/pais-mcp/README.md`'s Notes section — same transport, same
  DNS-rebinding protection, same fix (`MCP_ENABLE_DNS_REBINDING_PROTECTION`,
  `MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`).
- `count_errors` reports counts capped at `limit` per term, not a true
  server-side aggregate — treat large numbers as "at least this many."
