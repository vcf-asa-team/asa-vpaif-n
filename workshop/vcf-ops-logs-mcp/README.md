# vcf-ops-logs-mcp

An MCP server (Streamable HTTP transport) that lets an agent query
**VMware Cloud Foundation (VCF) Operations for Logs** — formerly
Aria Operations for Logs / vRealize Log Insight — and runs a background loop
that proactively watches for error patterns between queries.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_logs` | Phrase search over raw log messages within a time window. |
| `query_logs` | Raw Elasticsearch-style query-DSL search (advanced; full control over filtering). |
| `list_log_fields` | List extracted field names ingested by this instance, to help build queries. |
| `count_errors` | Exact matching-event totals per error term over a time window. |
| `get_recent_findings` | Read findings already captured by the background monitor — no new query. |

## Auth: two token exchanges, not a plain bearer token

VCF 9.1's fleet-wide API tokens (generated on VCF Operations' "Your API
Tokens" page) aren't sent directly on log queries, and getting past one
exchange isn't enough — the Log Management API is gated by a *second,
component-scoped* JWT on top of that. Both are needed:

1. Generate a token on that page and copy its **Token Endpoint URL** —
   this points at your SSO/identity-broker host
   (`https://<sso-fqdn>/acs/t/CUSTOMER/token`), which is a **different host**
   from the Operations-for-Logs appliance (`VCF_OPS_LOGS_BASE_URL`). Put it
   in `VCF_OPS_LOGS_TOKEN_ENDPOINT_URL`.
2. The client exchanges `VCF_OPS_LOGS_API_TOKEN` for a short-lived fleet-wide
   **access token** via a `POST` to that endpoint, form-urlencoded:
   `grant_type=urn:custom:vcf:params:oauth:grant-type:api-token&api_token=<token>`.
   This authenticates you to VCF Operations generally — but calling the Log
   Management API with only this token 403s with *"This API endpoint is not
   available on this port"*.
3. That access token is used to run a **second exchange**, scoped to the
   `ops-li` (Operations for Logs) component: `POST
   VCF_OPS_LOGS_SUITE_API_EXCHANGE_PATH` (default
   `/suite-api/api/auth/token/exchange`, on `VCF_OPS_LOGS_SUITE_API_BASE_URL`
   — defaults to `VCF_OPS_LOGS_BASE_URL`) with `Authorization: Bearer
   <access_token>` and body `{"serviceKeys": ["ops-li"]}`, returning a
   component **JWT**.
4. Both are sent on every actual log query — `Authorization: Bearer
   <access_token>` *and* `X-JWT-Token: <jwt>` — cached, and both
   transparently re-exchanged once if a query comes back `401`.

See `client.py`'s `_ensure_tokens`/`_ensure_access_token`/`_ensure_ops_li_jwt`
for this flow. Fleet API tokens only inherit permissions from your **VCF
Fleet role** (e.g. "VCF Administrator") — if queries still fail after both
exchanges succeed, check that the token's fleet role actually grants log
access.

## How queries are built

VCF 9.1.1's Log Management API (`POST /api/v2/logs/search`, confirmed
against `developer.broadcom.com/xapis/log-management-api`) is a single
**synchronous** call using an Elasticsearch-style query DSL — no submit/poll
cycle, no SQL. A `query` is one DSL fragment:

```json
{"match_phrase": {"originalText": "error"}}
```

or nested via `bool`:

```json
{"bool": {"must": [
  {"match_phrase": {"originalText": "panic"}},
  {"term": {"incomingAddress": "10.0.0.5"}}
]}}
```

Supported leaf types (per the `Query` schema): `bool` (`must`/`should`/
`must_not`/`filter`, each a list of fragments), `match_phrase`, `term`,
`terms`, `range` (`gte`/`gt`/`lte`/`lt` — typed as **strings**, not numbers),
`exists` (`{"field": name}`), `prefix`, `regexp`, `match_all`. Every tool
call ANDs your query with a `range` on `logTimestamp` for the `since_minutes`
window (`client.py`'s `with_time_range`) — don't add your own timestamp
filter on top of it.

The two field names used here are confirmed from the API's `MessageContent`
schema: `originalText` holds the raw log line, `logTimestamp` is the log's
own epoch-millisecond timestamp (as opposed to `ingestTimestamp`, when it
was received). `extract_records` flattens each hit's `msgContent` down to
that flat field map for you.

`total_matched` reads `events.total` from the response — the **true** total
match count, independent of `size` — so `count_errors` reports exact
numbers by searching with `size=1` and reading that field, not an estimate
capped by a page size. `query_logs` takes a raw DSL fragment for anything
`search_logs` can't express; run `list_log_fields` first to see what
extracted fields this instance actually has.

If this instance exposes yet another query shape, the place to adapt is
`client.py` (`search`, and `VCF_OPS_LOGS_SEARCH_PATH`/`FIELDS_PATH` in
`.env.example`). Run `list_log_fields` and a simple `search_logs` call first
to sanity-check compatibility before relying on this for a demo.

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
# edit .env: set VCF_OPS_LOGS_BASE_URL, VCF_OPS_LOGS_API_TOKEN, and
# VCF_OPS_LOGS_TOKEN_ENDPOINT_URL
```

`VCF_OPS_LOGS_API_TOKEN` is exchanged for a short-lived access token before
use — see "Auth: OAuth2 token exchange, not a plain bearer token" above. Set
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
- `query_logs` passes your DSL fragment straight through to the search API —
  it's JSON, not string-interpolated SQL, so there's no escaping concern the
  way the earlier SQL-based design had.
- The auth flow (`VCF_OPS_LOGS_TOKEN_ENDPOINT_URL` + OAuth2 token exchange)
  is unrelated to this API change and was confirmed working against a live
  VCF 9.1.1 lab — see `client.py`'s `_ensure_access_token`.
