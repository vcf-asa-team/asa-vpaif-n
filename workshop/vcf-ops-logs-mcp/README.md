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

## Example prompts

Try these against whatever chat client you've connected this MCP server to
(Claude Desktop, Open WebUI, etc.):

**On-demand search (`search_logs`)**
- "Search the VCF Operations for Logs for any mention of 'timeout' in the last 30 minutes."
- "Find log entries containing 'connection refused' in the last 2 hours."

**Advanced filtering (`query_logs`)**
- "Find all log entries from host 10.0.0.5 in the last hour." (exercises a `term` query on a specific field)
- "Show me everything logged in the last 15 minutes." (`match_all` — a good sanity check)

**Field discovery (`list_log_fields`)**
- "What fields can I filter log entries on in this VCF Operations for Logs instance?"

**Exact counts (`count_errors`)**
- "How many ERROR, Exception, CRITICAL, and FATAL log lines have occurred in the last 24 hours?"
- "Count how many times 'disk full' has appeared in the logs today." (pass a custom `error_terms` list)

**Background monitor — the proactive half (`get_recent_findings`)**
- "What has the background monitor already noticed in the logs, without me asking it to search?"
- "Has anything matching ERROR or CRITICAL shown up in the last few minutes?"
- A live demo sequence that shows the push/pull duality working together:
  tell the agent *"Keep this running — I'm going to trigger something,"*
  then actually cause an error somewhere in the environment, wait
  roughly one `MONITOR_INTERVAL_SECONDS` (60s default), and ask *"What did
  you just notice?"* — it should surface the finding via
  `get_recent_findings` without you having told it what to look for.

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
   `/suite-api/api/auth/token/exchange`, on `VCF_OPS_LOGS_SUITE_API_BASE_URL`)
   with `Authorization: Bearer <access_token>` and body `{"serviceKeys":
   ["ops-li"]}`, returning a component **JWT**.

   **Set `VCF_OPS_LOGS_SUITE_API_BASE_URL` explicitly** to your VCF
   Operations *manager* host. It defaults to `VCF_OPS_LOGS_BASE_URL`, but
   that's commonly wrong once you point `VCF_OPS_LOGS_BASE_URL` at a
   dedicated logs appliance — this exchange then `302`s to a login page
   instead of returning a token (surfaced as a clear `VCFOpsLogsAPIError`
   now, not a raw JSON-decode crash — see the `>=300` check in
   `_parse_response`). The response's token comes back under `jwtToken`
   (confirmed from a live lab), which `_extract_token` checks for alongside
   the more generic `token`/`access_token`/`jwt`/`accessToken`/`value`.
4. Only that component JWT is sent on actual log queries, as `X-JWT-Token:
   <jwt>` — this part matches the API docs' security schema exactly. Both
   tokens are cached and re-exchanged once automatically on a `401`.

See `client.py`'s `_ensure_access_token`/`_ensure_ops_li_jwt` for this flow.
Fleet API tokens only inherit permissions from your **VCF Fleet role** (e.g.
"VCF Administrator") — if queries still fail after both exchanges succeed,
check that the token's fleet role actually grants log access.

## How queries are built

VCF 9.1.1's Log Management API (`POST /api/v2/search`, confirmed working
end-to-end against a live deployment) is a single **synchronous** call
using an Elasticsearch-style query DSL — no submit/poll cycle, no SQL. The
public docs at `developer.broadcom.com/xapis/log-management-api` call this
path "deprecated" and instead document `/api/v2/logs/search` as current —
that one 403s here with `"This API endpoint is not available on this
port"` instead of returning results, so don't trust the "deprecated"
label on this deployment. A `query` is one DSL fragment:

```json
{"match_phrase": {"text": "error"}}
```

or nested via `bool`:

```json
{"bool": {"must": [
  {"match_phrase": {"text": "panic"}},
  {"term": {"incomingAddress": "10.0.0.5"}}
]}}
```

Supported leaf types (per the `Query` schema): `bool` (`must`/`should`/
`must_not`/`filter`, each a list of fragments), `match_phrase`, `term`,
`terms`, `range`, `exists` (`{"field": name}`), `prefix`, `regexp`,
`match_all`. Every tool call ANDs your query with a `range` on `timestamp`
for the `since_minutes` window (`client.py`'s `with_time_range`) — don't
add your own timestamp filter on top of it.

**Two field-name/type gotchas confirmed from a real request body captured
in the live log-search UI's own network traffic** (not from the public API
docs, which get both of these wrong):

- Query/filter against `text` (an analyzed field) and `timestamp` — *not*
  `originalText`/`logTimestamp`, which are the names those same values come
  back under in *results*. `client.py` has both: `TEXT_FIELD`/`TIMESTAMP_FIELD`
  for building queries, `RESULT_TEXT_FIELD` for reading a hit's raw text
  (used in `monitor.py`'s sample extraction).
- `range` values (`gte`/`lte`) are raw JSON numbers (epoch milliseconds),
  not strings — the `RangeQueryValue` schema docs claim string-typed, which
  doesn't match what the real UI sends or what this deployment accepts.

`extract_records` flattens each hit's `msgContent` down to a flat field map
(`originalText`, `logTimestamp`, `ingestTimestamp`, `incomingAddress`,
`fields`) for you.

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

## Triage agent system prompt

Pairing this server with `workshop/pais-mcp-web-search` (its `web_search`
and `fetch_page_content` tools) turns a chat client into a basic triage
agent: it finds errors in your logs, then researches what they mean. Give
the agent both MCP servers and a system prompt along these lines:

```
You are a VCF Operations triage and remediation research assistant with
two distinct tool sets. Before calling any tool, classify the user's
current message into exactly one of these two categories — every message,
including follow-ups, not just the first one in a conversation:

CATEGORY A — "what does the log data show" (use vcf-ops-logs-mcp):
search_logs, query_logs, list_log_fields, count_errors, get_recent_findings.
Use these ONLY when the user is asking about current or historical log
data itself: is something still happening, how many times did it occur,
show me the log lines, what else is in the logs, etc.

CATEGORY B — "how do I understand/fix/resolve this" (use pais-mcp-web-search):
web_search, fetch_page_content. Use these whenever the user asks to
explain, fix, resolve, troubleshoot, or find documentation/a workaround for
an error — INCLUDING when that error was already identified earlier in
the conversation. Do NOT call any vcf-ops-logs-mcp tool for these
questions, even though the topic is still "an error from the logs" — the
error text is already known from the conversation; what's missing is
research, not more log data.

Trigger words/phrases that mean Category B, not Category A: "fix", "how do
I", "what does this mean", "resolve", "troubleshoot", "root cause",
"documentation", "KB article", "workaround", or any direct follow-up about
an error you already reported.

Workflow for Category A ("what's wrong" / "investigate errors" questions):
1. Start with get_recent_findings to see what the background monitor has
   already caught; fall back to search_logs/count_errors for anything
   outside what it's configured to watch for.
2. For each distinct error (not every occurrence — group by message
   pattern), extract the specific error text, code, or exception type, and
   report it clearly so it's available for a Category B follow-up without
   re-querying the logs.

Workflow for Category B (research/remediation questions, first-asked or
follow-up):
1. Use web_search with the specific error text/code already known (from
   this turn or earlier in the conversation), biasing toward
   VMware/Broadcom official sources (search terms like "VMware Cloud
   Foundation Operations <error text>" or "Broadcom knowledge base <error
   text>").
2. Use fetch_page_content to read a promising result in full before citing
   it.
3. Summarize as: what the error means, the likely cause, and a suggested
   fix — with a link to the source you used. Say so plainly if you can't
   find a reliable source; don't guess at a fix.

Always keep log data and web results clearly separated in your answer —
never present a web search result as something the logs themselves say.
```

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
use — see "Auth: two token exchanges, not a plain bearer token" above. Set
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
  it's JSON, so there's no string-escaping concern to worry about.
- Everything in this README — both auth exchanges, the `/api/v2/search`
  path, the `X-JWT-Token` header, and the `MessageContent` field names — is
  confirmed end-to-end against a live VCF 9.1.1 deployment, not just the
  public API docs (which, as noted above, get the path wrong).
