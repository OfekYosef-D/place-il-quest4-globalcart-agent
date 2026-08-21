# Model Selection Evidence

The release model is selected from measured GlobalCart behavior rather than model size or reputation.

## Release rule

Reliability is the gate; efficiency is a secondary metric.

A candidate is not release-eligible if a live evaluation contains a `CRITICAL_FAILURE`, including a wrong business decision, hallucinated case, silent requested-amount reduction, refund-precondition bypass, runtime failure, or uncontrolled loop.

There is no LLM judge for business correctness. The scorer uses expected scenario outcomes plus the trusted runtime/tool trace.

## Previous Groq evidence

The first live matrix used Groq-hosted `openai/gpt-oss-20b` and `openai/gpt-oss-120b`. Provider rate limits contaminated the aggregate matrix, so it is not presented as clean accuracy evidence.

Useful pre-limit failures still exposed a real semantic issue: both models could reduce full-refund authority-boundary requests to the automatic cap (`$150 -> $50`, `$52 -> $50`). The project hardened that behavior model-independently:

- the system prompt preserves the requested/full amount;
- the eval scorer pins `ORD-1002` to `150.0` and `ORD-1011` to `52.0` at the actual `process_refund` boundary;
- a missing/reduced request is a critical failure;
- terminal business outcomes are projected deterministically from trusted evidence.

The 120B setup also showed additional protocol/timeline failures and was not kept as an active release candidate.

## Selected Stage 1 path

Provider:

```text
openrouter
```

Model:

```text
qwen/qwen3.5-397b-a17b
```

Reasoning override: provider default.

Per-million price is intentionally not pinned in the project because OpenRouter may route the same model through different inference backends with different current prices.

Why this path was selected:

- it supports the required autonomous tool-calling behavior;
- it works through the provider-neutral runtime rather than a model-specific agent implementation;
- structured output is available for the bounded no-tools repair path;
- most importantly, the project-specific live GlobalCart suite passed with zero critical failures before the final presentation refactor.

Capability references checked 2026-08-20:

- https://openrouter.ai/qwen/qwen3.5-397b-a17b
- https://openrouter.ai/qwen/qwen3.5-397b-a17b/providers
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/features/tool-calling
- https://openrouter.ai/docs/features/structured-outputs

## Provider architecture

```text
OperationsResolverAgent
        |
        v
    LLMProvider
      /     \
     /       \
GroqProvider OpenRouterProvider
```

Both adapters normalize into the same `ModelResponse`. OpenAI-compatible message/tool conversion is shared. API keys remain provider-specific environment configuration and never enter the agent loop.

Normal autonomous calls keep tools available and do not depend on combining tool use with `response_format`. If a final structured draft needs the single repair pass, that no-tools call may use the Pydantic-derived JSON Schema on a supported provider/model route.

## Measured OpenRouter/Qwen evidence

A complete local nine-scenario catalog run was executed on 2026-08-21 at:

```text
06b0286ddf8309dbfa668e24543393e7a6099024
```

Command:

```bash
python run_evals.py --repetitions 1
```

Observed summary:

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
Scenario 8 probes:     5/5 clean
```

The two warnings were `OUTPUT_REPAIR_USED` on Scenario 2 and Scenario 9. Both final business outcomes were correct and safe after the single bounded repair pass.

Authority-boundary behavior was specifically verified:

- `ORD-1002` preserved the `$150` request and escalated instead of clipping to `$50`;
- `ORD-1011` preserved the `$52` request and escalated instead of clipping to `$50`.

## Presentation hardening after model selection

A later Hebrew manual smoke used the same Scenario 2 business case. The model correctly understood Hebrew, selected the correct tools, passed the full `$150` amount, and reached `ESCALATION_REQUIRED`, but its free-form Hebrew customer wording added an unsupported future-contact promise.

That finding changed the architecture rather than creating a list of Hebrew forbidden phrases:

```text
trusted tool evidence
      -> deterministic terminal outcome
      -> deterministic localized customer rendering
```

Terminal business claims no longer depend on parsing model prose for English/Hebrew semantic patterns. The eval catalog now contains ten live agent entries: the original nine plus a project-owned Hebrew end-to-end Scenario 2 smoke.

## Current verification state

On the post-presentation-refactor code path, CI is green:

```text
project tests:       212 passed
upstream verifier:   33/33 passed
eval dry-run:        green (10 live scenarios + 5 tool probes)
```

The earlier 9/9 live result remains valid evidence for selecting OpenRouter/Qwen, but it is not reused as proof for the changed final runtime. One fresh full live run is the final submission gate:

```bash
python run_evals.py --repetitions 1
```

Release requires zero critical failures.

## Scope of claims

- The measured live result above is one complete catalog run, not a multi-repetition statistical-stability claim.
- Cost is not claimed because the OpenRouter inference backend/price was not pinned.
- OpenRouter routing can change underlying provider latency over time.
- If the final post-refactor live run exposes a systematic correctness issue, it must be fixed rather than averaged away with repetitions.
