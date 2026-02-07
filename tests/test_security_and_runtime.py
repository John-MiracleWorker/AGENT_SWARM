import asyncio
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.routes import create_router
from server.core.terminal import TerminalExecutor
from server.main import SwarmState


class _GeminiStub:
    def get_all_usage(self):
        return {"ok": True}


class _StateStub:
    def __init__(self, token: str):
        self.auth_token = token
        self.gemini = _GeminiStub()

    def is_authorized(self, token: str) -> bool:
        return token == self.auth_token


class TestApiAuth(unittest.TestCase):
    def _client(self, token: str) -> TestClient:
        app = FastAPI()
        app.include_router(create_router(_StateStub(token)), prefix="/api")
        return TestClient(app)

    def test_requires_api_key_when_configured(self):
        client = self._client("secret")
        response = client.get("/api/usage")
        self.assertEqual(response.status_code, 401)

    def test_accepts_valid_api_key(self):
        client = self._client("secret")
        response = client.get("/api/usage", headers={"X-API-Key": "secret"})
        self.assertEqual(response.status_code, 200)


class TestTerminalExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_subprocess_creation_failure_returns_error_result(self):
        executor = TerminalExecutor()

        async def fail(*_args, **_kwargs):
            raise RuntimeError("boom")

        with patch("asyncio.create_subprocess_shell", new=fail):
            result = await executor.execute("echo hi", cwd=".")

        self.assertEqual(result.return_code, -1)
        self.assertIn("boom", result.stderr)


class TestWorkspaceSwitching(unittest.TestCase):
    def test_switch_workspace_updates_workspace_root(self):
        state = SwarmState()
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            ws1 = state.add_workspace(one, "one")
            ws2 = state.add_workspace(two, "two")
            state.switch_workspace(ws1["id"])
            self.assertEqual(str(state.workspace.root), one)
            state.switch_workspace(ws2["id"])
            self.assertEqual(str(state.workspace.root), two)


if __name__ == "__main__":
    unittest.main()
