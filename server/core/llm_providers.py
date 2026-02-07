"""
LLM Providers — Abstraction layer for multiple LLM API providers.
Each provider implements generate() with a common interface.
"""

import asyncio
import json
import time
import logging
import httpx
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelState:
    """Tracks per-model rate limit & health state."""
    name: str
    provider: str
    rpm_limit: int
    cost_in: float = 0.0   # per 1M input tokens
    cost_out: float = 0.0  # per 1M output tokens
    request_times: list = field(default_factory=list)
    cooldown_until: float = 0.0
    consecutive_errors: int = 0

    @property
    def is_cooled_down(self) -> bool:
        if time.time() >= self.cooldown_until:
            if self.consecutive_errors > 0:
                logger.info(f"Model {self.name} cooldown expired, resetting for retry")
                self.consecutive_errors = 0
            return True
        return False

    def record_success(self):
        self.consecutive_errors = 0
        self.request_times.append(time.time())
        cutoff = time.time() - 60
        self.request_times = [t for t in self.request_times if t > cutoff]

    def record_rate_limit(self):
        self.consecutive_errors += 1
        backoff = min(60 * (2 ** self.consecutive_errors), 300)
        self.cooldown_until = time.time() + backoff
        logger.warning(f"Model {self.name} rate-limited, cooling down for {backoff}s")

    def record_error(self):
        self.consecutive_errors += 1
        self.cooldown_until = time.time() + 10

    @property
    def requests_in_window(self) -> int:
        cutoff = time.time() - 60
        self.request_times = [t for t in self.request_times if t > cutoff]
        return len(self.request_times)

    @property
    def has_capacity(self) -> bool:
        return self.is_cooled_down and self.requests_in_window < self.rpm_limit

    def wait_time(self) -> float:
        if not self.is_cooled_down:
            return self.cooldown_until - time.time()
        if self.requests_in_window >= self.rpm_limit:
            return 60 - (time.time() - self.request_times[0]) + 0.1
        return 0


class GeminiProvider:
    """Google Gemini API via google-genai SDK."""

    PROVIDER_NAME = "gemini"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        if api_key:
            from google import genai
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini client: {e}")

    @property
    def is_available(self) -> bool:
        return self.client is not None

    async def generate(
        self,
        model_name: str,
        system_prompt: str,
        contents: list,
        temperature: float = 0.7,
        file_context=None,
    ) -> str:
        """Generate response. Returns raw text."""
        from google.genai import types

        # Build contents with optional file context
        full_contents = []

        if file_context and file_context.is_ready:
            file_parts = file_context.get_file_parts()
            if file_parts:
                file_summary = file_context.get_file_summary()
                full_contents.append(types.Content(
                    role="user",
                    parts=file_parts + [
                        types.Part.from_text(
                            text=f"Above are the project source files uploaded for context.\n{file_summary}"
                        )
                    ],
                ))
                full_contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(
                        text="I've received and reviewed the project files. I'll use them as context for my responses."
                    )],
                ))

        # Add conversation messages
        for msg in contents:
            role = "user" if msg["role"] == "user" else "model"
            full_contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=model_name,
            contents=full_contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                response_mime_type="application/json",
            ),
        )
        return response

    async def generate_no_json_mode(
        self,
        model_name: str,
        system_prompt: str,
        contents: list,
        temperature: float = 0.7,
        file_context=None,
    ) -> str:
        """Retry without JSON mode for models that don't support it."""
        from google.genai import types

        full_contents = []
        if file_context and file_context.is_ready:
            file_parts = file_context.get_file_parts()
            if file_parts:
                file_summary = file_context.get_file_summary()
                full_contents.append(types.Content(
                    role="user",
                    parts=file_parts + [
                        types.Part.from_text(text=f"Above are project files.\n{file_summary}")
                    ],
                ))
                full_contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Got it. I'll use these for context.")],
                ))
        for msg in contents:
            role = "user" if msg["role"] == "user" else "model"
            full_contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=model_name,
            contents=full_contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt + "\n\nYou MUST respond with valid JSON only.",
                temperature=temperature,
            ),
        )
        return response


class GroqProvider:
    """Groq API — OpenAI-compatible, ultra-fast inference on OSS models."""

    PROVIDER_NAME = "groq"
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) if api_key else None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def generate(
        self,
        model_name: str,
        system_prompt: str,
        contents: list,
        temperature: float = 0.7,
        file_context=None,
    ):
        """Generate response via Groq's OpenAI-compatible API."""
        messages = [{"role": "system", "content": system_prompt}]

        # Inject file context as a system message
        if file_context and file_context.is_ready:
            file_summary = file_context.get_file_summary()
            messages.append({
                "role": "system",
                "content": f"Project file context:\n{file_summary}",
            })

        # Convert messages to OpenAI format
        for msg in contents:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            messages.append({"role": role, "content": msg["content"]})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": 8192,
        }

        response = await self._client.post(self.BASE_URL, json=payload)
        response.raise_for_status()
        data = response.json()

        return GroqResponse(data)


class GroqResponse:
    """Wraps Groq API response to match the interface expected by ModelRouter."""

    def __init__(self, data: dict):
        self._data = data
        self.text = data["choices"][0]["message"]["content"]
        self.usage_metadata = GroqUsage(data.get("usage", {}))


class GroqUsage:
    """Wraps Groq usage data."""

    def __init__(self, usage: dict):
        self.prompt_token_count = usage.get("prompt_tokens", 0)
        self.candidates_token_count = usage.get("completion_tokens", 0)
