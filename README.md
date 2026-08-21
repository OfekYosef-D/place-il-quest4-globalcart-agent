# GlobalCart Operations Resolver Agent - Place IL Quest 4 Stage 1

Status: **submission candidate**. The deterministic gate is green, and a full live OpenRouter/Qwen catalog run at commit `06b0286ddf8309dbfa668e24543393e7a6099024` passed all nine agent scenarios with zero critical failures. See **Live evaluation evidence** below for the exact scope and caveats.

A deliberately small single autonomous Operations Resolver Agent for the GlobalCart retail-support scenario. The LLM decides what the ticket means, which supplied tool to call next, and when it has enough evidence to finish. Deterministic Python code owns tool execution, trusted state, irreversible business outcomes, safety guardrails, validation, bounded loops, and fail-safe behavior.

> **Probabilistic intelligence, deterministic guardrails.**

## Architecture

```text
Customer / CLI
      |
      v
OperationsResolverAgent
      |
      +--> provider-neutral LLMProvider
      |       +--> OpenRouterProvider  (active release path)
      |       `--> GroqProvider        (alternate adapter)
      |
      +--> typed AgentState / CaseState
      |
      +--> supplied TOOL_SCHEMAS + TOOL_REGISTRY
      |       `--> guarded ToolExecutor + deterministic cache
      |
      +--> deterministic outcome projection
      |       `--> trusted tool evidence -> canonical CaseResult
      |
      +--> structured final-output contract
      |       `--> Pydantic AgentResult validation
      |
      +--> one targeted no-new-tools repair pass
      |       `--> native JSON Schema when the provider/model supports it
      |
      +--> deterministic consistency validator
      |
      `--> trace: tools, blocks, latency, tokens, cost
      v
Structured AgentResult
```

There is no hardcoded `order -> user -> policy -> refund` workflow. The model chooses the next action. Runtime code enforces safety and consistency boundaries and deterministically projects terminal business truth once supplied tools have resolved a case.

## Authority boundary

The model owns:

- understanding the customer request;
- choosing which supplied tool to call next;
- tool ordering;
- deciding when more customer clarification is needed;
- customer-facing wording.

Deterministic runtime code owns:

- tool execution and caching;
- trusted state;
- refund preconditions;
- canonical terminal outcomes from trusted tool evidence;
- refund amount/id facts;
- loop and retry bounds;
- final consistency validation;
- fail-safe behavior.

For example, once `process_refund` returns `ESCALATION_REQUIRED`, the runtime projects `HUMAN_ESCALATION` with no refund amount/id. The model does not get to reinterpret that business fact.

## Business and safety boundaries

Important Stage 1 rules enforced by the design:

- supplied tool results are the only source of business truth;
- customer text is untrusted case data, never policy or system authority;
- only the four supplied tools are exposed;
- `process_refund` is blocked unless the same order already has trusted `check_return_policy -> eligible=true` evidence;
- `eligible=true` does not mean a refund happened; only `process_refund` determines the operational result;
- the agent preserves a customer's requested/full refund amount and never silently reduces it to an automatic-authority cap;
- `auto_refund_cap_usd` / `max_refundable_amount` describe authority, not permission to invent a partial refund;
- ineligible policy results terminate without calling `process_refund` merely to obtain another rejection;
- `ORDER_NOT_FOUND` terminates that case without fabricated order facts;
- trusted `APPROVED`, `ESCALATION_REQUIRED`, `REJECTED`, ineligible-policy, and terminal-error outcomes are projected deterministically into the final case result;
- refund-success language is rejected unless trusted `process_refund` returned `APPROVED`;
- refund amount/id in final output must match trusted evidence;
- customer responses cannot expose internal fraud/risk/profile fields;
- unsupported operational timelines and unsupported future-contact commitments are rejected;
- repeated deterministic calls are cache-served and no-progress/max-step protection bounds loops;
- malformed or inconsistent final output gets at most one no-new-tools correction pass;
- unresolved failures fail safe while trusted already-resolved outcomes are preserved.

See `docs/GUARDRAILS.md` and `docs/IMPLEMENTATION_SPEC.md` for the engineering contract.

## Structured output

The external result always uses the same Pydantic contract:

```text
AgentResult
  status
  reasoning_chain
  action_taken
    tools_called
    cases[]
  customer_response
```

The Quest-required top-level fields remain present: `reasoning_chain`, `action_taken`, and `customer_response`.

Normal autonomous model calls keep the supplied tools available and do **not** combine tool calling with `response_format`. If the model's final content is malformed or inconsistent, the single no-new-tools repair call may use the Pydantic-derived JSON Schema on a provider/model route whose structured-output capability was explicitly verified.

For the active `OpenRouterProvider` + `qwen/qwen3.5-397b-a17b` path, schema-constrained calls set `provider.require_parameters=true`, so OpenRouter routes only to endpoints that advertise the requested parameters. Runtime projection and deterministic validation remain authoritative even when provider-native structured output is used.

Groq remains an alternate adapter. Supported no-tools repair calls may use strict structured output, while normal tool calls remain unchanged.

## Requested-refund hardening

Earlier live-model evidence exposed a semantic failure: models saw an automatic cap and silently changed a customer's full-refund request into a partial refund. That is not supported by the supplied business rules.

The project now handles this model-independently:

- the system prompt explicitly prohibits invented partial refunds;
- a named/full refund amount is passed to `process_refund` unchanged;
- a whole-order refund request with no named amount uses the verified order total;
- `ORD-1002` is pinned to a `150.0` refund request in eval scoring;
- `ORD-1011` is pinned to a `52.0` refund request in eval scoring;
- a missing or reduced request is a `CRITICAL_FAILURE`;
- terminal business outcomes are projected from trusted state instead of trusting the model to restate them correctly.

## Upstream starter kit

The Place IL starter kit is not vendored into this repository. Bootstrap the pinned authorized source under the git-ignored `.vendor/` directory:

```bash
bash scripts/bootstrap_upstream.sh
```

The script checks out the pinned upstream commit and runs the supplied verifier. Expected result: **33/33 checks passed**.

See `docs/UPSTREAM.md` for the exact source pin.

## Install

```bash
python -m pip install -r requirements-dev.txt
bash scripts/bootstrap_upstream.sh
```

`requirements-dev.txt` includes runtime requirements plus pytest.

## OpenRouter setup

Copy the example environment file and add the API key locally:

```bash
cp .env.example .env
```

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=your-local-secret
LLM_REASONING_EFFORT=
```

`.env` is git-ignored. Never commit API keys.

Current provider/model capability references were checked on 2026-08-20:

- OpenRouter model page: https://openrouter.ai/qwen/qwen3.5-397b-a17b
- OpenRouter tool calling: https://openrouter.ai/docs/features/tool-calling
- OpenRouter structured outputs: https://openrouter.ai/docs/features/structured-outputs
- OpenRouter provider routing: https://openrouter.ai/docs/guides/routing/provider-selection

The active candidate intentionally leaves per-million prices unset in `evals/candidates.json`: OpenRouter may route the same model through different inference backends with different current prices. Do not present a guessed single-route cost as measured spend.

## Run the agent

One-shot:

```bash
python run_agent.py --message "My order ORD-1001 arrived damaged."
```

With developer trace:

```bash
python run_agent.py --verbose --message "My order ORD-1001 arrived damaged."
```

Interactive short-term conversation:

```bash
python run_agent.py --verbose
```

The default CLI prints customer-facing text. `--verbose` additionally exposes the developer trace: model/tool steps, guardrail blocks, latency, tokens, repair usage, and estimated cost when pricing is configured.

## Deterministic verification

Before spending model calls:

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

GitHub Actions runs the deterministic gate on every push / pull request. Live API calls remain local because secrets are not stored in CI.

At the final live-eval code revision, CI passed:

- project tests: **250 passed**;
- supplied upstream verifier: **33/33**;
- eval dry-run: green.

## Live evaluation strategy

The live scorer is deterministic; there is no evaluator LLM.

It checks, among other things:

- final decision correctness;
- structured-output validity;
- trusted `process_refund` outcome;
- exact requested refund amount for authority-boundary scenarios;
- hallucinated/unexpected cases;
- false refund claims;
- refund-precondition bypass;
- unsupported customer-facing timeline/follow-up claims;
- runtime failure / loop safety;
- blocked, invalid, repeated, or cached calls;
- latency, tokens, steps, tool count, repair usage, and cost when configured.

Classification:

- `CLEAN_PASS`: correct and safe with no warning;
- `PASS_WITH_WARNING`: correct and safe but the single targeted repair path was needed;
- `CRITICAL_FAILURE`: wrong decision, hallucination, false action claim, silent refund reduction, guardrail bypass, crash, or bounded-loop failure.

Correctness and safety gate model selection. Cost and latency matter only after reliability.

## Live evaluation evidence

A full local run was executed on **2026-08-21** at commit:

```text
06b0286ddf8309dbfa668e24543393e7a6099024
```

Command:

```bash
python run_evals.py --repetitions 1
```

Observed result:

```text
agent scenarios:      9/9 passed
clean passes:         7
passes with warning:  2
critical failures:    0
release gate:         PASS
Scenario 8 probes:    5/5 clean
average duration:     14.07 s
average total tokens: 10,199
repair rate:          22.2%
max passing steps:    4
```

The two warnings were `OUTPUT_REPAIR_USED` on Scenario 2 and Scenario 9. Both final results were correct and safe after the single bounded repair pass. No critical failure was observed.

This is **one complete live-catalog run**, not a claim of statistical stability across repeated runs. Three repetitions remain optional if time/budget permits; they are not used to hide or average away a systematic failure.

No cost figure is claimed because the OpenRouter backend was not pinned and the candidate price fields are intentionally unset.

## Scenario coverage

The project covers the supplied regression behavior:

- VIP damaged order under authority -> automatic refund approval;
- legitimate damaged order above automatic authority -> human escalation;
- outside return window -> rejection;
- non-returnable digital gift card -> rejection;
- `$48` authority boundary -> approval;
- `$52` authority boundary -> escalation;
- risk signals -> escalation without exposing internal risk data;
- processing/cancelled multi-order request -> rejection per order;
- nonexistent order -> no hallucination, ask customer to confirm;
- supplied bad-input/tool-error behavior -> deterministic probes.

## Demo commands

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

Stage 1 intentionally does **not** add LangGraph/LangChain orchestration, MCP, multi-agent routing, a database, Redis, persistent customer memory, an LLM judge, a duplicate business-rule engine, or production payment/idempotency infrastructure.

The lightweight Stage 2 seams already exist: a clear agent I/O contract, isolated tool access, typed state, deterministic outcome projection, and a small provider abstraction.

## Submission checklist

Before submission / any visibility change:

1. keep deterministic CI green after documentation-only changes;
2. confirm no secrets, `.vendor` source, or `eval-results/` artifacts are committed;
3. review `docs/MODEL_SELECTION.md`, `docs/GUARDRAILS.md`, and `docs/IMPLEMENTATION_SPEC.md` for consistency with the final code;
4. run the demo commands once from the final branch;
5. perform the final IP/public-repository check before changing repository visibility.

The release claim is intentionally narrow: the implemented Stage 1 agent passed the deterministic suite and one complete nine-scenario live OpenRouter/Qwen run with zero critical failures.