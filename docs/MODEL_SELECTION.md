# Model Selection Evidence

Milestone 3 selects a runtime model from measured project behavior, not from model size or reputation alone.

## Release rule

Reliability is the gate; efficiency is the tie-breaker.

A candidate is not release-eligible if the final evaluation run contains a `CRITICAL_FAILURE`, including a wrong business decision, hallucinated case, false refund/action claim, refund-precondition bypass, customer-facing internal risk disclosure, crash, or bounded-loop failure.

Among candidates that clear that gate, compare:

- clean-pass rate and warning rate;
- stability across repetitions;
- latency;
- total tokens;
- estimated API cost;
- tool-call count / repeated calls;
- repair usage;
- observed normal step count.

Do not use an LLM judge as business authority.

## External reference benchmarks

Useful context for this problem shape:

- Sierra Research `tau2-bench` / tau-bench: conversational agents operating under domain policy with tools, including retail/customer-service tasks: https://github.com/sierra-research/tau2-bench
- Berkeley Function Calling Leaderboard (BFCL): function/tool-calling behavior, including multi-turn and agentic cases: https://github.com/ShishirPatil/gorilla

External leaderboards are context only. The release decision for this project comes from the local GlobalCart scenario suite because its tools, guardrails, and failure modes are the actual target.

## Initial Groq candidates

`evals/candidates.json` currently defines:

- `openai/gpt-oss-20b`, medium reasoning;
- `openai/gpt-oss-120b`, medium reasoning.

The candidate file is configuration, not agent logic. It records a `checked_at` date and pricing source because public model pricing is mutable.

Groq's API currently documents `reasoning_effort=low|medium|high` for both GPT-OSS models. The provider exposes that setting without coupling the core agent loop to a specific model.

Official references checked 2026-08-19:

- https://console.groq.com/docs/api-reference
- https://console.groq.com/docs/reasoning
- https://console.groq.com/docs/model/openai/gpt-oss-20b
- https://console.groq.com/docs/model/openai/gpt-oss-120b

## What is intentionally not claimed yet

Until live evals are executed with credentials:

- there is no chosen release model;
- there is no project-specific latency/token/cost result;
- the provisional `AGENT_MAX_STEPS` value is not considered calibrated;
- Milestone 3 is not complete.

Record those conclusions only from generated `eval-results/` evidence after the full deterministic test suite and supplied 33-check verifier are green.
