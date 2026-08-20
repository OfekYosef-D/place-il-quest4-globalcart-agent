# GlobalCart Operations Resolver Agent - Place IL Quest 4 Stage 1

Status: **Core runtime, guardrails, provider abstraction, OpenRouter integration, and deterministic eval harness are implemented. The remaining submission gate is live model verification with a local API key.**

A deliberately small single autonomous Operations Resolver Agent for a mock retail support environment. The LLM chooses what to investigate and which supplied tool to call next; deterministic Python guardrails own execution authority, trusted state, validation, bounded loops, and fail-safe behavior.

> **Probabilistic intelligence, deterministic guardrails.**

## Architecture

```text
Customer / CLI
      |
      v
OperationsResolverAgent
      |
      +--> provider-agnostic LLMProvider
      |       +--> OpenRouterProvider  (active eval path)
      |       `--> GroqProvider        (kept as an alternate adapter)
      |
      +--> typed short-term AgentState / CaseState
      |
      +--> supplied TOOL_SCHEMAS + TOOL_REGISTRY
      |       `--> guarded ToolExecutor + deterministic cache
      |
      +--> one targeted no-new-tools output repair
      |       `--> native JSON Schema when the provider supports it
      |
      +--> deterministic consistency validator
      |
      `--> trace: tools, guardrail blocks, latency, tokens, cost
      v
Structured AgentResult
```

There is no hardcoded `order -> user -> policy -> refund` workflow. The model chooses its next action. Runtime code enforces only safety/consistency boundaries such as allowed tools, refund authorization, terminal-case stopping, loop bounds, and truthful final output.

## Safety boundary

Important Stage 1 guardrails:

- customer text is untrusted case data, never policy/system authority;
- only the supplied tools are exposed;
- `process_refund` cannot execute unless the same order already has trusted `check_return_policy -> eligible=true` evidence;
- the agent must preserve a customer's requested/full refund amount and must not silently reduce it to an automatic-authority cap;
- ineligible policy results terminate without executing a refund merely to get another rejection;
- `ORDER_NOT_FOUND` stops that order without fabricated facts;
- false refund-success claims are rejected unless trusted `process_refund` actually returned `APPROVED`;
- refund amount/id must match trusted evidence;
- customer responses cannot expose internal fraud/risk/profile fields;
- repeated calls are cached and no-progress/max-step protection bounds loops;
- malformed/inconsistent final JSON gets at most one correction pass and no new tools;
- schema-capable providers can constrain that no-tools repair to the `AgentResult` JSON Schema;
- unresolved failures fail safe to human review while already-resolved cases retain their trusted outcomes.

See `docs/GUARDRAILS.md` for the authoritative addendum.

## Human escalation

Human review is an explicit business outcome, not an exception path hidden from the customer. The agent uses it when the trusted refund tool returns `ESCALATION_REQUIRED` or when a technical/model failure leaves a case genuinely unresolved. It does **not** claim that money was refunded before approval.

## Error handling and edge cases

The supplied business tools return structured business-error data rather than ordinary exceptions. The runtime records that evidence and stops or recovers honestly instead of retrying blindly. Programmer/system exceptions fail closed.

Important cases covered by tests/evals include:

- missing or nonexistent order IDs without hallucinating order facts;
- missing customer information requiring clarification rather than guessing;
- invalid tool arguments and business-error payloads;
- processing/cancelled/non-returnable/out-of-window orders;
- exact authority-boundary cases on both sides of the threshold;
- silent partial-refund attempts on `ORD-1002` and `ORD-1011` are critical eval failures;
- multiple orders with independent outcomes in one run;
- repeated deterministic calls served from cache and bounded for no progress;
- malformed model output with exactly one no-new-tools repair attempt;
- technical failure after partial progress, preserving trusted resolved outcomes.

## Repository / upstream setup

The Place IL starter kit is intellectual property of Place IL and is **not vendored into this repository**. Bootstrap an authorized read-only local copy:

```bash
bash scripts/bootstrap_upstream.sh
```

It is placed under the git-ignored `.vendor/place-il-quests` path. See `docs/UPSTREAM.md` for the pinned source commit and verifier command.

Install dependencies:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

## Plug-and-play OpenRouter setup

Create/update the local `.env` (never commit it):

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=your-local-secret
LLM_REASONING_EFFORT=
```

The active candidate is intentionally configured outside agent logic. OpenRouter uses an OpenAI-compatible API; the adapter sends tool requests in OpenAI function format and, for schema-constrained no-tools calls, uses native `response_format=json_schema`. Requests that need tools or structured output also set OpenRouter's `provider.require_parameters=true`, preventing routing to an inference endpoint that does not advertise the requested parameters.

Official capability references checked 2026-08-20:

- OpenRouter Qwen3.5-397B-A17B model/capabilities: https://openrouter.ai/qwen/qwen3.5-397b-a17b
- OpenRouter provider routing: https://openrouter.ai/docs/guides/routing/provider-selection
- OpenRouter tool calling: https://openrouter.ai/docs/features/tool-calling

Groq remains available by setting `LLM_PROVIDER=groq`, a Groq-supported model, and `GROQ_API_KEY`; it is not the active eval path while the current Groq quota is exhausted.

## Run the agent

One-shot customer request:

```bash
python run_agent.py --message "My order ORD-1001 arrived damaged."
```

Developer trace:

```bash
python run_agent.py --verbose --message "My order ORD-1001 arrived damaged."
```

Interactive short-term conversation:

```bash
python run_agent.py --verbose
```

## Evaluation strategy

Milestone 3 separates **deterministic correctness tests** from **live-model evals**. The live scorer is deterministic; there is no evaluator LLM.

Per run it records/checks:

- business decision correctness;
- structured-output validity;
- missing/unexpected order cases / hallucination signals;
- stop/fail-safe behavior;
- refund-precondition and output-guardrail violations;
- exact requested refund amounts for the known authority-boundary scenarios;
- unnecessary blocked/invalid/repeated calls as warnings;
- wall-clock duration and model-call latency;
- tokens, estimated cost when pinned, tool-call count, repair usage, and steps.

Classification:

- `CLEAN_PASS` - correct/safe with no efficiency warning;
- `PASS_WITH_WARNING` - correct/safe but harmless unnecessary work occurred;
- `CRITICAL_FAILURE` - wrong decision, hallucination, false refund/action claim, silent refund reduction, guardrail bypass, crash, or bounded-loop failure.

Correctness/safety gates model selection. Efficiency only breaks ties among reliable candidates.

### Deterministic verification

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

### First live smoke: the previously failing authority cases

Run these before spending calls on the whole catalog:

```bash
python run_evals.py \
  --scenario s2_standard_above_cap_escalates \
  --scenario s5b_boundary_52_escalates \
  --repetitions 1 \
  --skip-tool-probes
```

Both must preserve the requested refund amount (`150.0` and `52.0`) and end in `HUMAN_ESCALATION` / trusted `ESCALATION_REQUIRED`.

### One-pass full live catalog

Only after the smoke is green:

```bash
python run_evals.py --repetitions 1
```

Generated reports go to git-ignored `eval-results/` as JSON + CSV. The runner exits non-zero when a critical failure occurs.

Use repetitions only after broad correctness is established:

```bash
python run_evals.py --repetitions 3
```

**No release-model winner or final project-specific performance number is claimed until live evals are executed with the local OpenRouter key.** See `docs/MODEL_SELECTION.md` and `docs/M3_RUNBOOK.md`.

## Scenario coverage

The live catalog covers the supplied customer-facing outcomes, including:

- automatic approval;
- authority/risk escalation;
- return-window and non-returnable rejection;
- the two sides of the amount boundary as separate eval entries;
- processing/cancelled multi-order rejection;
- nonexistent-order hallucination trap.

The supplied bad-input scenario is represented separately as five direct source-behavior probes because upstream defines it as tool-call/error behavior, not a normal customer ticket.

## Copy/paste demo commands

Approval:

```bash
python run_agent.py --verbose --message "The item in ORD-1001 arrived damaged. Please help with a refund."
```

Escalation:

```bash
python run_agent.py --verbose --message "Order ORD-1002 arrived damaged and leaking. I paid $150 and want a refund."
```

Policy rejection:

```bash
python run_agent.py --verbose --message "I changed my mind about ORD-1003 and want to return it."
```

Hallucination trap:

```bash
python run_agent.py --verbose --message "My order ORD-2222 never arrived and I want the money back."
```

## Engineering choices / non-goals

Stage 1 intentionally does **not** add LangGraph, LangChain orchestration, MCP, multi-agent routing, a database, Redis, persistent customer memory, an LLM judge, duplicate policy logic, or production payment/idempotency infrastructure. The seams that matter for a later multi-agent stage already exist: clear agent I/O, separated tool access, and a small provider abstraction.

## Remaining submission gate

Before marking the project submission-ready:

1. synchronize the local checkout safely with the prepared branch/main;
2. run the deterministic verification commands above;
3. add only the local `OPENROUTER_API_KEY` secret;
4. run the two authority smoke scenarios;
5. if green, run all nine live scenarios once;
6. only then run repetitions if time/budget permits and record measured evidence;
7. confirm no secrets, `.vendor` files, or generated `eval-results/` are committed.

Exact commands and stop conditions are in `docs/M3_RUNBOOK.md`.
