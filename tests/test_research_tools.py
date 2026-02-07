import unittest
from unittest.mock import patch

from server.core.plugin_registry import PluginRegistry
from server.tools import web_research


class TestResearchTools(unittest.TestCase):
    def test_registry_includes_research_tools(self):
        registry = PluginRegistry()
        tool_names = {tool["name"] for tool in registry.list_tools()}
        self.assertIn("web_search", tool_names)
        self.assertIn("fetch_url", tool_names)

    def test_build_command_for_web_search(self):
        registry = PluginRegistry()
        cmd = registry.build_command(
            "web_search",
            workspace="/tmp/workspace",
            query="fastapi websocket",
            max_results=3,
        )
        self.assertIn("server.tools.web_research search", cmd)
        self.assertIn("--query \"fastapi websocket\"", cmd)
        self.assertIn("--max-results 3", cmd)

    @patch("server.tools.web_research._http_get")
    def test_search_web_parses_results(self, mock_http_get):
        mock_http_get.return_value = (
            '<a class="result__a" href="https://example.com/doc">Example Doc</a>'
            '<div class="result__snippet">FastAPI docs overview</div>'
        )
        result = web_research.search_web("fastapi docs", max_results=1)
        self.assertEqual(result["query"], "fastapi docs")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["url"], "https://example.com/doc")

    def test_fetch_url_blocks_non_allowlisted_domain(self):
        with self.assertRaises(ValueError):
            web_research.fetch_url_text("https://evil.example.com/page")


if __name__ == "__main__":
    unittest.main()
