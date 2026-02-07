# Coding Models Research for Agent Swarm

> Research compiled February 2026. Focused on free/low-cost alternatives to improve the Agent Swarm multi-agent coding platform.

---

## Current Setup Summary

Agent Swarm currently uses:
- **Google Gemini** (paid) — `gemini-3-pro-preview`, `gemini-2.5-flash`, `gemini-2.0-flash`
- **Groq** (free) — `openai/gpt-oss-120b`, `llama-4-maverick`, `qwen3-32b`, `llama-3.3-70b`

Role-based routing sends the Orchestrator to premium Gemini models while Developers/Reviewers/Testers prefer free Groq models. Budget defaults to $1.00/mission.

---

## Part 1: Free / Open-Source Models (Self-Hostable)

### Tier 1 — Best Overall

| Model | Params | Context | HumanEval | SWE-bench | License | Best For |
|---|---|---|---|---|---|---|
| **Qwen2.5-Coder 32B** | 32B | 131K | 88.4% | — | Apache 2.0 | General coding, debugging, explanations |
| **Qwen3-Coder** | 480B (35B active) | 256K–1M | — | SOTA open | Apache 2.0 | Agentic coding, tool use |
| **DeepSeek V3.2** | 685B (37B active) | 128K | 82.6% | GPT-5 class | MIT | Reasoning + code generation |
| **GLM-4.7** (Zhipu AI) | ~355B | — | — | 91.2% | Open | Agentic coding (best SWE-bench open model) |

### Tier 2 — Strong Alternatives

| Model | Params | Context | License | Best For |
|---|---|---|---|---|
| **Devstral Small 2** (Mistral) | 24B | 256K | Apache 2.0 | Large-context repo analysis |
| **Devstral 2** (Mistral) | 123B | 256K | Apache 2.0 | Agentic coding (72.2% SWE-bench) |
| **gpt-oss 20B** (OpenAI) | 20B | — | Open | Strong tool use on consumer GPU |
| **Kimi K2 Thinking** (Moonshot) | Large MoE | — | Open | Agentic coding |
| **MiniMax-M2.1** | 230B (10B active) | 204K | Open | Fast inference (60 tok/s), agent focus |

### Tier 3 — Lightweight / Specialized

| Model | Params | Context | License | Best For |
|---|---|---|---|---|
| **StarCoder2 15B** | 15B | 16K | OpenRAIL | Code completion / fill-in-the-middle (600+ languages) |
| **IBM Granite 4.0** | 3B–32B MoE | 128K | Apache 2.0 | Enterprise-safe (ISO 42001, license-safe data) |
| **Qwen3-Next-80B-A3B** | 80B (3B active) | — | Apache 2.0 | Ultra-efficient on consumer hardware |
| **CodeLlama 34B** | 34B | 16K | Llama License | Legacy; surpassed by above models |

---

## Part 2: Free API Tiers

### Ranked by Generosity

| Provider | Free Allocation | Best Models Available | Speed | Tool Use | OpenAI Compatible |
|---|---|---|---|---|---|
| **Mistral AI** | 1B tokens/month | Devstral 2 (promo free), Mistral Small 3 | Good | Yes | Yes |
| **Google AI Studio** | 15 RPM (Flash), 5 RPM (Pro) | Gemini 2.5 Pro, 2.5 Flash (1M ctx) | Good | Yes | No (own SDK) |
| **Groq** | ~500K–1M tokens/day | Llama 4, Qwen3, gpt-oss, Kimi K2 | **300+ tok/s** | Yes | Yes |
| **OpenRouter** | 18+ free models ($0.00) | Google, Meta, Mistral, NVIDIA models | Varies | Varies | Yes |
| **Together AI** | $25 free credits | Llama 4, DeepSeek, Qwen, Mixtral | Good | Yes | Yes |
| **Hugging Face** | ~few hundred req/hour | 300+ models | Slow | No | No |
| **Cloudflare Workers AI** | 10,000 Neurons/day | DeepSeek Coder, Mistral Small 3.1 | Good | No | No |

### Recommendations for Agent Swarm

1. **Mistral AI** — Most generous free tier (1B tokens/month). Devstral 2 is currently free during promotional period. Strong coding quality.
2. **Groq** — Already integrated. Fastest inference. Good free tier for developer/tester agents.
3. **OpenRouter** — One API key, 18+ free models. Great as a fallback routing layer.
4. **Google Gemini** — Already integrated. 1M token context unmatched. Good for orchestrator.

---

## Part 3: Paid API Pricing Comparison

### Cost per 1M Tokens (Cheapest to Most Expensive)

| Provider / Model | Input | Output | Context | Quality Tier |
|---|---|---|---|---|
| **DeepSeek R1 Distill 70B** | $0.03 | ~$0.10 | 128K | Reasoning |
| **OpenAI GPT-5 Nano** | $0.05 | $0.40 | — | Budget |
| **Devstral Small 2** | $0.10 | $0.30 | 256K | Coding |
| **Gemini 2.0 Flash** | $0.10 | $0.40 | 1M | Fast |
| **Gemini 2.5 Flash** | $0.15 | $0.60 | 1M | Standard |
| **DeepSeek V3.2** | $0.28 | $0.42 | 128K | Frontier |
| **Mistral Medium 3** | $0.40 | $2.00 | 131K | GPT-4 class |
| **Gemini 2.5 Pro** | $1.25 | $10.00 | 1M | Premium |
| **OpenAI GPT-5** | $1.25 | $10.00 | 128K+ | Premium |
| **Claude 4.5 Haiku** | $1.00 | $5.00 | 200K | Fast |
| **Claude 4.5 Sonnet** | $3.00 | $15.00 | 200K | Balanced |
| **OpenAI GPT-5.2** | $1.75 | $14.00 | 128K+ | Flagship |
| **Claude 4.5 Opus** | $5.00 | $25.00 | 200K | Flagship |

---

## Part 4: Concrete Recommendations for Agent Swarm

### Immediate Actions (No Code Changes)

1. **Add Mistral as a provider** — 1B free tokens/month is a massive upgrade. Devstral 2 is specifically tuned for coding agents and is currently free. This should be the highest priority integration.

2. **Add OpenRouter as a fallback** — Single API key gives access to 18+ free models. Acts as a universal fallback when Groq/Gemini hit rate limits.

3. **Add DeepSeek API** — At $0.28/$0.42 per 1M tokens, it's 10–30x cheaper than Gemini Pro for comparable frontier quality. Use as the paid fallback instead of Gemini Pro.

### Suggested Model Routing Updates

| Agent Role | Primary (Free) | Secondary (Free) | Paid Fallback |
|---|---|---|---|
| **Orchestrator** | Mistral Devstral 2 (promo free) | Gemini 2.5 Flash (free tier) | DeepSeek V3.2 ($0.28/$0.42) |
| **Developer** | Groq gpt-oss-120b | Mistral Devstral Small 2 | DeepSeek V3.2 |
| **Reviewer** | Groq qwen3-32b | Mistral Devstral 2 | DeepSeek V3.2 |
| **Tester** | Groq gpt-oss-120b | Mistral Devstral Small 2 | DeepSeek V3.2 |

### Estimated Cost Savings

| Scenario | Current Cost | With Recommended Changes |
|---|---|---|
| Light mission (mostly free models) | ~$0.10–0.50 | ~$0.00 (all free tier) |
| Medium mission (some paid fallback) | ~$0.50–2.00 | ~$0.05–0.20 (DeepSeek fallback) |
| Heavy mission (lots of premium calls) | ~$5–20 | ~$0.50–2.00 (DeepSeek instead of Gemini Pro) |

### Self-Hosting Option

If the project has access to a GPU server (A100/H100 or even an RTX 4090):
- **Qwen2.5-Coder 32B** — Best open-source coding model, fits on a single 80GB GPU
- **Devstral Small 2 (24B)** — Apache 2.0, 256K context, fits on consumer GPU
- **gpt-oss 20B** — Strong tool use, fits on 24GB+ GPU

Serve via [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), or [llama.cpp](https://github.com/ggerganov/llama.cpp) for zero ongoing cost.

---

## Part 5: Key Takeaways

1. **The open-source gap has closed.** GLM-4.7 (91.2% SWE-bench), DeepSeek V3.2, and Qwen3-Coder match or exceed proprietary models on coding benchmarks.

2. **DeepSeek is the value king.** At $0.28/$0.42 per million tokens with MIT license, it is 10–30x cheaper than GPT-5/Claude for comparable quality.

3. **Mistral's free tier is the most generous** at 1B tokens/month — enough for significant production usage, not just prototyping.

4. **Agentic coding is the new frontier.** Models are judged on tool use, repo navigation, and real-world SWE tasks. GLM-4.7, Qwen3-Coder, and Devstral 2 lead here.

5. **For Agent Swarm specifically**, adding Mistral + DeepSeek as providers would dramatically reduce costs (potentially to near-zero for most missions) while maintaining or improving quality.

---

*Sources: index.dev, whatllm.org, noviai.ai, qwenlm.github.io, mistral.ai, deepseek.com, openrouter.ai, groq.com, ai.google.dev, huggingface.co, fireworks.ai, arxiv.org, ibm.com, prompt.16x.engineer, ucstrategies.com*
