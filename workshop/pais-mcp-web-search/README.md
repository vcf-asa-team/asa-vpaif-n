# pais-mcp-web-search

An MCP server (Streamable HTTP transport) that gives an agent web search —
Google and DuckDuckGo — plus page-content fetching and GitHub code/repository
search, so it can gather up-to-date information from the internet (and real
code examples) to answer a user's question or assist with coding. Web search
is similar in spirit to "rival search" style MCP servers: no API keys
required, since it scrapes search result pages directly via the
[`ddgs`](https://pypi.org/project/ddgs/) library rather than calling a paid
search API.

## Tools

| Tool | Description |
| --- | --- |
| `web_search` | Search Google and/or DuckDuckGo for a query; returns title/url/snippet per source. |
| `fetch_page_content` | Fetch a URL (e.g. one returned by `web_search`) and extract its main readable text. |
| `github_search_code` | Search code across public GitHub repos for real-world usage examples — useful for coding/vibe-coding. Requires `GITHUB_TOKEN`. |
| `github_search_repositories` | Search GitHub repositories (sorted by stars) — find relevant libraries, example projects, or starter templates. |

### `web_search` parameters

| Parameter | Description |
| --- | --- |
| `query` | The search query, typically derived from the user's question. |
| `engine` | `"duckduckgo"` (default), `"google"`, or `"both"` (merged, de-duplicated by URL). |
| `num_results` | **How many sources to return.** Defaults to `WEB_SEARCH_DEFAULT_NUM_RESULTS`; capped at `WEB_SEARCH_MAX_NUM_RESULTS`. |

### `fetch_page_content` parameters

| Parameter | Description |
| --- | --- |
| `url` | URL of the page to fetch. |
| `max_chars` | Maximum characters of extracted text to return. Defaults to `WEB_SEARCH_DEFAULT_FETCH_MAX_CHARS`; capped at `WEB_SEARCH_MAX_FETCH_CHARS`. |

### `github_search_code` parameters

| Parameter | Description |
| --- | --- |
| `query` | Code search query, e.g. a function/class name, error message, import statement, or API usage pattern. |
| `language` | Optional: restrict to a programming language, e.g. `"python"`. |
| `repo` | Optional: restrict to one repository, `"owner/name"` form. |
| `num_results` | **How many sources to return.** Same defaults/cap as `web_search`. |

Each result includes the repo, file path, a matched code snippet, and a link
to the file on GitHub. Requires `GITHUB_TOKEN` to be set — GitHub's code
search API does not allow unauthenticated requests.

### `github_search_repositories` parameters

| Parameter | Description |
| --- | --- |
| `query` | Repository search query, e.g. a library name, framework, or project topic. |
| `language` | Optional: restrict to a programming language. |
| `num_results` | **How many sources to return.** Same defaults/cap as `web_search`. |

Works without `GITHUB_TOKEN`, but GitHub's unauthenticated search rate limit
is low (10 requests/min); set the token for heavier use.

## Setup

Requires Python 3.10+.

```bash
cd workshop/pais-mcp-web-search
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env if you want to change defaults; set GITHUB_TOKEN to enable
# github_search_code (the web/github_search_repositories tools need no keys)
```

## Running

Load `.env` into the environment, then run either:

```bash
# via the installed console script
export $(grep -v '^#' .env | xargs)
pais-mcp-web-search
```

or, for local development with the MCP Inspector:

```bash
uv run mcp dev src/pais_mcp_web_search/server.py
```

By default the server listens on `0.0.0.0:8000` (override with `MCP_HOST` /
`MCP_PORT`) and serves the Streamable HTTP endpoint at `/mcp`, i.e.
`http://localhost:8000/mcp`.

## Running in Docker

```bash
cd workshop/pais-mcp-web-search
docker build -t pais-mcp-web-search .
docker run --rm -p 8000:8000 --env-file .env pais-mcp-web-search
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
    "web-search": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Logging

Set `WEB_SEARCH_LOG_LEVEL=DEBUG` (default `INFO`) to see what's actually being
scraped: every raw title/URL returned by each engine (including ones later
deduplicated across `engine="both"`), HTTP status/content-type/byte count for
each `fetch_page_content` request, and a text preview of the extracted
content. The same applies to `github_search_code` / `github_search_repositories`
(repo/path/url per result at `INFO`). At the default `INFO` level you still
get one line per tool call and one line per result returned, just without the
raw-scrape/preview detail. Logs go to stderr via the standard `logging` module.

## Notes

- This server scrapes public search result pages rather than calling an
  official search API. That means no API key or billing setup, but also no
  SLA: result pages can change layout, rate-limit, or block a given IP/User-Agent
  at any time. For heavier or production use, consider swapping `ddgs`'s
  Google backend for the official Google Programmable Search / Custom Search
  JSON API.
- Respect the target sites' terms of service and robots directives for your
  use case; this tool is intended for interactive, human-in-the-loop research,
  not bulk crawling.
- The server itself does not enforce authentication on incoming MCP
  connections — if you expose it beyond localhost, put it behind a reverse
  proxy / VPN / auth layer you control.
- **"421 Misdirected Request" / "Invalid Host header" in the logs**: the
  Streamable HTTP transport's DNS-rebinding protection rejects any request
  whose `Host` header isn't localhost. This server ships with that
  protection disabled (`MCP_ENABLE_DNS_REBINDING_PROTECTION=false`) since
  it's normally reached over a private network, not a browser. To re-enable
  it, set `MCP_ENABLE_DNS_REBINDING_PROTECTION=true` and list every
  host:port / origin clients connect through in `MCP_ALLOWED_HOSTS` /
  `MCP_ALLOWED_ORIGINS` (see `.env.example`).
