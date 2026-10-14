from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .config import WebSearchConfig

logger = logging.getLogger(__name__)

Engine = str  # "google" | "duckduckgo"

_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "form", "svg")
_PREVIEW_CHARS = 300
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_API_VERSION = "2022-11-28"


class SearchError(Exception):
    """Raised when a search engine or page fetch fails."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: Engine


@dataclass(frozen=True)
class GitHubCodeResult:
    repo: str
    path: str
    url: str
    snippet: str


@dataclass(frozen=True)
class GitHubRepoResult:
    full_name: str
    description: str
    url: str
    stars: int
    language: str | None


class SearchClient:
    """Wraps ddgs (Google/DuckDuckGo scraping, no API keys) and an HTTP client for page fetches."""

    def __init__(self, config: WebSearchConfig) -> None:
        self._config = config
        self._ddgs = DDGS(proxy=config.proxy, timeout=int(round(config.timeout)), verify=config.verify_ssl)
        self._http = httpx.AsyncClient(
            headers={"User-Agent": config.user_agent},
            timeout=config.timeout,
            verify=config.verify_ssl,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, engines: tuple[Engine, ...], num_results: int) -> list[SearchResult]:
        logger.info("search: query=%r engines=%s num_results=%d", query, engines, num_results)
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for engine in engines:
            try:
                raw = await asyncio.to_thread(
                    self._ddgs.text, query, max_results=num_results, backend=engine
                )
            except DDGSException as exc:
                logger.warning("search: engine=%s failed for query=%r: %s", engine, query, exc)
                if len(engines) == 1:
                    raise SearchError(f"{engine} search failed: {exc}") from exc
                continue  # best-effort when combining engines: skip a failing one

            logger.debug("search: engine=%s scraped %d raw result(s) for query=%r", engine, len(raw), query)
            new_count = 0
            for item in raw:
                url = item.get("href") or ""
                title = item.get("title", "")
                if not url:
                    logger.debug("search: engine=%s dropped result with no url: title=%r", engine, title)
                    continue
                if url in seen_urls:
                    logger.debug("search: engine=%s skipped duplicate url=%s", engine, url)
                    continue
                seen_urls.add(url)
                logger.debug("search: engine=%s scraped result title=%r url=%s", engine, title, url)
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=item.get("body", ""),
                        engine=engine,
                    )
                )
                new_count += 1
            logger.debug("search: engine=%s contributed %d new unique result(s)", engine, new_count)

        if not results and len(engines) > 1:
            raise SearchError(f"No results from any of: {', '.join(engines)}")

        final = results[:num_results]
        logger.info("search: query=%r returning %d result(s)", query, len(final))
        return final

    async def fetch_content(self, url: str, max_chars: int) -> str:
        logger.info("fetch_content: GET %s", url)
        try:
            response = await self._http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("fetch_content: failed to fetch %s: %s", url, exc)
            raise SearchError(f"Failed to fetch {url}: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        logger.debug(
            "fetch_content: %s -> status=%d content-type=%r bytes=%d",
            url,
            response.status_code,
            content_type,
            len(response.content),
        )

        if "html" not in content_type.lower():
            text = response.text[:max_chars]
            logger.debug("fetch_content: non-HTML response, returning %d raw character(s)", len(text))
            return text

        extracted = _extract_readable_text(response.text)
        text = extracted[:max_chars]
        logger.debug(
            "fetch_content: extracted %d character(s) of readable text from %s (%d after max_chars=%d cap)",
            len(extracted),
            url,
            len(text),
            max_chars,
        )
        logger.debug("fetch_content: preview: %s", text[:_PREVIEW_CHARS].replace("\n", " "))
        return text

    async def search_github_code(
        self,
        query: str,
        num_results: int,
        language: str | None = None,
        repo: str | None = None,
    ) -> list[GitHubCodeResult]:
        if not self._config.github_token:
            raise SearchError(
                "GitHub code search requires a GITHUB_TOKEN (personal access token with "
                "public_repo/no scopes needed for public code) to be configured on the server."
            )

        q = query
        if language:
            q += f" language:{language}"
        if repo:
            q += f" repo:{repo}"

        logger.info(
            "search_github_code: query=%r language=%s repo=%s num_results=%d", query, language, repo, num_results
        )
        data = await self._github_get(
            "/search/code",
            params={"q": q, "per_page": min(num_results, 100)},
            accept="application/vnd.github.text-match+json",
        )

        results: list[GitHubCodeResult] = []
        for item in data.get("items", [])[:num_results]:
            matches = item.get("text_matches") or []
            snippet = matches[0].get("fragment", "") if matches else ""
            repo_full_name = item.get("repository", {}).get("full_name", "")
            logger.debug(
                "search_github_code: scraped repo=%s path=%s url=%s", repo_full_name, item.get("path"), item.get("html_url")
            )
            results.append(
                GitHubCodeResult(
                    repo=repo_full_name,
                    path=item.get("path", ""),
                    url=item.get("html_url", ""),
                    snippet=snippet,
                )
            )

        logger.info("search_github_code: query=%r returning %d result(s)", query, len(results))
        return results

    async def search_github_repositories(
        self, query: str, num_results: int, language: str | None = None
    ) -> list[GitHubRepoResult]:
        q = query
        if language:
            q += f" language:{language}"

        logger.info("search_github_repositories: query=%r language=%s num_results=%d", query, language, num_results)
        data = await self._github_get(
            "/search/repositories",
            params={"q": q, "per_page": min(num_results, 100), "sort": "stars", "order": "desc"},
        )

        results = [
            GitHubRepoResult(
                full_name=item.get("full_name", ""),
                description=item.get("description") or "",
                url=item.get("html_url", ""),
                stars=item.get("stargazers_count", 0),
                language=item.get("language"),
            )
            for item in data.get("items", [])[:num_results]
        ]
        logger.info("search_github_repositories: query=%r returning %d result(s)", query, len(results))
        return results

    async def _github_get(
        self, path: str, *, params: dict[str, object], accept: str = "application/vnd.github+json"
    ) -> dict:
        headers = {"Accept": accept, "X-GitHub-Api-Version": _GITHUB_API_VERSION}
        if self._config.github_token:
            headers["Authorization"] = f"Bearer {self._config.github_token}"

        try:
            response = await self._http.get(f"{_GITHUB_API_BASE}{path}", params=params, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _extract_github_error_detail(exc.response)
            logger.warning("github api: %s%s failed (%d): %s", _GITHUB_API_BASE, path, exc.response.status_code, detail)
            raise SearchError(f"GitHub API error ({exc.response.status_code}): {detail}") from exc
        except httpx.HTTPError as exc:
            logger.warning("github api: %s%s failed: %s", _GITHUB_API_BASE, path, exc)
            raise SearchError(f"Failed to reach GitHub API at {path}: {exc}") from exc

        return response.json()


def _extract_github_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return str(body)


def _extract_readable_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = (line.strip() for line in text.splitlines())
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)
