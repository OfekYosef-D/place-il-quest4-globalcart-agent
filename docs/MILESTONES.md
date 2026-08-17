# Implementation Milestones

Qoder should implement one milestone at a time. Do not silently continue into the next milestone.

## Milestone 0 - Source verification (no agent implementation)

Goal: prove the supplied environment is understood before coding.

Tasks:
- ensure the upstream checkout is available using `scripts/bootstrap_upstream.sh` or an equivalent read-only local checkout;
- inspect the supplied task, `mock_services.py`, fixtures, scenarios, and verification script;
- run the supplied `examples/verify_scenarios.py` and confirm all 33 checks pass;
- document any discrepancy between `docs/IMPLEMENTATION_SPEC.md` and actual upstream behavior before implementation.

Acceptance criteria:
- upstream files remain unmodified;
- 33/33 supplied checks pass;
- no project implementation code has been added beyond setup/config if needed.

## Milestone 1 - Foundation and contracts

Goal: create the typed, testable skeleton without yet implementing the full autonomous refund loop.

Tasks:
- create minimal Python project dependencies/config;
- implement config/environment loading;
- implement Pydantic models for state and final output;
- implement provider protocol/interface and normalized `ModelResponse` contract;
- implement tracing/metrics data structures;
- implement a thin adapter/loader that accesses supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY` without modifying them;
- implement deterministic tool-call cache primitives;
- add unit tests for contracts/config/cache/trace basics;
- verify `.env` is ignored.

Do not implement:
- a hardcoded order->user->policy workflow;
- LangGraph/MCP/multi-agent;
- live refund behavior beyond harmless adapter tests.

Acceptance criteria:
- foundation unit tests pass;
- supplied 33 checks still pass;
- no upstream file changed;
- provider and tool layers are isolated from the future loop.

## Milestone 2 - Agent runtime, safety, and CLI

Goal: implement the actual single-agent behavior.

Tasks:
- implement the autonomous tool-calling loop;
- maintain `AgentState` and per-order `CaseState`;
- implement short-term conversation continuation and `NEEDS_CLARIFICATION`;
- support one or multiple order IDs in the same run;
- dispatch tools through the supplied registry;
- use cache for identical deterministic tool calls;
- add max-step and no-progress/cycle protection;
- add transient-only LLM retry behavior (initial attempt + 2 retries as starting config);
- implement structured-output parsing;
- implement deterministic consistency validator;
- implement one targeted no-new-tools correction pass;
- implement fail-safe behavior;
- write a concise system prompt following the security/business boundaries in the spec;
- implement clean CLI plus `--verbose` trace;
- include token/latency metrics and configurable estimated cost;
- add deterministic unit/integration tests.

Acceptance criteria:
- no fixed tool-call workflow controls normal routing;
- terminal business errors stop correctly;
- ineligible policy result does not require a refund tool call;
- false refund claims are blocked;
- missing data asks for clarification;
- malformed final output gets at most one targeted repair;
- loops are bounded;
- CLI works in clean and verbose mode;
- supplied 33 checks remain green.

## Milestone 3 - Evals, model evidence, and portfolio polish

Goal: make reliability measurable and submission-ready.

Tasks:
- build `run_evals.py` around all supplied scenarios;
- split the two-order boundary scenario into clear case-level expectations where useful;
- score business correctness, schema validity, hallucinations, stop behavior, unnecessary calls, latency, tokens, and cost;
- support `CLEAN_PASS`, `PASS_WITH_WARNING`, and `CRITICAL_FAILURE`;
- run repeated evals when cost permits to estimate stability;
- calibrate `max_steps` from observed normal traces rather than an arbitrary guess;
- compare candidate runtime models when useful; select on reliability first, efficiency second;
- finish README with architecture rationale, tool integration, error handling, edge cases, eval evidence, and run instructions;
- make demo commands easy to copy/paste for approval, escalation, rejection, and hallucination-trap examples.

Acceptance criteria:
- all supplied scenarios have explicit automated evaluation coverage;
- no critical failures in the chosen release configuration across the final evaluation run;
- README is sufficient for another developer/reviewer to run the project;
- code is clean and explainable;
- no secrets or upstream Place IL source files are committed.

## Optional Milestone 4 - Small chat demo UI

Only start after Milestones 0-3 are complete and green.

Goal: a minimal portfolio/demo interface, not a second application architecture.

Constraints:
- reuse the exact same core agent API/runtime;
- no business logic in UI;
- chat-style presentation is enough;
- trace may be shown in a collapsible developer panel;
- do not delay submission-quality core work for UI polish.
