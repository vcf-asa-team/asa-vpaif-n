from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .client import VCFOpsLogsAPIError, VCFOpsLogsClient, time_range_constraints
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


class Constraint(BaseModel):
    field: str = Field(
        description="Log field to filter on, e.g. 'text', 'hostname', 'appname'. "
        "Use list_log_fields to discover what this instance has ingested."
    )
    operator: str = Field(
        description="One of CONTAINS, NOT_CONTAINS, EQ, NOT_EQ, GT, GTE, LT, LTE."
    )
    value: str = Field(description="Value to compare the field against.")


SinceMinutesParam = Annotated[
    float,
    Field(ge=0, le=10080, description="How far back to search, in minutes (default: last hour)."),
]
LimitParam = Annotated[int, Field(ge=1, le=500, description="Maximum number of events to return.")]


async def _run_query(
    ctx: Context[AppContext], constraints: list[tuple[str, str, str]], limit: int
) -> dict[str, Any]:
    try:
        return await ctx.request_context.lifespan_context.logs.query_events(
            constraints, limit=limit
        )
    except VCFOpsLogsAPIError as exc:
        raise ToolError(
            f"VCF Operations for Logs API error ({exc.status_code}): {exc.message}"
        ) from exc


@mcp.tool()
async def search_logs(
    ctx: Context[AppContext],
    query_text: Annotated[
        str, Field(description="Free-text term or phrase to search for in the raw log message.")
    ],
    since_minutes: SinceMinutesParam = 60,
    limit: LimitParam = 50,
) -> dict[str, Any]:
    """Search raw log messages for a text term/phrase within a recent time window."""
    constraints = [("text", "CONTAINS", query_text), *time_range_constraints(since_minutes)]
    return await _run_query(ctx, constraints, limit)


@mcp.tool()
async def query_logs(
    ctx: Context[AppContext],
    constraints: Annotated[
        list[Constraint], Field(description="Field/operator/value triples, ANDed together.")
    ],
    since_minutes: SinceMinutesParam = 60,
    limit: LimitParam = 50,
) -> dict[str, Any]:
    """Run a structured, field-level query (advanced; prefer search_logs for simple text search)."""
    tuples = [(c.field, c.operator, c.value) for c in constraints]
    return await _run_query(ctx, [*tuples, *time_range_constraints(since_minutes)], limit)


@mcp.tool()
async def list_log_fields(ctx: Context[AppContext]) -> dict[str, Any]:
    """List field names known to this VCF Operations for Logs instance, to help build queries."""
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
    limit: LimitParam = 200,
    error_terms: Annotated[
        list[str] | None,
        Field(
            description="Terms to count, e.g. ['ERROR', 'Exception']. "
            "Defaults to the server's configured MONITOR_ERROR_TERMS."
        ),
    ] = None,
) -> dict[str, Any]:
    """Count matching events per error term over a time window.

    Counts are capped at `limit` per term (one query per term, since the
    query API ANDs constraints together rather than ORing them) — treat the
    result as "at least this many", not an exact total for high-volume terms.
    """
    terms = error_terms or ctx.request_context.lifespan_context.monitor.error_terms
    counts: dict[str, int] = {}
    for term in terms:
        constraints = [("text", "CONTAINS", term), *time_range_constraints(since_minutes)]
        result = await _run_query(ctx, constraints, limit)
        counts[term] = len(result.get("events") or [])
    return {"since_minutes": since_minutes, "limit_per_term": limit, "counts": counts}


@mcp.tool()
async def get_recent_findings(
    ctx: Context[AppContext],
    limit: Annotated[
        int, Field(ge=1, le=200, description="Maximum number of findings to return, most recent first.")
    ] = 20,
) -> list[dict[str, Any]]:
    """Return recent findings already surfaced by the background monitor loop (no new query is made)."""
    return ctx.request_context.lifespan_context.monitor.recent_findings(limit)
