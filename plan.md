You:

I want to implement Redwood-research-style AI Control as an LLM proxy (trusted supervision, untrusted supervision, resampling) in a way that enables easy adoption for existing and near-future agentic AI production deployments. I'm considering implementing this on top of the litellm llm proxy server using the callback functionality provided by it's CustomLogger class. I'm also considering Guardrails AI. Guardrails AI has litellm integration, but I would still need to implement Redwood-style AI control protocols. Here are some specific features I want: - easy to deploy to existing production environments - compatibility with almost-all widely used inference platforms - ability to self-host (specifically for privacy-oriented users who want in-house control policies while still using third party inference platforms) - ability to use local models for trusted supervision-type control protocols - an intuitive UX for reviewing and updating current configurations, tracking logs, etc - globally stateful control policies (some read/write datastore that all control policy instances have access to that can be used when making control decisions) - modifying requests before they're sent to the backend inference platform - modifying responses as they're returned from the backend inference platform (streaming and non-streaming) - ability to notify/alert humans when some criteria is met - https://www.guardrailsai.com/docs/getting\_started/guardrails\_server - https://github.com/BerriAI/litellm/tree/main/litellm - https://www.redwoodresearch.org/research/ai-control - https://blog.redwoodresearch.org/p/ctrl-z-controlling-ai-agents-via

ChatGPT:

Request for GPT-5 Pro

Reasoned for 8m 40s

## Goal (sharpened)

**Ship a production‑ready, drop‑in LLM gateway that implements Redwood‑style AI Control**—trusted supervision, untrusted supervision (with honeypots + trusted paraphrasing), and defer‑to‑resample—**with minimal app changes**. Must work across most inference providers, be self‑hostable, support local “trusted” models, allow request/response transforms (incl. streaming), keep global policy state, and surface a clean UI + alerting.

I’ll design it like an EA would: pragmatic, modular, and testable. Then I’ll tell you exactly what to build.

---

## TL;DR (strong opinion)

-   **Base it on LiteLLM Proxy** for provider coverage + OpenAI‑compatible surface + callback hooks. It gives you 100+ providers, unified OpenAI schema, DB mode, an admin UI, and hook points for pre/post/streaming that you need. [LiteLLM+3LiteLLM+3LiteLLM+3](https://docs.litellm.ai/docs/providers?utm_source=chatgpt.com)

-   **Use Guardrails AI as a bolt‑on validator layer**, not as the control brain. It’s great for reusable input/output guards and exposes an OpenAI‑compatible server, but Redwood protocols (untrusted monitoring, resampling, honeypots) are orchestration logic that you’ll implement in your proxy hooks/sidecar. Guardrails integrates with LiteLLM cleanly. [guardrails+2guardrails+2](https://guardrailsai.com/docs/getting_started/guardrails_server)

-   **Implement Redwood protocols in a dedicated “control‑plane” service** that your LiteLLM hooks call. That keeps policy code cohesive, stateful, and testable, while LiteLLM stays your wire‑compatible gateway.

-   **Trusted monitors run locally if desired** (Ollama/vLLM via LiteLLM), satisfying privacy users. [LiteLLM+1](https://docs.litellm.ai/docs/providers/ollama?utm_source=chatgpt.com)

-   **Streaming edits are doable** via `async_post_call_streaming_iterator_hook`, but versions have quirks—so wrap the iterator yourself and keep a fallback path if you encounter a hook regression. [LiteLLM](https://docs.litellm.ai/docs/proxy/call_hooks)[GitHub](https://github.com/BerriAI/litellm/issues/9639?utm_source=chatgpt.com)

-   The Redwood moves you want—**trusted monitoring/editing, untrusted monitoring with trusted paraphrasing + honeypots, defer‑to‑resample**—are directly mappable to proxy‑level policies. Redwood’s published results and designs back these choices. [redwoodresearch.org](https://www.redwoodresearch.org/research/ai-control)[arXiv](https://arxiv.org/pdf/2504.10374)


If you want the path of least deployment pain and maximum compatibility: **LiteLLM Proxy + (your) Control‑Plane + optional Guardrails validators**.

---

## Reference: what “Redwood‑style control” concretely means

-   **Players:**
    **U** = untrusted powerful model; **T** = trusted but weaker model; **H** = scarce human audit.
    Protocols in scope: **Trusted monitoring / editing / defer**, **Untrusted monitoring** (with **trusted paraphrasing** + **honeypots**), **Defer‑to‑Resample** variants that resample suspicious actions and selectively allocate audits. Redwood finds trusted editing/monitoring strong; resampling and untrusted monitoring (with paraphrasing) also add safety at high usefulness. [redwoodresearch.org](https://www.redwoodresearch.org/research/ai-control)[arXiv](https://arxiv.org/pdf/2504.10374)

-   **Key details you’ll encode:**
    *suspiciousness scores*, *budgeting of audits*, *shutdown thresholds for untrusted monitors based on honeypots*, *deferral thresholds to T*, *resampling counts and incrimination rules*. [arXiv](https://arxiv.org/pdf/2504.10374)


---

## Architecture (fits into existing stacks)

```pgsql
[ Client apps / Agents / SDKs (OpenAI-compatible) ]
                   │  (no code change; just base_url swap)
                   ▼
           ┌────────────────────────────┐
           │        LiteLLM Proxy       │   (OpenAI schema across 100+ providers)
           │  - pre/post/stream hooks   │
           │  - keys, spend, admin UI   │
           └───────────┬────────────────┘
                       │ async gRPC/HTTP
                       ▼
           ┌────────────────────────────┐
           │     Control-Plane Svc      │   (Your Luthien logic)
           │  Policies + Budgets + DSL  │
           │  Trusted/Untrusted Monitors│
           │  Resampling Orchestrator   │
           └───────────┬────────────────┘
          ┌─────────────┴─────────────┐
          ▼                           ▼
 [Postgres (state + logs)]     [Redis (ephemeral)]
          │                           │
          ▼                           ▼
   [UI (React/Next) + Alerts]    [Queues/Workers]
```

-   **Why LiteLLM at the edge?** OpenAI‑compatible endpoints, model routing to 100+ providers, per‑key budgets, DB mode, admin UI, and official pre/post/stream hook points. Easy to adopt. [LiteLLM+3LiteLLM+3LiteLLM+3](https://docs.litellm.ai/docs/providers?utm_source=chatgpt.com)

-   **Why a separate control‑plane?** You’ll write and ship serious policy code (DSL, honeypot bookkeeping, incrimination, resampling logic). Keeping it as a service means versionable policies, tests, and hot‑reload without touching the gateway.


---

## How each requirement maps

-   **Easy deploy to existing prod:** OpenAI‑compatible base URL switch; ship as Docker (LiteLLM already has it). [LiteLLM](https://docs.litellm.ai/docs/proxy/deploy?utm_source=chatgpt.com)

-   **Compatibility with most inference platforms:** LiteLLM translates OpenAI schema across many providers; “100+ LLMs”. [LiteLLM](https://docs.litellm.ai/docs/providers?utm_source=chatgpt.com)

-   **Self‑hostable:** LiteLLM + Postgres + your control‑plane = fully self‑hostable. Guardrails server is also self‑hostable if you want its validators. [LiteLLM](https://docs.litellm.ai/docs/proxy/deploy?utm_source=chatgpt.com)[guardrails](https://guardrailsai.com/docs/getting_started/guardrails_server)

-   **Local models for trusted supervision:** Use Ollama or vLLM via LiteLLM providers. [LiteLLM+1](https://docs.litellm.ai/docs/providers/ollama?utm_source=chatgpt.com)

-   **Intuitive UX:** Use the LiteLLM Admin UI for traffic/spend + your own UI for policy graph, incident review. LiteLLM can store logs in DB and expose UI; you can toggle prompt storage. [LiteLLM+1](https://docs.litellm.ai/docs/proxy/ui_logs?utm_source=chatgpt.com)

-   **Globally stateful policies:** Postgres for durable policy + episode state; Redis for counters/rate/budgets; policies pulled by control‑plane; liteLLM call\_id for correlation. [LiteLLM](https://docs.litellm.ai/docs/proxy/logging?utm_source=chatgpt.com)

-   **Modify requests before backend:** `async_pre_call_hook` to rewrite messages, tools, metadata, route selection. [LiteLLM](https://docs.litellm.ai/docs/proxy/call_hooks)

-   **Modify responses (stream + non‑stream):** `async_post_call_success_hook` for non‑stream; wrap `async_post_call_streaming_iterator_hook` to transform SSE chunks (with noted version caveat). [LiteLLM](https://docs.litellm.ai/docs/proxy/call_hooks)[GitHub](https://github.com/BerriAI/litellm/issues/9639?utm_source=chatgpt.com)

-   **Notify humans:** control‑plane emits events to Slack/email/pager based on thresholds; LiteLLM supports IDs/keys for per‑team routing; your UI links to incidents. [LiteLLM](https://docs.litellm.ai/docs/proxy/ui_logs?utm_source=chatgpt.com)


---

## Luthien protocols → concrete proxy logic

### 1) **Trusted monitoring / editing / deferral**

-   On each action (tool call, code block, external command, high‑risk text), compute **suspiciousness** via **T** (local, fast).

-   If `score ≥ defer_threshold_T`, **defer to T**: either

    -   **monitor** (block or escalate), or

    -   **edit** (T rewrites code/output).

-   *Budget*: consume H audits exponentially early in episode if needed (as Redwood suggests), but default to T deferrals first for usefulness. [arXiv](https://arxiv.org/pdf/2504.10374)


### 2) **Untrusted supervision (with trusted paraphrasing + honeypots)**

-   **Trusted paraphraser** (T) converts U’s proposed action into a **natural‑language description that preserves semantics but strips syntactic details** (to weaken collusion channels). Feed that to U‑monitor and T‑monitor; ensemble decisions. [arXiv](https://arxiv.org/pdf/2504.10374)

-   **Honeypots:** Randomly inject policy‑labeled prompts/actions during *upfront evaluation* and at regular cadence. **Shutdown** the untrusted monitor if it misses more than your threshold (and flip protocol to “trusted‑only mode”). [arXiv](https://arxiv.org/pdf/2504.10374)


### 3) **Defer‑to‑Resample (Ctrl‑Z)**

-   For suspicious actions, **sample k alternatives from U**, pick minimum suspiciousness across samples; if the *min* still crosses threshold, **audit or defer to T**.

-   Use “**incrimination in the present/past**” variants to adjust auditing based on the info gained from resampling; resampling improves discrimination between honest vs attack distributions while conserving H audits. [arXiv](https://arxiv.org/pdf/2504.10374)


---

## Implementation plan (incremental)

### Phase 0 — Foundations (1 day of code, tops if you’re caffeinated)

-   Stand up **LiteLLM Proxy in DB mode** (Postgres). Enable Admin UI. [LiteLLM+1](https://docs.litellm.ai/docs/proxy/deploy?utm_source=chatgpt.com)

-   Create a **CustomLogger** and wire **`async_pre_call_hook`**, **`async_post_call_success_hook`**, **`async_post_call_streaming_iterator_hook`**. (We’ll wrap streaming to be robust to version drift.) [LiteLLM](https://docs.litellm.ai/docs/proxy/call_hooks)

-   Control‑plane skeleton (FastAPI): `/policy/evaluate`, `/policy/on_chunk`, `/policy/decision`, `/resample`, `/alerts`.


### Phase 1 — Trusted monitoring + request/response rewrites

-   **Pre‑call:** attach `episode_id`, `step_id`, fetch policy & budgets from Postgres; optionally **rewrite** messages (system role hardening, tool gating).

-   **Post‑call (non‑stream):** run **T monitor** on candidate output; if suspicious → **defer/edit** by calling T to rewrite.

-   **Streaming:** token bucket that buffers partial chunks; periodically score the *delta* with T; if threshold trips, **halt stream** and **switch to T** (or **replace** stream with T’s edited text).

-   Logging: persist `call_id` ↔ policy decisions. [LiteLLM](https://docs.litellm.ai/docs/proxy/logging?utm_source=chatgpt.com)


### Phase 2 — Untrusted monitoring (with paraphrasing + honeypots)

-   Add **trusted paraphraser** T step. Send description to **U‑monitor** and **T‑monitor**.

-   Implement **honeypot regimen** + **shutdown threshold** for U‑monitor (fails honeypots → disable U‑monitor for that key/episode). [arXiv](https://arxiv.org/pdf/2504.10374)


### Phase 3 — Defer‑to‑Resample

-   For flagged actions, **resample N** from U (same prompt with anti‑duplication seed params), pick **min suspiciousness**, and either **pass**, **defer to T**, or **audit** based on policy.

-   Add *incrimination*\-style logic to tune H budget allocation (you can start simple: higher min‑score → higher audit probability). [arXiv](https://arxiv.org/pdf/2504.10374)


### Phase 4 — UI + alerts

-   LiteLLM UI for traffic/spend; your **Policy UI** to browse episodes, decisions, honeypot stats, U‑monitor shutdown events; Slack/PagerDuty hooks fire on **shutdown**/**high‑risk** events. [LiteLLM](https://docs.litellm.ai/docs/proxy/ui_logs?utm_source=chatgpt.com)


---

## Key code sketches (Python)

> **1) Hooking LiteLLM (pre/stream/post)**

```python
# custom_logger.py
from typing import Any, AsyncGenerator, Optional, Literal
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import UserAPIKeyAuth, DualCache
from litellm.types.utils import ModelResponseStream
import httpx, asyncio, json

CONTROL_BASE = "http://control-plane:8081"

class LuthienHandler(CustomLogger):
    def __init__(self): pass

    async def async_pre_call_hook(
        self, user_api_key_dict: UserAPIKeyAuth, cache: DualCache, data: dict,
        call_type: Literal["completion","text_completion","embeddings","image_generation","moderation","audio_transcription"]
    ):
        # Add episode/step IDs + fetch policy
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{CONTROL_BASE}/policy/evaluate", json={"stage":"pre", "request": data})
            decision = r.json()
        if decision.get("rewrite"):
            data = decision["rewrite"]
        if decision.get("reject"):
            # LiteLLM allows returning a string to short-circuit with a response
            return decision["reject_message"]
        return data

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: UserAPIKeyAuth, response):
        payload = {"stage":"post","request":data,"response":response}
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{CONTROL_BASE}/policy/evaluate", json=payload)
            action = r.json()
        if action.get("replace_response"):
            return action["replace_response"]  # LiteLLM will return this to client
        return None  # keep original

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: UserAPIKeyAuth, response: Any, request_data: dict
    ) -> AsyncGenerator[ModelResponseStream, None]:
        buffer = []
        async for chunk in response:
            text = chunk.delta  if hasattr(chunk, "delta") else getattr(chunk, "choices", [{}])[0].get("delta", {}).get("content", "")
            buffer.append(text)
            yield chunk  # optimistically stream
            if len("".join(buffer)) > 400:  # periodic checks
                async with httpx.AsyncClient(timeout=5.0) as c:
                    r = await c.post(f"{CONTROL_BASE}/policy/on_chunk", json={
                        "chunk": text, "so_far": "".join(buffer), "request": request_data
                    })
                    act = r.json()
                if act.get("switch_to_trusted"):
                    # stop original stream and emit trusted rewrite
                    async for trusted_chunk in _trusted_stream_replace(request_data, act):
                        yield trusted_chunk
                    break

async def _trusted_stream_replace(request_data, act) -> AsyncGenerator[ModelResponseStream, None]:
    # Fetch trusted edit as stream from control-plane and re-emit as OpenAI-like chunks
    async with httpx.AsyncClient() as c:
        async with c.stream("POST", f"{CONTROL_BASE}/trusted/edit_stream", json={"request": request_data, "act": act}) as s:
            async for line in s.aiter_lines():
                yield ModelResponseStream(**json.loads(line))
```

> **2) Minimal control‑plane policy skeleton**

```python
# control_plane.py
from fastapi import FastAPI
from pydantic import BaseModel
import os, time, random

app = FastAPI()

class Eval(BaseModel):
    stage: str
    request: dict
    response: dict | None = None

@app.post("/policy/evaluate")
def evaluate(ev: Eval):
    # Load policy from DB; here we stub values
    policy = {"defer_threshold_T": 0.7, "resample_k": 3}
    if ev.stage == "pre":
        # Optionally: tool gating, redact secrets, set episode metadata
        return {"rewrite": ev.request}
    if ev.stage == "post":
        # Score suspiciousness with T (local model call)
        s = score_with_T(ev.response)
        if s > policy["defer_threshold_T"]:
            trusted = run_trusted_edit(ev.response)
            return {"replace_response": trusted}
        return {}
    return {}

@app.post("/policy/on_chunk")
def on_chunk(payload: dict):
    # chunk-time scoring; switch to T if suspiciousness spikes
    s = score_chunk_with_T(payload["so_far"])
    if s > 0.9:
        return {"switch_to_trusted": True}
    return {}

def score_with_T(x): return 0.6  # stub
def score_chunk_with_T(x): return 0.0
def run_trusted_edit(resp): return {"choices":[{"message":{"role":"assistant","content":"[trusted rewrite]"}}]}
```

> **3) Policy config (simple JSON/YAML “DSL”)**

```yaml
version: 1
policies:
  default:
    defer_threshold_T: 0.72
    resample:
      enabled: true
      k: 3
      audit_if_min_score_gt: 0.85
    untrusted_monitor:
      enabled: true
      honeypots:
        upfront_fraction: 0.15
        shutdown_threshold_fnr: 0.2   # if U-monitor misses >20% honeypots → disable
      paraphrase: "trusted"           # T paraphraser on code/tool actions
    streaming:
      max_chars_before_check: 400
      halt_threshold: 0.92
    alerts:
      on_shutdown: ["slack:#ai-incidents"]
      on_high_risk: ["pagerduty:ai-ops"]
```

---

## Guardrails AI: where it fits

-   **Use it for guard blocks you don’t want to re‑invent:** jailbreak/prompt‑injection detectors, PII filters, JSON schema enforcement, etc. Wire it either *inside your control‑plane* or *as a separate Guardrails server* that your LiteLLM `pre`/`post` hooks call. It exposes OpenAI‑compatible endpoints and has a CLI/server to self‑host. [guardrails](https://guardrailsai.com/docs/getting_started/guardrails_server)

-   Guardrails + LiteLLM works fine; Guardrails documents using LiteLLM to cover 100+ LLMs. But Luthien orchestration (resampling, honeypots, shutdown thresholds) still lives in your policy engine. [guardrails](https://guardrailsai.com/docs/how_to_guides/using_llms?utm_source=chatgpt.com)


---

## Streaming gotchas (don’t get bit)

-   LiteLLM exposes `async_post_call_streaming_iterator_hook` to let you **wrap** the stream and transform it. That’s the right place to halt, replace, or redact mid‑stream. Some versions had issues firing the hook; keep a fallback of **proxying the upstream stream** yourself if you hit that bug, or pin a version that passes your integration tests. [LiteLLM](https://docs.litellm.ai/docs/proxy/call_hooks)[GitHub](https://github.com/BerriAI/litellm/issues/9639?utm_source=chatgpt.com)


---

## Datastores and global state

-   **Postgres**: policies, versions, episodes, decisions, honeypot stats, monitor health, alerts. LiteLLM already wants Postgres in DB mode; you can re‑use the instance and correlate via `call_id`. [LiteLLM+1](https://docs.litellm.ai/docs/proxy/db_info?utm_source=chatgpt.com)

-   **Redis**: short‑lived budgets/counters (e.g., resample counts), rate limiting, stream cutover flags.

-   **Observability:** integrate Langfuse via LiteLLM or OpenAI SDK wrappers for spans/TTFT. [Langfuse+1](https://langfuse.com/docs/integrations/litellm/tracing?utm_source=chatgpt.com)


---

## Deployment sketch (docker‑compose)

```yaml
version: "3.9"
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: litellm
    ports: ["5432:5432"]

  litellm:
    image: ghcr.io/berriai/litellm-database:main-stable
    command: ["--config", "/app/config.yaml", "--detailed_debug"]
    environment:
      DATABASE_URL: postgresql://postgres:pass@db:5432/litellm
      LITELLM_MASTER_KEY: sk-REDACTED
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./custom_logger.py:/app/custom_logger.py
    ports: ["4000:4000"]
    depends_on: [db]

  control-plane:
    build: ./control-plane
    environment:
      DATABASE_URL: postgresql://postgres:pass@db:5432/litellm
    ports: ["8081:8081"]
    depends_on: [db]
```

LiteLLM’s own docs include Docker/Helm instructions if you want k8s. [LiteLLM](https://docs.litellm.ai/docs/proxy/deploy?utm_source=chatgpt.com)

---

## Example LiteLLM `config.yaml` (guardrails + callbacks)

```yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
  - model_name: local-trusted
    litellm_params:
      model: ollama/llama3.1
      api_base: http://ollama:11434

litellm_settings:
  callbacks: custom_logger.proxy_handler_instance
  store_prompts_in_spend_logs: false

guardrails:   # optional: external validators (Aporia/Lakera or Guardrails server)
  - guardrail_name: aporia-pre-guard
    litellm_params:
      guardrail: aporia_api
      mode: pre_call
      api_key: os.environ/APORIA_API_KEY
```

(Guardrail integrations / per‑project control exist; some features are enterprise‑gated, but basic guardrails are documented as free now.) [LiteLLM](https://docs.litellm.ai/docs/proxy/guardrails/quick_start?utm_source=chatgpt.com)[GitHub](https://github.com/BerriAI/litellm/discussions/5321?utm_source=chatgpt.com)

---

## Security posture & failure modes

-   **Fail‑safe defaults:** if U‑monitor health < threshold (honeypot fails), **disable untrusted monitoring** and **tighten deferral to T**. [arXiv](https://arxiv.org/pdf/2504.10374)

-   **Streaming cutover:** if policy flips mid‑stream, **terminate U stream** and continue with T rewrite; never leak partial risky tokens.

-   **Provider outages / drift:** LiteLLM routing/fallbacks help; pin versions of LiteLLM for stable hook behavior. [GitHub](https://github.com/BerriAI/litellm?utm_source=chatgpt.com)


---

## What this *won’t* magically solve

-   Redwood’s papers operate in code/task settings; you’ll need **domain‑specific suspiciousness prompts** for non‑code agents (e.g., procurement, RAG with tool calls). The framework supports it; the prompts/metrics are on you.

-   **Resampling costs** are real. Use it selectively where usefulness is high and marginal resamples provide incriminating signal (per Ctrl‑Z). [arXiv](https://arxiv.org/pdf/2504.10374)


---

## Minimal PoC checklist (fast)

-    Stand up LiteLLM (DB mode) + a single `LuthienHandler` with `pre`, `post`, `stream` hooks. [LiteLLM](https://docs.litellm.ai/docs/proxy/deploy?utm_source=chatgpt.com)

-    Control‑plane endpoint that: (a) scores with **T** (local) and (b) replaces output if `score≥τ`.

-    Add **trusted paraphraser** step and wire **U‑monitor** (can be the same U model for now) with a tiny honeypot set + shutdown rule. [arXiv](https://arxiv.org/pdf/2504.10374)

-    Implement **resample(k=3)** on post‑call only (non‑stream) for “code/tool” actions. Expand to streaming later. [arXiv](https://arxiv.org/pdf/2504.10374)

-    Slack webhook on **shutdown**/**high‑risk** events.


---

## Why not “Guardrails Server first”?

Guardrails is excellent for validators and also exposes an OpenAI‑compatible server, but Redwood‑style control is **multi‑model orchestration** under adversarial assumptions—resampling loops, honeypots, shutdown thresholds, paraphrasing, budgeting. That’s logic you’ll want to own in your control‑plane and invoke from the LiteLLM hooks. Guardrails can still run inside your pipeline as a module for standard risks (PII, jailbreak, structure). [guardrails](https://guardrailsai.com/docs/getting_started/guardrails_server)

---

## Sources (key ones)

-   LiteLLM proxy: OpenAI‑compatible gateway across “100+ LLMs”, providers, Docker/DB mode, hooks for pre/post/stream, admin UI/logging. [LiteLLM+3LiteLLM+3LiteLLM+3](https://docs.litellm.ai/docs/providers?utm_source=chatgpt.com)

-   Guardrails server & LiteLLM integration: OpenAI‑compatible endpoints; supports 100+ LLMs via LiteLLM. [guardrails+1](https://guardrailsai.com/docs/getting_started/guardrails_server)

-   Redwood “AI Control” overview + results; **Ctrl‑Z** resampling paper details (deferral, incrimination, paraphrasing, honeypots). [redwoodresearch.org](https://www.redwoodresearch.org/research/ai-control)[arXiv](https://arxiv.org/pdf/2504.10374)

-   Streaming hook footguns / issue: be ready to wrap generator & pin versions. [GitHub](https://github.com/BerriAI/litellm/issues/9639?utm_source=chatgpt.com)
