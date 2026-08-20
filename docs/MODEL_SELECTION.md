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

The genuine pre-limit failures were still useful: both models repeatedly reduced full-refund authority-boundary requests to the automatic cap (for example `$150 -> $50` and `$52 -> $50`). Source review showed the system prompt had not stated the requested-amount semantics explicitly. The runtime/tools behaved as implemented; the prompt/eval contract was incomplete.

That gap is now hardened model-independently:

- the prompt prohibits invented/silent partial refunds and preserves the requested/full order amount;
- eval scenarios pin `ORD-1002` to `150.0` and `ORD-1011` to `52.0` at the actual `process_refund` call boundary;
- any reduction or missing required refund request is a critical eval failure.

The 120B setup also produced additional genuine protocol/timeline failures, so it is not an active release candidate.

## Active provider/model candidate

The next candidate is:

- provider/gateway: `openrouter`;
- model: `qwen/qwen3.5-397b-a17b`;
- reasoning override: unset/provider default;
- configured per-million price: intentionally unset because OpenRouter can route the same model through multiple backends with different prices unless a backend is pinned.

Why this candidate is worth testing:

- the current OpenRouter model page advertises both `tools` and `response_format` for `qwen/qwen3.5-397b-a17b`;
- OpenRouter standardizes the OpenAI-compatible tool-call loop across tool-capable models;
- `provider.require_parameters=true` restricts routing to inference endpoints that advertise support for the parameters requested by the adapter;
- current tau-bench/OpenRouter evidence makes this a materially more relevant agentic candidate than selecting another model from size or reputation alone.

Official references checked 2026-08-20:

- https://openrouter.ai/qwen/qwen3.5-397b-a17b
- https://openrouter.ai/qwen/qwen3.5-397b-a17b/providers
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/features/tool-calling
- https://openrouter.ai/docs/features/structured-outputs
- https://taubench.com/

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

The final-output contract is provider-neutral. `SchemaBoundProvider` binds the Pydantic-derived `AgentResult` JSON Schema only when the selected adapter/model route has an explicitly verified capability to combine tool use with structured output. For the active OpenRouter/Qwen candidate, normal autonomous calls therefore keep tools available while constraining final content. For Groq, normal tool calls remain unchanged because its documented Structured Outputs path cannot be combined with tools; supported no-tools repair calls may still use the schema. The deterministic parser and validator remain the final consistency/safety boundary in both cases.

This is deliberately capability-based rather than assuming every OpenRouter model supports the same parameters. Adding another model to structured tool mode requires verifying its current OpenRouter capabilities first.

## What is intentionally not claimed yet

Until the OpenRouter candidate is run with local credentials:

- Qwen3.5-397B-A17B is a **candidate**, not the selected release winner;
- there is no project-specific OpenRouter latency/token/cost result;
- there is no claim that all nine live scenarios pass;
- the provisional `AGENT_MAX_STEPS` value is not considered calibrated from this candidate.

First run the two known authority-boundary smoke scenarios. Only if they pass should the full nine-scenario catalog be run once; repetitions come after broad correctness.
