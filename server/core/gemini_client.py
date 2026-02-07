"""
Gemini 3 Pro Client — Rate-limited async wrapper for the Google GenAI SDK.
All agents share a single request queue with configurable RPM/TPM limits.
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3-pro-preview"

# Cost estimates per 1M tokens (approximate)
COST_PER_1M_INPUT = 1.25
COST_PER_1M_OUTPUT = 5.00


@dataclass
class TokenUsage:
    """Tracks token usage per agent and globally."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost(self) -> float:
        return (
            (self.input_tokens / 1_000_000) * COST_PER_1M_INPUT
            + (self.output_tokens / 1_000_000) * COST_PER_1M_OUTPUT
        )

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost, 4),
        }


class GeminiClient:
    """
    Async Gemini 3 Pro client with built-in rate limiting.
    All agents share this single client instance.
    """

    def __init__(self, api_key: str, max_rpm: int = 10, max_retries: int = 3):
        self._api_key = api_key
        self.client = None
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini client: {e}")
        self.max_rpm = max_rpm
        self.max_retries = max_retries
        self._request_times: list[float] = []
        self._queue_lock = asyncio.Lock()
        self._global_usage = TokenUsage()
        self._agent_usage: dict[str, TokenUsage] = {}

    async def _wait_for_rate_limit(self):
        """Enforce RPM limit by waiting if necessary."""
        async with self._queue_lock:
            now = time.time()
            # Remove timestamps older than 60s
            self._request_times = [t for t in self._request_times if now - t < 60]
            if len(self._request_times) >= self.max_rpm:
                wait_time = 60 - (now - self._request_times[0]) + 0.1
                logger.info(f"Rate limit: waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
            self._request_times.append(time.time())

    def _track_usage(self, agent_id: str, response):
        """Track token usage from response metadata."""
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            meta = response.usage_metadata
            input_t = getattr(meta, 'prompt_token_count', 0) or 0
            output_t = getattr(meta, 'candidates_token_count', 0) or 0

            self._global_usage.input_tokens += input_t
            self._global_usage.output_tokens += output_t

            if agent_id not in self._agent_usage:
                self._agent_usage[agent_id] = TokenUsage()
            self._agent_usage[agent_id].input_tokens += input_t
            self._agent_usage[agent_id].output_tokens += output_t

    async def generate(
        self,
        agent_id: str,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> dict:
        """
        Generate a response from Gemini 3 Pro.
        Returns parsed JSON action dict, or raw text wrapped in a dict.
        """
        if not self.client:
            raise RuntimeError("Gemini client not initialized — set GEMINI_API_KEY in .env")

        await self._wait_for_rate_limit()

        # Build contents list from messages
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        for attempt in range(self.max_retries):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=MODEL_NAME,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )

                self._track_usage(agent_id, response)

                # Parse JSON response
                text = response.text.strip()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    # If Gemini didn't return valid JSON, wrap it
                    return {"thinking": "", "action": "message", "params": {}, "message": text}

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    wait = (2 ** attempt) * 2
                    logger.warning(f"Rate limited (attempt {attempt+1}), waiting {wait}s")
                    await asyncio.sleep(wait)
                elif "500" in error_str or "503" in error_str:
                    wait = (2 ** attempt) * 1
                    logger.warning(f"Server error (attempt {attempt+1}), retrying in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Gemini error: {e}")
                    raise

        raise RuntimeError(f"Failed after {self.max_retries} retries")

    def get_global_usage(self) -> dict:
        return self._global_usage.to_dict()

    def get_agent_usage(self, agent_id: str) -> dict:
        if agent_id in self._agent_usage:
            return self._agent_usage[agent_id].to_dict()
        return TokenUsage().to_dict()

    def get_all_usage(self) -> dict:
        return {
            "global": self.get_global_usage(),
            "agents": {
                aid: usage.to_dict()
                for aid, usage in self._agent_usage.items()
            },
        }
