from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .client import PAISAPIError, PAISClient
from .config import PAISConfig
from .resources import AGENTS, DATA_SOURCES, KNOWLEDGE_BASES, ResourceSpec


@dataclass
class AppContext:
    pais: PAISClient


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    client = PAISClient(PAISConfig.from_env())
    try:
        yield AppContext(pais=client)
    finally:
        await client.aclose()


mcp = MCPServer("pais-mcp", lifespan=app_lifespan)


async def _list(
    ctx: Context[AppContext], spec: ResourceSpec, limit: int | None, after: str | None
) -> dict[str, Any]:
    try:
        return await ctx.request_context.lifespan_context.pais.list_resource(
            spec.list_path, limit=limit, after=after
        )
    except PAISAPIError as exc:
        raise ToolError(
            f"PAIS API error listing {spec.key}s ({exc.status_code}): {exc.message}"
        ) from exc


async def _delete(ctx: Context[AppContext], spec: ResourceSpec, resource_id: str) -> dict[str, Any]:
    try:
        return await ctx.request_context.lifespan_context.pais.delete_resource(
            spec.delete_path_for(resource_id)
        )
    except PAISAPIError as exc:
        raise ToolError(
            f"PAIS API error deleting {spec.key} {resource_id!r} ({exc.status_code}): {exc.message}"
        ) from exc


LimitParam = Annotated[
    int | None, Field(ge=1, le=100, description="Maximum number of items to return.")
]
AfterParam = Annotated[
    str | None,
    Field(description="Pagination cursor: return items after this id (a previous response's last_id)."),
]


@mcp.tool()
async def list_agents(
    ctx: Context[AppContext], limit: LimitParam = None, after: AfterParam = None
) -> dict[str, Any]:
    """List agents defined in Private AI Services."""
    return await _list(ctx, AGENTS, limit, after)


@mcp.tool()
async def delete_agent(
    ctx: Context[AppContext],
    agent_id: Annotated[str, Field(description="ID of the agent to delete.")],
) -> dict[str, Any]:
    """Permanently delete an agent from Private AI Services."""
    return await _delete(ctx, AGENTS, agent_id)


@mcp.tool()
async def list_knowledge_bases(
    ctx: Context[AppContext], limit: LimitParam = None, after: AfterParam = None
) -> dict[str, Any]:
    """List knowledge bases defined in Private AI Services."""
    return await _list(ctx, KNOWLEDGE_BASES, limit, after)


@mcp.tool()
async def delete_knowledge_base(
    ctx: Context[AppContext],
    knowledge_base_id: Annotated[str, Field(description="ID of the knowledge base to delete.")],
) -> dict[str, Any]:
    """Permanently delete a knowledge base from Private AI Services."""
    return await _delete(ctx, KNOWLEDGE_BASES, knowledge_base_id)


@mcp.tool()
async def list_data_sources(
    ctx: Context[AppContext], limit: LimitParam = None, after: AfterParam = None
) -> dict[str, Any]:
    """List data sources defined in Private AI Services."""
    return await _list(ctx, DATA_SOURCES, limit, after)


@mcp.tool()
async def delete_data_source(
    ctx: Context[AppContext],
    data_source_id: Annotated[str, Field(description="ID of the data source to delete.")],
) -> dict[str, Any]:
    """Permanently delete a data source from Private AI Services."""
    return await _delete(ctx, DATA_SOURCES, data_source_id)
