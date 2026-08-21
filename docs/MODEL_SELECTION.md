# Model Selection Evidence

The release model is selected from measured GlobalCart behavior rather than model size or reputation.

## Release rule

Reliability is the gate; efficiency is secondary. A model/provider path is not release-eligible if evaluation contains a `CRITICAL_FAILURE` such as a wrong business decision, hallucinated case, silent requested-amount reduction, refund-precondition bypass, runtime failure, or uncontrolled loop.

There is no LLM judge for business correctness. The scorer uses expected scenario outcomes plus trusted runtime/tool traces.

## Selected Stage 1 path

```text
provider: openrouter
model:    qwen/qwen3.5-397b-a17b
reasoning effort: provider default
```

Per-million price is intentionally not pinned because OpenRouter can route the same model through different inference backends with different prices.

Why this path was selected:

- autonomous tool calling works through the provider-neutral runtime;
- no model-specific agent implementation is required;
- structured output is available for the bounded no-tools repair path;
- most importantly, project-specific live GlobalCart evaluation cleared the correctness/safety gate.

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

Both adapters normalize into the same `ModelResponse`. OpenAI-compatible message/tool conversion is shared. API keys stay in provider-specific environment configuration and never enter the agent loop.

Normal autonomous calls keep tools available and do not assume simultaneous tool calling plus `response_format`. A supported no-tools repair call may use the Pydantic-derived JSON Schema.

## Previous Groq evidence

Early live testing used Groq-hosted `openai/gpt-oss-20b` and `openai/gpt-oss-120b`. Provider rate limits contaminated the aggregate matrix, so it is not presented as clean accuracy evidence.

Useful pre-limit failures still exposed a real semantic issue: models could reduce a full refund request to an automatic cap (`$150 -> $50`, `$52 -> $50`). The project hardened that model-independently:

- prompt explicitly preserves the requested/full amount;
- eval scoring pins `ORD-1002` to `150.0` and `ORD-1011` to `52.0` at the actual `process_refund` boundary;
- a missing/reduced request is critical;
- terminal business outcomes are projected from trusted evidence.

## OpenRouter/Qwen live evidence

### Selection run

A complete nine-entry live catalog on 2026-08-21 at commit:

```text
06b0286ddf8309dbfa668e24543393e7a6099024
```

produced:

```text
9/9 agent scenarios passed
7 CLEAN_PASS
2 PASS_WITH_WARNING (bounded output repair only)
0 critical failures
5/5 Scenario 8 probes clean
average duration: 14.07 s
average total tokens: 10,199
average tool calls: 2.78
repair rate: 22.2%
max passing steps: 4
```

This run selected OpenRouter/Qwen and specifically verified the authority-boundary amounts: `$150` and `$52` were passed in full and escalated rather than clipped.

### Post-terminal-renderer run

After replacing phrase-specific terminal safety checks with deterministic localized rendering, a fresh run at commit:

```text
2be3a1a90114f40ce0bd96821b4ef65ceafb37dd
```

produced:

```text
10/10 CLEAN_PASS
0 warnings
0 critical failures
release gate: PASS
5/5 Scenario 8 probes clean
repair rate: 0%
average duration: 26.46 s
average total tokens: 9,365.4
average tool calls: 2.9
max passing steps: 4
```

The tenth entry is a project-owned Hebrew end-to-end Scenario 2 smoke. It reached the same trusted `HUMAN_ESCALATION` outcome and rendered the Hebrew customer response from canonical state.

## Final review hardening

Automated code review then identified one nonterminal presentation gap: `NEEDS_CLARIFICATION` could bypass the terminal renderer and deliver unsafe free-form draft wording.

The fix follows the same architecture rather than adding phrase lists:

```text
clarification decision by LLM
    -> unresolved cases canonicalized to NO_ACTION
    -> deterministic clarification question from structural state
```

Deterministic CI passed after the fix with 215 project tests, the supplied 33/33 verifier, and a valid eval dry-run.

### Final submission run

A fresh complete live run was then executed on the post-review runtime at:

```text
385e307e3526cc906bddf3675d2677da6c4a480c
```

Result:

```text
10/10 CLEAN_PASS
0 warnings
0 critical failures
release gate: PASS
5/5 Scenario 8 probes CLEAN_PASS
repair rate: 0%
average duration: 15.06 s
average model latency: 15.06 s
average total tokens: 9,912.5
average tool calls: 3.0
max passing steps: 4
```

The Hebrew smoke also passed cleanly and rendered:

```text
הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי.
```

This is the final runtime evidence used for submission readiness.

## Scope of claims

- Measured live runs are one catalog repetition each, not statistical-stability claims.
- Cost is not claimed because the OpenRouter backend/price is not pinned.
- OpenRouter routing can change underlying latency over time.
- A systematic correctness failure must be fixed, never averaged away with repetitions.
