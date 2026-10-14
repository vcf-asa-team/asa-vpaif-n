from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from pydantic import Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .client import GitHubCodeResult, GitHubRepoResult, SearchClient, SearchError
from .config import WebSearchConfig, configure_logging

configure_logging()
logger = logging.getLogger(__name__)

ENGINE_BACKENDS: dict[str, tuple[str, ...]] = {
    "duckduckgo": ("duckduckgo",),
    "google": ("google",),
    "both": ("duckduckgo", "google"),
}


@dataclass
class AppContext:
    search: SearchClient
    config: WebSearchConfig


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    config = WebSearchConfig.from_env()
    client = SearchClient(config)
    try:
        yield AppContext(search=client, config=config)
    finally:
        await client.aclose()


mcp = MCPServer("pais-mcp-web-search", lifespan=app_lifespan)


EngineParam = Annotated[
    Literal["duckduckgo", "google", "both"],
    Field(description="Which search engine(s) to query. 'both' merges and de-duplicates results."),
]
NumResultsParam = Annotated[
    int | None,
    Field(
        ge=1,
        le=50,
        description=(
            "How many sources to return (how many search results/URLs the tool gives back). "
            "Defaults to the server's configured default; capped at the server's configured maximum."
        ),
    ),
]
QueryParam = Annotated[str, Field(description="The search query, typically derived from the user's question.")]


@mcp.tool()
async def web_search(
    ctx: Context[AppContext],
    query: QueryParam,
    engine: EngineParam = "duckduckgo",
    num_results: NumResultsParam = None,
) -> dict[str, Any]:
    """Search the web (Google and/or DuckDuckGo) and return a list of matching sources.

    Use this to gather up-to-date information from the internet to answer the
    user's question. Each result includes a title, URL, and snippet; follow up
    with fetch_page_content on a URL to read the full page if the snippet isn't
    enough.
    """
    config = ctx.request_context.lifespan_context.config
    n = min(num_results or config.default_num_results, config.max_num_results)
    engines = ENGINE_BACKENDS[engine]

    logger.info("tool web_search: query=%r engine=%s num_results=%d", query, engine, n)
    try:
        results = await ctx.request_context.lifespan_context.search.search(query, engines, n)
    except SearchError as exc:
        logger.warning("tool web_search: failed for query=%r: %s", query, exc)
        raise ToolError(str(exc)) from exc

    for r in results:
        logger.info("tool web_search: result [%s] %s -> %s", r.engine, r.title, r.url)

    return {
        "query": query,
        "engine": engine,
        "num_results": len(results),
        "results": [asdict(r) for r in results],
    }


MaxCharsParam = Annotated[
    int | None,
    Field(
        ge=200,
        le=50_000,
        description=(
            "Maximum characters of extracted page text to return. Defaults to the server's "
            "configured default; capped at the server's configured maximum."
        ),
    ),
]


@mcp.tool()
async def fetch_page_content(
    ctx: Context[AppContext],
    url: Annotated[str, Field(description="URL of a web page to fetch, e.g. one returned by web_search.")],
    max_chars: MaxCharsParam = None,
) -> dict[str, Any]:
    """Fetch a web page and extract its main readable text content.

    Use this after web_search when a result's snippet doesn't give enough
    detail to answer the user's question and you need the full page text.
    """
    config = ctx.request_context.lifespan_context.config
    limit = min(max_chars or config.default_fetch_max_chars, config.max_fetch_chars)

    logger.info("tool fetch_page_content: url=%s max_chars=%d", url, limit)
    try:
        content = await ctx.request_context.lifespan_context.search.fetch_content(url, limit)
    except SearchError as exc:
        logger.warning("tool fetch_page_content: failed for url=%s: %s", url, exc)
        raise ToolError(str(exc)) from exc

    logger.info("tool fetch_page_content: url=%s returned %d character(s)", url, len(content))
    return {"url": url, "characters": len(content), "content": content}


CodeQueryParam = Annotated[
    str,
    Field(
        description=(
            "The code search query, e.g. a function/class name, error message, import "
            "statement, or API usage pattern you want real-world examples of."
        )
    ),
]
LanguageParam = Annotated[
    str | None,
    Field(description="Restrict results to a programming language, e.g. 'python', 'typescript', 'go'."),
]
RepoParam = Annotated[
    str | None,
    Field(description="Restrict results to one repository, in 'owner/name' form, e.g. 'modelcontextprotocol/python-sdk'."),
]


@mcp.tool()
async def github_search_code(
    ctx: Context[AppContext],
    query: CodeQueryParam,
    language: LanguageParam = None,
    repo: RepoParam = None,
    num_results: NumResultsParam = None,
) -> dict[str, Any]:
    """Search code across public GitHub repositories for real-world usage examples.

    Use this when coding/vibe-coding and you want to see how other projects call
    an API, structure a pattern, or fix a specific error, rather than general web
    results. Each result includes the repo, file path, a matched code snippet, and
    a link; follow up with fetch_page_content on the url for the full file.
    Requires the server to be configured with GITHUB_TOKEN.
    """
    config = ctx.request_context.lifespan_context.config
    n = min(num_results or config.default_num_results, config.max_num_results)

    logger.info("tool github_search_code: query=%r language=%s repo=%s num_results=%d", query, language, repo, n)
    try:
        results: list[GitHubCodeResult] = await ctx.request_context.lifespan_context.search.search_github_code(
            query, n, language=language, repo=repo
        )
    except SearchError as exc:
        logger.warning("tool github_search_code: failed for query=%r: %s", query, exc)
        raise ToolError(str(exc)) from exc

    for r in results:
        logger.info("tool github_search_code: result %s/%s -> %s", r.repo, r.path, r.url)

    return {
        "query": query,
        "num_results": len(results),
        "results": [asdict(r) for r in results],
    }


@mcp.tool()
async def github_search_repositories(
    ctx: Context[AppContext],
    query: Annotated[
        str, Field(description="Repository search query, e.g. a library name, framework, or project topic.")
    ],
    language: LanguageParam = None,
    num_results: NumResultsParam = None,
) -> dict[str, Any]:
    """Search GitHub repositories (sorted by stars) matching a query.

    Use this to find relevant libraries, example projects, or starter templates
    when coding — a complement to github_search_code for discovering *which*
    repos to look at before searching their code.
    """
    config = ctx.request_context.lifespan_context.config
    n = min(num_results or config.default_num_results, config.max_num_results)

    logger.info("tool github_search_repositories: query=%r language=%s num_results=%d", query, language, n)
    try:
        results: list[GitHubRepoResult] = await ctx.request_context.lifespan_context.search.search_github_repositories(
            query, n, language=language
        )
    except SearchError as exc:
        logger.warning("tool github_search_repositories: failed for query=%r: %s", query, exc)
        raise ToolError(str(exc)) from exc

    for r in results:
        logger.info("tool github_search_repositories: result %s (%d stars) -> %s", r.full_name, r.stars, r.url)

    return {
        "query": query,
        "num_results": len(results),
        "results": [asdict(r) for r in results],
    }
