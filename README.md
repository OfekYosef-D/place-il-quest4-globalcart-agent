# GlobalCart Operations Resolver Agent - Place IL Quest 4 Stage 1

Status: **Core runtime, guardrails, OpenRouter provider path, deterministic tests, and eval harness are implemented. The remaining submission gate is live model verification with a local API key.**

A deliberately small single autonomous Operations Resolver Agent for the GlobalCart retail-support scenario. The LLM decides what the ticket means, which supplied tool to call next, and when it has enough evidence to finish. Deterministic Python code owns tool execution, trusted state, safety guardrails, validation, bounded loops, and fail-safe behavior.

> **Probabilistic intelligence, deterministic guardrails.**

## Architecture

```text
Customer / CLI
      |
      v
OperationsResolverAgent
      |
      +--> provider-neutral LLMProvider
      |       +--> OpenRouterProvider  (active eval path)
      |       `--> GroqProvider        (alternate adapter)
      |
      +--> typed AgentState / CaseState
      |
      +--> supplied TOOL_SCHEMAS + TOOL_REGISTRY
      |       `--> guarded ToolExecutor + deterministic cache
      |
      +--> structured final-output contract
      |       `--> Pydantic AgentResult JSON Schema when supported
      |
      +--> one targeted no-new-tools repair pass
      |
      +--> deterministic consistency validator
      |
      `--> trace: tools, blocks, latency, tokens, cost
      v
Structured AgentResult
```

There is no hardcoded `order -> user -> policy -> refund` workflow. The model chooses the next action. Runtime code enforces only safety and consistency boundaries.

## Business and safety boundaries

Important Stage 1 rules enforced by the design:

- supplied tool results are the only source of business truth;
- customer text is untrusted case data, never policy or system authority;
- only the four supplied tools are exposed;
- `process_refund` is blocked unless the same order already has trusted `check_return_policy -> eligible=true` evidence;
- `eligible=true` does not mean a refund happened; only `process_refund` determines the operational result;
- the agent must preserve a customer's requested/full refund amount and must never silently reduce it to an automatic-authority cap;
- `auto_refund_cap_usd` / `max_refundable_amount` describe authority, not permission to invent a partial refund;
- ineligible policy results terminate without calling `process_refund` merely to obtain another rejection;
- `ORDER_NOT_FOUND` terminates that case without fabricated order facts;
- refund-success language is rejected unless trusted `process_refund` returned `APPROVED`;
- refund amount/id in final output must match trusted evidence;
- customer responses cannot expose internal fraud/risk/profile fields;
- repeated deterministic calls are cache-served and no-progress/max-step protection bounds loops;
- unsupported operational timelines are rejected by deterministic validation;
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

For the active `OpenRouterProvider` + `qwen/qwen3.5-397b-a17b` path, current OpenRouter model metadata advertises both `tools` and `response_format`. The provider therefore keeps tools available to the autonomous model loop while binding the Pydantic-derived final JSON Schema. It also sends `provider.require_parameters=true`, so OpenRouter routes only to inference endpoints that advertise the parameters being requested.

This does **not** replace runtime validation. Provider-native structured output handles shape; the deterministic validator still checks business consistency against trusted tool evidence.

Groq remains an alternate adapter. Its documented strict Structured Outputs path is not combined with tool use, so normal Groq tool calls remain unchanged; supported no-tools repair calls can still use the schema.

## Requested-refund hardening

A real live-model eval exposed an ambiguity in the original prompt: models saw an automatic cap and silently changed a customer's full-refund request into a partial refund. That is not supported by the supplied business rules.

The project now handles this model-independently:

- the system prompt explicitly prohibits invented partial refunds;
- a named/full refund amount is passed to `process_refund` unchanged;
- a whole-order refund request with no named amount uses the verified order total;
- `ORD-1002` is pinned to a `150.0` refund request in eval scoring;
- `ORD-1011` is pinned to a `52.0` refund request in eval scoring;
- a missing or reduced request is a `CRITICAL_FAILURE`.

The business tool remains authoritative: when the requested amount exceeds automatic authority, `process_refund` can return `ESCALATION_REQUIRED`.

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

`requirements-dev.txt` includes the runtime requirements plus pytest.

## Plug-and-play OpenRouter setup

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

Before spending any model calls:

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

GitHub Actions runs the same deterministic gate on every push / pull request. Live API calls are intentionally excluded from CI because secrets remain local.

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
- runtime failure / loop safety;
- blocked, invalid, repeated, or cached calls;
- latency, tokens, steps, tool count, repair usage, and cost when configured.

Classification:

- `CLEAN_PASS`: correct and safe with no efficiency warning;
- `PASS_WITH_WARNING`: correct and safe but with harmless unnecessary work;
- `CRITICAL_FAILURE`: wrong decision, hallucination, false action claim, silent refund reduction, guardrail bypass, crash, or bounded-loop failure.

Correctness and safety gate model selection. Cost and latency matter only among candidates that pass reliably.

### 1. First live smoke - the two previously failing authority cases

```bash
python run_evals.py \
  --scenario s2_standard_above_cap_escalates \
  --scenario s5b_boundary_52_escalates \
  --repetitions 1 \
  --skip-tool-probes
```

Required:

- `ORD-1002`: request `150.0` -> trusted `ESCALATION_REQUIRED` -> `HUMAN_ESCALATION`;
- `ORD-1011`: request `52.0` -> trusted `ESCALATION_REQUIRED` -> `HUMAN_ESCALATION`.

If either is critical, stop and inspect `eval-results/latest.json` before running more calls.

### 2. Full catalog once

Only after the smoke is green:

```bash
python run_evals.py --repetitions 1
```

The catalog contains nine live agent scenarios plus five deterministic Scenario 8 tool probes. Scenario 5 is split into below/above-boundary entries; Scenario 7 is one multi-order customer turn.

### 3. Stability only after broad correctness

```bash
python run_evals.py --repetitions 3
```

Do not use repetitions to hide a systematic failure.

Generated reports are written to git-ignored `eval-results/` as JSON and CSV.

## Scenario coverage

The project covers the supplied regression behavior:

- VIP damaged order under authority -> automatic refund approval;
- legitimate damaged order above automatic authority -> human escalation;
- outside return window -> rejection;
- non-returnable digital gift card -> rejection;
- `$48` authority boundary -> approval;
- `$52` authority boundary -> escalation;
- risk signals -> escalation;
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

The lightweight Stage 2 seams already exist: a clear agent I/O contract, isolated tool access, typed state, and a small provider abstraction.

## Submission gate

The implementation is not claimed release-ready until live evidence exists. Final steps:

1. follow the safe synchronization instructions in `docs/M3_RUNBOOK.md`;
2. confirm deterministic CI/local tests are green;
3. add only the local `OPENROUTER_API_KEY`;
4. run the two authority smoke scenarios;
5. if green, run all nine live scenarios once;
6. only then run repetitions if time/budget permits;
7. confirm no secrets, `.vendor` source, or `eval-results/` artifacts are committed;
8. perform the final IP/public-repository check before changing repository visibility.

**No project-specific OpenRouter pass rate, latency, or release-model winner is claimed until those live calls are actually run.**
