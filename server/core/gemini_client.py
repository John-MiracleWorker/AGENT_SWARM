"""
Gemini Client — Rate-limited async wrapper with intelligent model fallback.
Automatically swaps between models when rate limits or errors are hit.
All agents share a single request queue with configurable RPM/TPM limits.
Includes budget cap to prevent runaway API costs.
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


class BudgetExhaustedError(Exception):
    """Raised when the API budget limit is reached."""
    pass

# Model cascade — ordered by preference. Falls back on rate limit / errors.
MODEL_CASCADE = [
    {"name": "gemini-3-flash-preview",          "rpm": 10, "cost_in": 0.15, "cost_out": 0.60},
    {"name": "gemini-2.5-flash-preview-05-20",  "rpm": 10, "cost_in": 0.15, "cost_out": 0.60},
    {"name": "gemini-2.5-pro-preview-05-06",    "rpm": 5,  "cost_in": 1.25, "cost_out": 10.00},
    {"name": "gemini-2.0-flash",                "rpm": 15, "cost_in": 0.10, "cost_out": 0.40},
]

# Default cost estimates per 1M tokens
COST_PER_1M_INPUT = 0.15
COST_PER_1M_OUTPUT = 0.60


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


@dataclass
class ModelState:
    """Tracks per-model rate limit & health state."""
    name: str
    rpm_limit: int
    request_times: list = field(default_factory=list)
    cooldown_until: float = 0.0  # timestamp when cooldown expires
    consecutive_errors: int = 0

    @property
    def is_cooled_down(self) -> bool:
        return time.time() >= self.cooldown_until

    def record_success(self):
        self.consecutive_errors = 0
        self.request_times.append(time.time())
        # Prune old timestamps
        cutoff = time.time() - 60
        self.request_times = [t for t in self.request_times if t > cutoff]

    def record_rate_limit(self):
        """Back off this model for a while."""
        self.consecutive_errors += 1
        backoff = min(60 * (2 ** self.consecutive_errors), 300)  # max 5 min
        self.cooldown_until = time.time() + backoff
        logger.warning(f"Model {self.name} rate-limited, cooling down for {backoff}s")

    def record_error(self):
        self.consecutive_errors += 1
        self.cooldown_until = time.time() + 10  # short cooldown on errors

    @property
    def requests_in_window(self) -> int:
        cutoff = time.time() - 60
        self.request_times = [t for t in self.request_times if t > cutoff]
        return len(self.request_times)

    @property
    def has_capacity(self) -> bool:
        return self.is_cooled_down and self.requests_in_window < self.rpm_limit

    def wait_time(self) -> float:
        """How long until this model has capacity."""
        if not self.is_cooled_down:
            return self.cooldown_until - time.time()
        if self.requests_in_window >= self.rpm_limit:
            return 60 - (time.time() - self.request_times[0]) + 0.1
        return 0


class GeminiClient:
    """
    Async Gemini client with intelligent model fallback.
    Automatically rotates between models when rate limits are hit.
    """

    def __init__(self, api_key: str, max_retries: int = 3):
        self._api_key = api_key
        self.client = None
        if api_key:
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini client: {e}")
        self.max_retries = max_retries
        self._queue_lock = asyncio.Lock()
        self._global_usage = TokenUsage()
        self._agent_usage: dict[str, TokenUsage] = {}
        self._current_model: str = MODEL_CASCADE[0]["name"]

        # File context — injected file parts from Gemini Files API
        self._file_context = None  # Set via set_file_context()

        # Budget cap
        self._budget_limit_usd: float = 1.00  # Default $1 budget
        self._budget_warning_sent: bool = False
        self._budget_exceeded: bool = False
        self._on_budget_event = None  # Callback for budget warnings

        # Initialize model states
        self._models: dict[str, ModelState] = {}
        for m in MODEL_CASCADE:
            self._models[m["name"]] = ModelState(
                name=m["name"],
                rpm_limit=m["rpm"],
            )

    def _pick_best_model(self) -> Optional[str]:
        """Pick the best available model with capacity."""
        for m in MODEL_CASCADE:
            state = self._models[m["name"]]
            if state.has_capacity:
                if m["name"] != self._current_model:
                    logger.info(f"🔄 Switching to model: {m['name']}")
                self._current_model = m["name"]
                return m["name"]
        return None

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
        Generate a response using the best available Gemini model.
        Automatically falls back to other models on rate limit.
        """
        if not self.client:
            raise RuntimeError("Gemini client not initialized — set GEMINI_API_KEY in .env")

        # Budget check before making API call
        self._check_budget(agent_id)

        # Build contents list — start with uploaded file context
        contents = []

        # Inject uploaded codebase files for full project context
        if self._file_context and self._file_context.is_ready:
            file_parts = self._file_context.get_file_parts()
            if file_parts:
                file_summary = self._file_context.get_file_summary()
                contents.append(types.Content(
                    role="user",
                    parts=file_parts + [
                        types.Part.from_text(
                            text=f"Above are the project source files uploaded for context.\n{file_summary}"
                        )
                    ],
                ))
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(
                        text="I've received and reviewed the project files. I'll use them as context for my responses."
                    )],
                ))

        # Add conversation messages
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        last_error = None

        for attempt in range(self.max_retries * len(MODEL_CASCADE)):
            # Pick the best model with capacity
            async with self._queue_lock:
                model_name = self._pick_best_model()

            if not model_name:
                # All models exhausted — wait for the one with shortest cooldown
                min_wait = min(s.wait_time() for s in self._models.values())
                wait = max(min_wait, 1)
                logger.warning(f"All models exhausted, waiting {wait:.1f}s")
                await asyncio.sleep(wait)
                continue

            model_state = self._models[model_name]

            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )

                model_state.record_success()
                self._track_usage(agent_id, response)

                # Parse JSON response
                text = response.text.strip()
                try:
                    result = json.loads(text)
                    logger.info(f"[{agent_id}] ✅ {model_name} responded")
                    return result
                except json.JSONDecodeError:
                    return {"thinking": "", "action": "message", "params": {}, "message": text}

            except Exception as e:
                error_str = str(e)
                last_error = e

                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    model_state.record_rate_limit()
                    logger.warning(f"[{agent_id}] 🔄 {model_name} rate-limited, trying next model...")
                    continue  # will pick next model on next iteration

                elif "403" in error_str or "PERMISSION_DENIED" in error_str:
                    logger.error(f"[{agent_id}] 🔑 API key issue: {e}")
                    raise  # can't recover from auth errors

                elif "500" in error_str or "503" in error_str:
                    model_state.record_error()
                    wait = (2 ** (attempt % 3)) * 1
                    logger.warning(f"[{agent_id}] Server error on {model_name}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                else:
                    logger.error(f"[{agent_id}] Gemini error: {e}")
                    raise

        raise RuntimeError(f"Failed after exhausting all models and retries: {last_error}")

    @property
    def active_model(self) -> str:
        return self._current_model

    def get_model_states(self) -> list[dict]:
        """Get status of all models for the UI."""
        return [
            {
                "name": s.name,
                "active": s.name == self._current_model,
                "has_capacity": s.has_capacity,
                "requests_in_window": s.requests_in_window,
                "rpm_limit": s.rpm_limit,
                "cooled_down": s.is_cooled_down,
                "cooldown_remaining": max(0, s.cooldown_until - time.time()),
            }
            for s in self._models.values()
        ]

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
            "active_model": self._current_model,
            "models": self.get_model_states(),
            "budget": self.get_budget_status(),
        }

    # ─── Budget Management ──────────────────────────────────────

    def set_budget(self, limit_usd: float):
        """Set the budget limit in USD. Set to 0 for unlimited."""
        self._budget_limit_usd = limit_usd
        self._budget_exceeded = False
        self._budget_warning_sent = False
        logger.info(f"Budget set to ${limit_usd:.2f}")

    def get_budget_status(self) -> dict:
        """Get current budget status."""
        cost = self._global_usage.estimated_cost
        limit = self._budget_limit_usd
        pct = (cost / limit * 100) if limit > 0 else 0
        return {
            "limit_usd": round(limit, 2),
            "spent_usd": round(cost, 4),
            "remaining_usd": round(max(0, limit - cost), 4),
            "percent_used": round(min(pct, 100), 1),
            "exceeded": self._budget_exceeded,
            "warning": self._budget_warning_sent,
        }

    def _check_budget(self, agent_id: str):
        """Check budget and raise/warn if needed."""
        if self._budget_limit_usd <= 0:
            return  # Unlimited

        cost = self._global_usage.estimated_cost
        pct = cost / self._budget_limit_usd

        if pct >= 1.0 and not self._budget_exceeded:
            self._budget_exceeded = True
            logger.warning(f"🚨 Budget EXCEEDED: ${cost:.4f} / ${self._budget_limit_usd:.2f}")
            raise BudgetExhaustedError(
                f"Budget limit of ${self._budget_limit_usd:.2f} exceeded (spent ${cost:.4f})"
            )

        if pct >= 0.8 and not self._budget_warning_sent:
            self._budget_warning_sent = True
            logger.warning(f"⚠️ Budget WARNING: ${cost:.4f} / ${self._budget_limit_usd:.2f} (80%)")

    def set_budget_callback(self, callback):
        """Set a callback for budget events (for broadcasting to UI)."""
        self._on_budget_event = callback

    def set_file_context(self, file_context):
        """Attach a FileContextManager for codebase context injection."""
        self._file_context = file_context
