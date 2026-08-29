"""
Web search and URL extraction tools — adapted from Hermes Agent (NousResearch/hermes-agent).
MIT License. Uses DuckDuckGo (no API key) and Jina Reader for content extraction.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any, Dict, List, Optional

from packages.agent.tools.base import Tool, ToolContext, ToolResult
from packages.shared.logging import logger

# Max chars returned to model from a URL extraction
_MAX_EXTRACT_CHARS = 10_000
# Jina Reader base URL (free, no auth needed for public pages)
_JINA_BASE = "https://r.jina.ai/"
# DuckDuckGo instant answer endpoint (free, no API key)
_DDG_URL = "https://api.duckduckgo.com/"


async def _http_get(url: str, params: Optional[Dict] = None, timeout: float = 15.0) -> str:
    """Async HTTP GET using stdlib urllib (no extra deps)."""
    import urllib.request
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    loop = asyncio.get_event_loop()

    def _fetch():
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HELM-Agent/1.0 (autonomous coding assistant)",
                "Accept": "application/json, text/html, */*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    return await loop.run_in_executor(None, _fetch)


class WebSearchTool(Tool):
    """
    Search the web using DuckDuckGo — no API key required.
    Returns up to 10 results with title, URL, and snippet.
    Adapted from Hermes Agent's web_tools.py.
    """

    name = "web_search"
    description = (
        "Search the web for information. Returns ranked results with titles, URLs, "
        "and snippets. Use for unfamiliar APIs, library docs, error messages, or "
        "any information not in the codebase."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (be specific for best results)",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default: 8, max: 20)",
                "default": 8,
            },
        },
        "required": ["query"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments.get("query", "").strip()
        max_results = min(int(arguments.get("max_results", 8)), 20)

        if not query:
            return ToolResult(success=False, output="", error="Search query cannot be empty.")

        try:
            # DuckDuckGo instant answer API
            raw = await _http_get(
                _DDG_URL,
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
            )
            import json
            data = json.loads(raw)

            results: List[Dict] = []

            # Instant answer / abstract
            if data.get("AbstractText") and data.get("AbstractURL"):
                results.append({
                    "title": data.get("Heading", "DuckDuckGo Abstract"),
                    "url": data["AbstractURL"],
                    "snippet": data["AbstractText"][:300],
                })

            # Related topics
            for topic in data.get("RelatedTopics", []):
                if len(results) >= max_results:
                    break
                if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "url": topic["FirstURL"],
                        "snippet": topic.get("Text", "")[:300],
                    })
                elif isinstance(topic, dict) and topic.get("Topics"):
                    for sub in topic["Topics"]:
                        if len(results) >= max_results:
                            break
                        if sub.get("FirstURL") and sub.get("Text"):
                            results.append({
                                "title": sub.get("Text", "")[:80],
                                "url": sub["FirstURL"],
                                "snippet": sub.get("Text", "")[:300],
                            })

            if not results:
                return ToolResult(
                    success=True,
                    output=f"No results found for '{query}'. Try a more specific query.",
                )

            formatted = f"Search results for: '{query}'\n\n"
            for i, r in enumerate(results[:max_results], 1):
                formatted += f"{i}. **{r['title']}**\n   {r['url']}\n   {r['snippet']}\n\n"

            return ToolResult(success=True, output=formatted.strip())

        except Exception as e:
            logger.warning(f"WebSearchTool error: {e}")
            return ToolResult(success=False, output="", error=f"Search failed: {e}")


class WebExtractTool(Tool):
    """
    Extract readable text content from a URL using Jina Reader.
    Returns clean markdown — no JS execution needed.
    Adapted from Hermes Agent's web_tools.py.
    """

    name = "web_extract"
    description = (
        "Fetch and extract readable content from a URL as clean markdown. "
        "Use after web_search to read documentation, blog posts, or API references. "
        "Respects a 10,000 character limit per page."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch and extract content from",
            },
            "max_chars": {
                "type": "integer",
                "description": f"Max characters to return (default: {_MAX_EXTRACT_CHARS})",
                "default": _MAX_EXTRACT_CHARS,
            },
        },
        "required": ["url"],
    }

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        url = arguments.get("url", "").strip()
        max_chars = min(int(arguments.get("max_chars", _MAX_EXTRACT_CHARS)), _MAX_EXTRACT_CHARS * 2)

        if not url:
            return ToolResult(success=False, output="", error="URL cannot be empty.")
        if not url.startswith(("http://", "https://")):
            return ToolResult(success=False, output="", error="URL must start with http:// or https://")

        try:
            jina_url = _JINA_BASE + url
            content = await _http_get(jina_url, timeout=20.0)
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
            return ToolResult(
                success=True,
                output=content,
                metadata={"url": url, "chars": len(content)},
            )
        except Exception as e:
            logger.warning(f"WebExtractTool error for {url}: {e}")
            return ToolResult(success=False, output="", error=f"Failed to extract content: {e}")
