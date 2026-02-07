"""
Model Router — Multi-provider LLM client with role-based routing.
Routes agents to the best available model based on their role.
Orchestrator → Gemini (smart planning), Developers → Groq (fast coding).
Falls back across providers when rate limits are hit.
Backward-compatible with the old GeminiClient interface.
"""

import asyncio
import json
import time
import logging
from dataclasses import dataclass
from typing import Optional

from server.core.llm_providers import (
    GeminiProvider,
    GroqProvider,
    ModelState,
)

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when the API budget limit is reached."""
    pass


# ─── Model Definitions ────────────────────────────────────────────

MODELS = [
    # Gemini models
    {"name": "gemini-3-pro-preview",    "provider": "gemini",  "rpm": 5,  "cost_in": 1.25, "cost_out": 10.00, "tier": "premium"},
    {"name": "gemini-3-flash-preview",  "provider": "gemini",  "rpm": 10, "cost_in": 0.15, "cost_out": 0.60,  "tier": "standard"},
    {"name": "gemini-2.5-flash",        "provider": "gemini",  "rpm": 10, "cost_in": 0.15, "cost_out": 0.60,  "tier": "standard"},
    {"name": "gemini-2.5-pro",          "provider": "gemini",  "rpm": 5,  "cost_in": 1.25, "cost_out": 10.00, "tier": "premium"},
    {"name": "gemini-2.0-flash",        "provider": "gemini",  "rpm": 15, "cost_in": 0.10, "cost_out": 0.40,  "tier": "fast"},
    # Groq models (FREE)
    {"name": "deepseek-r1-0528",              "provider": "groq", "rpm": 30, "cost_in": 0.0, "cost_out": 0.0, "tier": "free-reasoning"},
    {"name": "llama-3.3-70b-versatile",       "provider": "groq", "rpm": 30, "cost_in": 0.0, "cost_out": 0.0, "tier": "free-coding"},
    {"name": "qwen-3-32b",                    "provider": "groq", "rpm": 30, "cost_in": 0.0, "cost_out": 0.0, "tier": "free-reasoning"},
    {"name": "llama-4-scout-17b-16e-instruct","provider": "groq", "rpm": 30, "cost_in": 0.0, "cost_out": 0.0, "tier": "free-coding"},
    {"name": "llama-3.1-8b-instant",          "provider": "groq", "rpm": 30, "cost_in": 0.0, "cost_out": 0.0, "tier": "free-fast"},
]

# Role-based model cascade — ordered by preference per role
ROLE_CASCADES = {
    "Orchestrator": [
        "gemini-3-pro-preview",
        "gemini-2.5-pro",
        "deepseek-r1-0528",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "Developer": [
        "llama-3.3-70b-versatile",
        "llama-4-scout-17b-16e-instruct",
        "qwen-3-32b",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "llama-3.1-8b-instant",
    ],
    "Reviewer": [
        "deepseek-r1-0528",
        "llama-3.3-70b-versatile",
        "gemini-2.5-flash",
        "qwen-3-32b",
    ],
    "Tester": [
        "llama-3.3-70b-versatile",
        "llama-4-scout-17b-16e-instruct",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "llama-3.1-8b-instant",
    ],
}

# Default cascade for unknown roles (including dynamic agents)
DEFAULT_CASCADE = [
    "llama-3.3-70b-versatile",
    "gemini-2.5-flash",
    "qwen-3-32b",
    "gemini-2.0-flash",
    "llama-3.1-8b-instant",
]

# Default cost estimates (for Gemini models used in budget tracking)
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


class ModelRouter:
    """
    Multi-provider LLM router with role-based model selection.
    Drop-in replacement for GeminiClient — same generate() interface.
    """

    def __init__(
        self,
        gemini_api_key: str = "",
        groq_api_key: str = "",
        max_retries: int = 3,
    ):
        # Initialize providers
        self._providers = {}

        self._gemini = GeminiProvider(gemini_api_key)
        if self._gemini.is_available:
            self._providers["gemini"] = self._gemini
            logger.info("✅ Gemini provider initialized")

        self._groq = GroqProvider(groq_api_key)
        if self._groq.is_available:
            self._providers["groq"] = self._groq
            logger.info("✅ Groq provider initialized (FREE models available)")

        if not self._providers:
            logger.error("❌ No LLM providers available! Set at least GEMINI_API_KEY.")

        # For backward compat — file_context uses this
        self.client = self._gemini.client if self._gemini.is_available else None

        self.max_retries = max_retries
        self._queue_lock = asyncio.Lock()
        self._global_usage = TokenUsage()
        self._agent_usage: dict[str, TokenUsage] = {}
        self._current_model: str = ""

        # File context — injected file parts from Gemini Files API
        self._file_context = None

        # Budget cap
        self._budget_limit_usd: float = 1.00
        self._budget_warning_sent: bool = False
        self._budget_exceeded: bool = False
        self._on_budget_event = None

        # Initialize model states
        self._models: dict[str, ModelState] = {}
        self._model_config: dict[str, dict] = {}
        for m in MODELS:
            provider_name = m["provider"]
            # Only register models whose provider is available
            if provider_name in self._providers:
                self._models[m["name"]] = ModelState(
                    name=m["name"],
                    provider=provider_name,
                    rpm_limit=m["rpm"],
                    cost_in=m.get("cost_in", 0),
                    cost_out=m.get("cost_out", 0),
                )
                self._model_config[m["name"]] = m

        # Set initial active model
        first_available = next(iter(self._models), None)
        self._current_model = first_available or "none"
        logger.info(f"📡 Model router ready — {len(self._models)} models across {len(self._providers)} providers")

    def _pick_best_model(self, role: str = "") -> Optional[str]:
        """Pick the best available model for the given agent role."""
        cascade = ROLE_CASCADES.get(role, DEFAULT_CASCADE)

        for model_name in cascade:
            if model_name in self._models:
                state = self._models[model_name]
                if state.has_capacity:
                    if model_name != self._current_model:
                        provider = self._model_config.get(model_name, {}).get("provider", "?")
                        tier = self._model_config.get(model_name, {}).get("tier", "?")
                        logger.info(f"🔄 Routing to {model_name} [{provider}/{tier}]")
                    self._current_model = model_name
                    return model_name

        # Fallback: try ANY model with capacity
        for name, state in self._models.items():
            if state.has_capacity:
                logger.warning(f"⚠️ Fallback to {name} (no role-preferred model available)")
                self._current_model = name
                return name

        return None

    def _get_provider(self, model_name: str):
        """Get the provider instance for a model."""
        config = self._model_config.get(model_name, {})
        provider_name = config.get("provider", "gemini")
        return self._providers.get(provider_name)

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
        role: str = "",
    ) -> dict:
        """
        Generate a response using the best available model for the agent's role.
        Automatically falls back across providers on rate limit.
        """
        if not self._providers:
            raise RuntimeError("No LLM providers initialized — set GEMINI_API_KEY or GROQ_API_KEY in .env")

        # Budget check
        self._check_budget(agent_id)

        last_error = None
        total_attempts = self.max_retries * len(self._models)

        for attempt in range(total_attempts):
            # Pick the best model for this role
            async with self._queue_lock:
                model_name = self._pick_best_model(role)

            if not model_name:
                min_wait = min(s.wait_time() for s in self._models.values())
                wait = max(min_wait, 1)
                logger.warning(f"All models exhausted, waiting {wait:.1f}s")
                await asyncio.sleep(wait)
                continue

            model_state = self._models[model_name]
            provider = self._get_provider(model_name)
            if not provider:
                model_state.record_error()
                continue

            try:
                response = await provider.generate(
                    model_name=model_name,
                    system_prompt=system_prompt,
                    contents=messages,
                    temperature=temperature,
                    file_context=self._file_context,
                )

                model_state.record_success()
                self._track_usage(agent_id, response)

                # Parse JSON response
                text = response.text.strip()

                # Strip markdown code fences if present
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                try:
                    result = json.loads(text)
                    provider_name = self._model_config.get(model_name, {}).get("provider", "?")
                    logger.info(f"[{agent_id}] ✅ {model_name} [{provider_name}] responded")
                    return result
                except json.JSONDecodeError:
                    return {"thinking": "", "action": "message", "params": {}, "message": text}

            except Exception as e:
                error_str = str(e)
                last_error = e

                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "rate_limit" in error_str.lower():
                    model_state.record_rate_limit()
                    logger.warning(f"[{agent_id}] 🔄 {model_name} rate-limited, trying next...")
                    continue

                elif "403" in error_str or "PERMISSION_DENIED" in error_str:
                    logger.error(f"[{agent_id}] 🔑 Auth error on {model_name}: {e}")
                    model_state.cooldown_until = time.time() + 600  # 10 min cooldown
                    continue  # try another model instead of crashing

                elif "404" in error_str or "NOT_FOUND" in error_str:
                    model_state.record_rate_limit()
                    model_state.cooldown_until = time.time() + 300
                    logger.warning(f"[{agent_id}] ⚠️ {model_name} not found, cooling 5min...")
                    continue

                elif "400" in error_str or "INVALID_ARGUMENT" in error_str:
                    # Try without JSON mode (Gemini-specific)
                    if isinstance(provider, GeminiProvider):
                        logger.warning(f"[{agent_id}] ⚠️ {model_name} rejected JSON mode, retrying...")
                        try:
                            response = await provider.generate_no_json_mode(
                                model_name=model_name,
                                system_prompt=system_prompt,
                                contents=messages,
                                temperature=temperature,
                                file_context=self._file_context,
                            )
                            model_state.record_success()
                            self._track_usage(agent_id, response)
                            text = response.text.strip()
                            if text.startswith("```"):
                                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                            try:
                                result = json.loads(text)
                                logger.info(f"[{agent_id}] ✅ {model_name} responded (no-JSON-mode)")
                                return result
                            except json.JSONDecodeError:
                                return {"thinking": "", "action": "message", "params": {}, "message": text}
                        except Exception as retry_e:
                            logger.error(f"[{agent_id}] Retry failed: {retry_e}")
                            model_state.record_error()
                            continue
                    else:
                        model_state.record_error()
                        continue

                elif "500" in error_str or "503" in error_str:
                    model_state.record_error()
                    wait = (2 ** (attempt % 3))
                    logger.warning(f"[{agent_id}] Server error on {model_name}, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                else:
                    logger.error(f"[{agent_id}] LLM error ({model_name}): {e}")
                    model_state.record_error()
                    continue

        raise RuntimeError(f"Failed after exhausting all models and retries: {last_error}")

    # ─── Backward Compatibility Properties ────────────────────────

    @property
    def active_model(self) -> str:
        return self._current_model

    def get_model_states(self) -> list[dict]:
        """Get status of all models for the UI."""
        return [
            {
                "name": s.name,
                "provider": s.provider,
                "active": s.name == self._current_model,
                "has_capacity": s.has_capacity,
                "requests_in_window": s.requests_in_window,
                "rpm_limit": s.rpm_limit,
                "cooled_down": s.is_cooled_down,
                "cooldown_remaining": max(0, s.cooldown_until - time.time()),
                "tier": self._model_config.get(s.name, {}).get("tier", "unknown"),
                "cost_in": s.cost_in,
                "cost_out": s.cost_out,
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
            "providers": list(self._providers.keys()),
        }

    # ─── Budget Management ──────────────────────────────────────

    def set_budget(self, limit_usd: float):
        self._budget_limit_usd = limit_usd
        self._budget_exceeded = False
        self._budget_warning_sent = False
        logger.info(f"Budget set to ${limit_usd:.2f}")

    def get_budget_status(self) -> dict:
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
        if self._budget_limit_usd <= 0:
            return
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
        self._on_budget_event = callback

    def set_file_context(self, file_context):
        self._file_context = file_context


# Backward compatibility alias
GeminiClient = ModelRouter
