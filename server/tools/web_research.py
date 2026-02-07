"""Web research helpers for agents.

Provides a constrained CLI for web search and URL fetching so agents can
research external references without unrestricted shell access.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 12
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_CHARS = 6000

# Restrict fetched pages to trusted documentation domains.
ALLOWED_FETCH_DOMAINS = {
    "docs.python.org",
    "fastapi.tiangolo.com",
    "developer.mozilla.org",
    "docs.pydantic.dev",
    "docs.pytest.org",
    "docs.github.com",
    "nodejs.org",
    "numpy.org",
    "pandas.pydata.org",
    "docs.docker.com",
}


class _TextExtractor(HTMLParser):
    """Extract visible text from simple HTML while skipping script/style blocks."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def _http_get(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "AgentSwarmResearchBot/1.0 (+https://localhost)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_duckduckgo_results(html: str, max_results: int) -> list[dict]:
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>|'
        r'<div[^>]*class="result__snippet"[^>]*>(?P<divsnippet>.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )

    href_titles = list(pattern.finditer(html))
    snippets = list(snippet_pattern.finditer(html))

    results: list[dict] = []
    for idx, match in enumerate(href_titles[:max_results]):
        raw_href = unquote(match.group("href"))
        if "uddg=" in raw_href:
            parsed = parse_qs(urlparse(raw_href).query)
            url = unquote(parsed.get("uddg", [raw_href])[0])
        else:
            url = raw_href
        title = re.sub(r"<[^>]+>", "", match.group("title"))
        snippet_match = snippets[idx] if idx < len(snippets) else None
        snippet_raw = ""
        if snippet_match:
            snippet_raw = snippet_match.group("snippet") or snippet_match.group("divsnippet") or ""
        snippet = re.sub(r"<[^>]+>", "", snippet_raw)
        results.append({"rank": idx + 1, "title": title.strip(), "url": url.strip(), "snippet": snippet.strip()})
    return results


def search_web(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> dict:
    """Search DuckDuckGo HTML endpoint and return compact results."""
    q = query.strip()
    if not q:
        raise ValueError("query cannot be empty")
    max_results = max(1, min(max_results, 10))
    html = _http_get(f"https://duckduckgo.com/html/?q={quote_plus(q)}")
    results = _extract_duckduckgo_results(html, max_results=max_results)
    return {"query": q, "results": results}


def _is_allowed_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_FETCH_DOMAINS or any(host.endswith(f".{d}") for d in ALLOWED_FETCH_DOMAINS)


def fetch_url_text(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict:
    """Fetch URL text from an allowlisted domain and return normalized output."""
    u = url.strip()
    if not _is_allowed_fetch_url(u):
        raise ValueError(
            "url domain is not allowlisted; use trusted documentation domains only"
        )

    max_chars = max(500, min(max_chars, 20000))
    html = _http_get(u)
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.get_text()
    normalized = re.sub(r"\n{3,}", "\n\n", text)
    return {
        "url": u,
        "max_chars": max_chars,
        "content": normalized[:max_chars],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Swarm web research utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Search the web")
    s.add_argument("--query", required=True)
    s.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)

    f = sub.add_parser("fetch", help="Fetch and extract URL text")
    f.add_argument("--url", required=True)
    f.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)

    args = parser.parse_args()

    try:
        if args.cmd == "search":
            output = search_web(query=args.query, max_results=args.max_results)
        else:
            output = fetch_url_text(url=args.url, max_chars=args.max_chars)
        print(json.dumps(output, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("web_research failed: %s", exc)
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
