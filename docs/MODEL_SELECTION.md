# Model Selection Evidence

Milestone 3 selects a runtime model from measured project behavior, not from model size or reputation alone.

## Release rule

Reliability is the gate; efficiency is the tie-breaker.

A candidate is not release-eligible if the final evaluation contains a `CRITICAL_FAILURE`, including a wrong business decision, hallucinated case, false refund/action claim, silent requested-amount reduction, refund-precondition bypass, customer-facing internal risk disclosure, crash, or bounded-loop failure.

Among candidates that clear that gate, compare clean-pass stability, warnings, latency, tokens, tool/retry behavior, and cost when the price is actually pinned/measurable.

Do not use an LLM judge as business authority.

## External reference benchmarks

Useful context for this problem shape:

- Sierra Research `tau2-bench` / tau-bench: conversational agents operating under domain policy with tools, including retail/customer-service tasks: https://github.com/sierra-research/tau2-bench
- Berkeley Function Calling Leaderboard (BFCL): function/tool-calling behavior, including multi-turn and agentic cases: https://github.com/ShishirPatil/gorilla

External leaderboards are context only. The release decision for this project comes from the local GlobalCart scenario suite because its tools, guardrails, and failure modes are the actual target.

## Previous Groq / GPT-OSS evidence

The first live matrix used Groq-hosted `openai/gpt-oss-20b` and `openai/gpt-oss-120b`. The aggregate matrix was contaminated by provider rate-limit responses and therefore must not be presented as clean model accuracy.

The genuine pre-limit failures were still useful: both models repeatedly reduced full-refund authority-boundary requests to the automatic cap (for example `$150 -> $50` and `$52 -> $50`). That behavior violated the supplied business contract because `process_refund` must receive the requested/full amount and decide whether to approve or escalate.

That gap is now hardened model-independently:

- the prompt prohibits invented/silent partial refunds and preserves the requested/full order amount;
- eval scenarios pin `ORD-1002` to `150.0` and `ORD-1011` to `52.0` at the actual `process_refund` call boundary;
- any reduction or missing required refund request is a critical eval failure;
- terminal business outcomes are projected deterministically from trusted tool evidence.

The 120B setup also produced additional genuine protocol/timeline failures, so it is not an active release candidate.

## Selected release path

Provider/gateway:

```text
openrouter
```

Model:

```text
qwen/qwen3.5-397b-a17b
```

Reasoning override: unset/provider default.

Configured per-million price: intentionally unset because OpenRouter can route the same model through multiple inference backends with different prices unless a backend is pinned.

Why this candidate was selected for the final Stage 1 submission path:

- it exposes the required autonomous tool-calling behavior through the existing provider-neutral runtime;
- OpenRouter exposes structured output for the selected model on no-tools calls, which is used only for the bounded repair path;
- `provider.require_parameters=true` restricts routing to inference endpoints that advertise support for parameters requested by the adapter;
- most importantly, the project-specific live GlobalCart suite passed with zero critical failures.

Official capability references checked 2026-08-20:

- https://openrouter.ai/qwen/qwen3.5-397b-a17b
- https://openrouter.ai/qwen/qwen3.5-397b-a17b/providers
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/features/tool-calling
- https://openrouter.ai/docs/features/structured-outputs

## Provider architecture

The runtime depends only on `LLMProvider`. Provider selection is configuration:

```text
OperationsResolverAgent
        |
        v
    LLMProvider
      /     \
     /       \
GroqProvider OpenRouterProvider
```

Both adapters normalize into the same `ModelResponse`. OpenAI-compatible wire conversion is shared in one small helper module rather than duplicated between adapters. API keys remain provider-specific environment variables and never enter the agent loop.

The final-output contract is provider-neutral:

- normal autonomous calls keep tools available;
- normal tool calls do not combine tool use with `response_format`;
- if final content is malformed or inconsistent, one no-new-tools repair call is allowed;
- on a verified provider/model route, that repair call may request the Pydantic-derived `AgentResult` JSON Schema;
- deterministic outcome projection and validation remain the business/safety authority regardless of provider-native schema support.

This deliberately avoids depending on an unverified tools-plus-schema request shape.

## Final live evidence

A complete local catalog run was executed on **2026-08-21** at commit:

```text
06b0286ddf8309dbfa668e24543393e7a6099024
```

Command:

```bash
python run_evals.py --repetitions 1
```

Observed candidate summary:

```text
total agent runs:      9
clean passes:          7
passes with warning:   2
critical failures:     0
release gate:          PASS
pass rate:             100%
clean rate:            77.8%
average duration:      14.07 s
average total tokens:  10,199
average tool calls:    2.78
repair rate:           22.2%
max passing steps:     4
```

All five deterministic Scenario 8 bad-input/source-behavior probes were also `CLEAN_PASS`.

The two warning runs were:

- `s2_standard_above_cap_escalates` -> `OUTPUT_REPAIR_USED`;
- `s9_nonexistent_order_no_hallucination` -> `OUTPUT_REPAIR_USED`.

Both remained correct and safe after the single targeted correction pass. There were no critical failures.

The authority-boundary cases were specifically verified after the refund hardening:

- `ORD-1002` preserved the full `$150` request and escalated instead of clipping to `$50`;
- `ORD-1011` preserved the full `$52` request and escalated instead of clipping to `$50`.

A later targeted live smoke of the customer-facing guardrail changes produced `3/3 CLEAN_PASS` for Scenario 1, Scenario 5a, and Scenario 6 with no repairs.

## Scope of the claim

The evidence supports selecting `openrouter/qwen/qwen3.5-397b-a17b` as the Stage 1 release path for this submission.

The claim is intentionally narrow:

- one complete nine-scenario live catalog run passed with zero critical failures;
- deterministic tests and the supplied 33-check verifier were green at the same code revision;
- this is not a statistical stability claim across repeated live runs;
- cost is not reported because the OpenRouter inference backend and price were not pinned;
- OpenRouter routing can vary the underlying provider, so the measured latency/token result belongs to this observed run, not to every future route.

Three repetitions remain optional if time/budget permits. They are not required to reinterpret or average away any systematic failure; if a future repeated run exposes a real correctness issue, that issue should be fixed rather than hidden by aggregation.