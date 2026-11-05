from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .client import (
    TEXT_FIELD,
    VCFOpsLogsAPIError,
    VCFOpsLogsClient,
    extract_records,
    match_phrase_query,
    time_range_ms,
    total_matched,
    with_time_range,
)
from .config import MonitorConfig, VCFOpsLogsConfig
from .monitor import Monitor


@dataclass
class AppContext:
    logs: VCFOpsLogsClient
    monitor: Monitor


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    client = VCFOpsLogsClient(VCFOpsLogsConfig.from_env())
    monitor = Monitor(client, MonitorConfig.from_env())
    monitor.start()
    try:
        yield AppContext(logs=client, monitor=monitor)
    finally:
        await monitor.stop()
        await client.aclose()


mcp = MCPServer("vcf-ops-logs-mcp", lifespan=app_lifespan)


SinceMinutesParam = Annotated[
    float,
    Field(ge=0, le=10080, description="How far back to search, in minutes (default: last hour)."),
]
SizeParam = Annotated[
    int, Field(ge=1, le=2000, description="Maximum number of log records to return.")
]


async def _run_search(
    ctx: Context[AppContext], query: dict[str, Any], since_minutes: float, size: int
) -> dict[str, Any]:
    start_ms, end_ms = time_range_ms(since_minutes)
    bounded_query = with_time_range(query, start_ms, end_ms)
    try:
        result = await ctx.request_context.lifespan_context.logs.search(bounded_query, size=size)
    except VCFOpsLogsAPIError as exc:
        raise ToolError(
            f"VCF Operations for Logs API error ({exc.status_code}): {exc.message}"
        ) from exc
    return {
        "total_matched": total_matched(result),
        "records": extract_records(result),
        "timed_out": result.get("timedOut", False),
    }


@mcp.tool()
async def search_logs(
    ctx: Context[AppContext],
    query_text: Annotated[
        str, Field(description="Exact phrase to search for in the raw log message.")
    ],
    since_minutes: SinceMinutesParam = 60,
    size: SizeParam = 50,
) -> dict[str, Any]:
    """Search raw log messages for a phrase within a recent time window."""
    return await _run_search(ctx, match_phrase_query(TEXT_FIELD, query_text), since_minutes, size)


@mcp.tool()
async def query_logs(
    ctx: Context[AppContext],
    query: Annotated[
        dict[str, Any],
        Field(
            description="One Elasticsearch-style query DSL fragment, e.g. "
            '{"term": {"incomingAddress": "10.0.0.5"}} or '
            '{"bool": {"must": [{"match_phrase": {"text": "panic"}}]}}. '
            "Supported leaf types: bool (must/should/must_not/filter, each a list of "
            "these fragments), match_phrase, term, terms, range (gte/gt/lte/lt), "
            "exists ({\"field\": name}), prefix, regexp, match_all. "
            "Use list_log_fields to discover field names. Automatically ANDed with "
            "the since_minutes time range — do not add your own timestamp range."
        ),
    ],
    since_minutes: SinceMinutesParam = 60,
    size: SizeParam = 50,
) -> dict[str, Any]:
    """Run a raw query-DSL log search (advanced; prefer search_logs for a simple phrase)."""
    return await _run_search(ctx, query, since_minutes, size)


@mcp.tool()
async def list_log_fields(ctx: Context[AppContext]) -> dict[str, Any]:
    """List extracted fields known to this VCF Operations for Logs instance."""
    try:
        return await ctx.request_context.lifespan_context.logs.list_fields()
    except VCFOpsLogsAPIError as exc:
        raise ToolError(
            f"VCF Operations for Logs API error ({exc.status_code}): {exc.message}"
        ) from exc


@mcp.tool()
async def count_errors(
    ctx: Context[AppContext],
    since_minutes: SinceMinutesParam = 60,
    error_terms: Annotated[
        list[str] | None,
        Field(
            description="Terms to count, e.g. ['ERROR', 'Exception']. "
            "Defaults to the server's configured MONITOR_ERROR_TERMS."
        ),
    ] = None,
) -> dict[str, Any]:
    """Count exact matching-event totals per error term over a time window."""
    terms = error_terms or ctx.request_context.lifespan_context.monitor.error_terms
    counts: dict[str, int] = {}
    for term in terms:
        result = await _run_search(ctx, match_phrase_query(TEXT_FIELD, term), since_minutes, size=1)
        counts[term] = result["total_matched"]
    return {"since_minutes": since_minutes, "counts": counts}


@mcp.tool()
async def get_recent_findings(
    ctx: Context[AppContext],
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of findings to return, most recent first.")
    ] = 20,
) -> list[dict[str, Any]]:
    """Return recent findings already surfaced by the background monitor loop (no new query is made)."""
    return ctx.request_context.lifespan_context.monitor.recent_findings(limit)
