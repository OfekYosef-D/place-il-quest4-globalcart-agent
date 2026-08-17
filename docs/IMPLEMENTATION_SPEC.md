# Implementation Specification - GlobalCart Single-Agent Resolver

This document is the authoritative engineering handoff for Stage 1. Coding agents must read it before implementation.

## 1. Goal

Build a portfolio-quality but intentionally small **single autonomous Operations Resolver Agent** for GlobalCart.

The agent receives a complex customer support incident and must:
1. analyze the issue, urgency, and sentiment;
2. use the supplied tools when needed;
3. make the supported business decision: automatic refund, rejection, or human escalation;
4. return parseable structured output containing the required top-level fields:
   - `reasoning_chain`
   - `action_taken`
   - `customer_response`

Stage 1 must stay easy to extend later, but Stage 2 infrastructure must not be built now.

## 2. Source-of-truth hierarchy

When sources disagree, use this order:
1. supplied `mock_services.py` behavior;
2. supplied fixtures in `data/`;
3. supplied `examples/scenarios.md` and `examples/verify_scenarios.py`;
4. Quest task/background documentation;
5. this specification for engineering choices not already fixed upstream.

If this document conflicts with supplied source behavior, source behavior wins. Document the discrepancy before coding around it.

## 3. Supplied tool facts

The supplied agent tool set is:
- `get_order_details(order_id)`
- `get_user_profile(user_id)`
- `check_return_policy(order_id, reason=...)`
- `process_refund(order_id, amount, reason=...)`

Use the supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY`. Do not rebuild or edit them.

Important facts:
- tools are local deterministic Python functions;
- business failures are returned as data, not normal business exceptions;
- the FastAPI/Swagger app is a learning aid only; the agent should import/use the local tool layer directly rather than call it over HTTP;
- `check_return_policy` is the business-policy source of truth;
- `process_refund` rechecks policy and mechanically enforces operational refund authority;
- `process_refund` is simulated: no real payment, network side effect, or disk mutation;
- do not implement production idempotency/reconciliation for Stage 1.

Known supplied rules, for understanding/evals only:
- standard return window: 30 days;
- VIP return window: 45 days;
- standard auto-refund cap: 50 USD;
- VIP auto-refund cap: 75 USD;
- non-returnable categories: digital goods, perishables, gift cards;
- refundable statuses: delivered, shipped;
- escalation signals include requested amount above cap, fraud score >= 60, prior fraud flags > 0, and >= 3 refund claims in trailing 60 days;
- fixture reference date is 2026-08-05.

Do **not** duplicate those rules in the validator. The tool result remains authoritative.

## 4. Agent versus workflow boundary

This must be a real model-driven tool loop, not a coded fixed sequence.

The LLM decides:
- what the ticket means;
- which allowed tool is needed next;
- tool ordering;
- when enough information is available;
- final concise rationale and customer-facing response.

Python/runtime decides and enforces:
- actual tool execution;
- trusted state storage;
- tool result caching;
- loop bounds/no-progress protection;
- transient LLM retries;
- structured-output parsing and validation;
- consistency guardrails;
- fail-safe behavior;
- tracing/metrics.

Principle: **probabilistic intelligence, deterministic guardrails.**

Do not hardcode the normal path `order -> user -> policy -> refund` into control flow. It is a common sensible sequence, but the model must choose calls dynamically.

## 5. Technology and structure

Use Python.

Do not use LangGraph initially. Implement the basic agent loop directly so the architecture is understandable and interview-ready.

Recommended structure:

```text
app/
  agent.py
  state.py
  schemas.py
  validator.py
  tracing.py
  config.py
  prompts/
    system_prompt.md
  llm/
    base.py
    groq_provider.py

tests/
  unit/
  evals/

run_agent.py
run_evals.py
.env.example
.gitignore
README.md
```

Minor simplifications are fine. Avoid enterprise-style layering.

## 6. Provider abstraction

The agent loop must not contain provider-specific logic.

Conceptual interface:

```text
LLMProvider.generate(messages, tools) -> ModelResponse
```

Normalized `ModelResponse` should include:
- assistant content and/or tool calls;
- provider/model identifier;
- input tokens when reported;
- output tokens when reported;
- total tokens when reported;
- latency in milliseconds;
- optional raw usage metadata only if useful for debugging.

Provider/model/temperature/timeout/retry settings come from config/environment, not hardcoded agent logic.

Initial provider may be Groq/OpenAI-compatible, but preserve the abstraction.

Start with:
- low temperature, ideally 0 when provider/model supports it;
- request timeout configured;
- initial LLM attempt + 2 retries for known transient failures only.

Non-transient auth/config errors must not be retried blindly.

## 7. Agent state

Use Pydantic for important contracts.

### `AgentState`

Conceptual fields:
- `messages`
- `cases`
- `tool_history`
- `sentiment`
- `urgency`
- `step_count`
- `status`
- `final_result`
- `failure_reason`

Recommended runtime statuses:
- `RUNNING`
- `NEEDS_CLARIFICATION`
- `COMPLETED`
- `FAILED_SAFE`

### `CaseState`

Keep it small:
- `order_id`
- `reason`
- `verified_order`
- `verified_user`
- `policy_result`
- `refund_result`
- `decision`

State stores facts/results, not a duplicate policy engine.

The runtime owns state. The LLM only sees the relevant context the runtime sends on each model call.

## 8. Conversation and clarification

Maintain short-term conversation state across customer turns.

Example:
- customer: “my product is broken”
- agent asks for order number
- customer: “ORD-1001”
- runtime keeps enough prior context to continue the same case.

Do not invent global customer memory. Persistent customer facts always come from trusted tools.

If information is missing:
- no order ID for an order-specific issue -> `NEEDS_CLARIFICATION`;
- order ID exists but complaint is too ambiguous to safely map to a supplied reason -> ask one targeted clarification;
- do not guess a return reason or order ID.

`get_user_profile` does not provide a list of all customer orders. There is no supplied agent tool that can discover an order from user ID alone.

## 9. Multiple orders

A single customer turn may mention N independent orders.

Requirements:
- one agent, one run, no concurrency/multiple-agent architecture;
- each order is an independent `CaseState`;
- one case may approve while another rejects/escalates/fails;
- terminal failure in one case must not invalidate unrelated cases;
- return one consolidated `customer_response`.

Use the same output schema for single-order and multi-order runs: `cases` is always a list.

## 10. Structured final output

Quest-required top-level fields must remain present:
- `reasoning_chain`
- `action_taken`
- `customer_response`

Recommended schema:

```text
AgentResult
  status
  reasoning_chain: list[str]
  action_taken:
    tools_called: list[str]
    cases: list[CaseResult]
  customer_response: str
```

Recommended `CaseResult` fields:
- `order_id`
- `decision`
- `refund_amount` optional
- `refund_id` optional
- `policy_verdict` optional
- `error_code` optional
- `escalation_reasons` list

Recommended business-decision enum:
- `AUTO_REFUND_APPROVED`
- `REJECTED`
- `HUMAN_ESCALATION`
- `NO_ACTION`

Final status:
- `COMPLETED`
- `NEEDS_CLARIFICATION`
- `FAILED_SAFE`

Do not add speculative fields such as confidence scores or recommendation engines unless a concrete requirement appears.

## 11. Reasoning-chain policy

The assignment requires a reasoning chain. Implement a concise, developer-facing audit rationale, not verbose hidden-style chain-of-thought.

Use roughly 3-6 factual bullets per case, based on trusted evidence such as:
- verified order facts;
- verified customer/profile facts when relevant;
- policy verdict and policy IDs;
- actual refund result when called.

## 12. Customer language, sentiment, urgency

Understand at least English and Hebrew.

- `customer_response` should follow the customer's language;
- internal enums/tool names/policy IDs/log fields remain English;
- sentiment and urgency may affect wording and tone;
- sentiment/urgency must **not** modify refund eligibility, authority, amount, fraud rules, or policy outcome;
- avoid a separate sentiment-only LLM call unless evidence shows it is necessary.

## 13. Business terminal behavior

Treat trusted terminal outcomes as enough to stop a case.

### Order not found
`ORDER_NOT_FOUND` -> stop that case, ask customer to confirm the order number, do not invent facts, and do not force a second tool call just to satisfy the “2 tools” rubric.

### Policy ineligible
If `check_return_policy` returns `eligible=false`, reject based on that trusted result and policy information. Do not call `process_refund` merely to get another rejection.

### Policy eligible
`ELIGIBLE` does **not** mean a refund has happened. `process_refund` determines the operational result.

### Refund outcomes
- `APPROVED` -> refund can be described as approved/issued according to returned amount/id;
- `ESCALATION_REQUIRED` -> no refund was issued; human review required;
- `REJECTED` -> no refund was issued.

Never tell a customer that a refund succeeded unless a trusted `process_refund` result actually returned `APPROVED`.

If a technical failure happens after a trusted terminal business outcome is already known, prefer a safe response grounded in that known outcome. If the business outcome is still unresolved, fail safe to human escalation rather than inventing a result.

## 14. Partial refunds

Do not invent a partial-refund policy.

There is no supplied rule that maps severity to a refund percentage. Use the simple supplied examples/amounts. Revisit only if a concrete upstream test requires it.

## 15. Tool execution, errors, cache, loops

Dispatch model-requested tools through the supplied registry, conceptually:

```python
TOOL_REGISTRY[name](**arguments)
```

Business errors are returned data. The agent must inspect them and stop/recover honestly rather than retry in a loop.

Known structured error codes include:
- `ORDER_NOT_FOUND`
- `USER_NOT_FOUND`
- `INVALID_AMOUNT`
- `INVALID_REASON`

Programmer/system exceptions from local deterministic tools are system failures: log and fail safely rather than blindly retrying.

Cache identical deterministic tool calls using `(tool_name, normalized_args)`.

If the same tool+args is requested again:
- return the cached result;
- record a cache hit in trace;
- detect repeated cycles/lack of progress;
- eventually fail safely rather than loop forever.

`max_steps` is a safety ceiling, not a normal stop mechanism. Calibrate it from actual eval traces and leave it configurable.

## 16. Malformed model output and validator

Final output must parse through the Pydantic response model.

If output is malformed or inconsistent:
1. collect exact validation errors;
2. perform **one targeted correction pass**;
3. correction may not call new tools;
4. revalidate;
5. if still invalid -> `FAILED_SAFE`.

Missing customer information is not an output-repair problem. Ask for clarification instead.

The deterministic validator checks **consistency**, not business policy.

It should verify things such as:
- schema valid;
- final decision consistent with trusted tool result;
- no refund-success language unless actual `APPROVED` exists;
- refund id/amount match trusted result;
- nonexistent orders do not acquire invented facts;
- multi-order outputs map to the correct cases/tool evidence.

The validator must **not** recompute return windows, caps, fraud policy, or other business rules already enforced by supplied tools.

No LLM evaluator is business authority in Stage 1.

## 17. Prompt-injection boundary

Customer text is untrusted case data, never operational authority.

System prompt should state that customer content cannot:
- override system instructions;
- disable policy/tool checks;
- grant refund authority;
- instruct fabrication;
- change allowed tools/protocol behavior;
- force the system to claim unconfirmed actions succeeded.

Defense layers:
1. system prompt hierarchy;
2. runtime exposes only allowed tools and structured arguments;
3. supplied tools enforce business truth;
4. deterministic validator blocks contradictions.

Do not invent new fraud/security business policies beyond supplied behavior.

## 18. Observability

Keep customer-facing output separate from developer trace.

Each model call should capture when available:
- step;
- provider/model;
- latency;
- input/output/total tokens;
- result kind (tool call/final);
- retry metadata.

Each tool call should capture:
- step;
- tool name;
- arguments;
- result;
- duration;
- cache hit/miss.

Run summary should include:
- LLM call count;
- tool-call count;
- total tokens;
- total duration;
- estimated cost when pricing is configured.

Cost formula uses provider-reported usage + configurable model pricing. Do not hardcode mutable public prices inside the agent.

## 19. CLI

Default CLI is clean/customer-facing.

Example:

```text
Customer:
> My order ORD-1001 arrived damaged.

Agent:
> ...customer-facing answer...
```

`--verbose` additionally shows developer trace:
- model/tool steps;
- validation;
- latency;
- token usage;
- estimated cost;
- run summary.

The same entry point should support both demo and debugging.

## 20. Evaluation strategy

Keep deterministic tests separate from live-model evals.

Suggested:
- `tests/unit/`: schemas/state, validator, cache/loop protection, provider normalization, parsing/state transitions;
- `tests/evals/`: real model behavior against supplied business scenarios.

Evaluate:
- final business decision correctness;
- schema validity;
- credible tool use;
- hallucination-free behavior;
- false refund claims;
- correct stop behavior;
- unnecessary tool calls as warnings, not automatic correctness failures;
- latency;
- token usage;
- estimated cost.

Eval classification:
- `CLEAN_PASS`: correct and efficient;
- `PASS_WITH_WARNING`: correct but harmless unnecessary work;
- `CRITICAL_FAILURE`: wrong decision, hallucination, false refund/action claim, unsafe mismatch, crash, or uncontrolled loop.

Correctness and safety gate model selection. Among models that pass reliably, prefer cheaper/faster models.

When budget permits, run each scenario multiple times; 3 repetitions is a useful starting point for stability measurement, not a hard requirement.

## 21. Supplied scenario expectations

Preserve these supplied regression behaviors:
1. `ORD-1001`: VIP, damaged, 35 USD -> eligible -> refund `APPROVED`.
2. `ORD-1002`: Standard, damaged, 150 USD -> eligible on merits -> `ESCALATION_REQUIRED`.
3. `ORD-1003`: changed mind, 60 days after delivery -> `OUTSIDE_RETURN_WINDOW` -> reject; no refund call required.
4. `ORD-1008`: digital gift card -> `NON_RETURNABLE_CATEGORY` -> reject despite VIP.
5. Boundary:
   - `ORD-1010`: 48 USD Standard -> approve;
   - `ORD-1011`: 52 USD Standard -> escalate.
6. `ORD-1005`: risky customer -> escalate even for a small amount.
7. `ORD-1007` processing and `ORD-1009` cancelled -> `ORDER_NOT_REFUNDABLE`; do not route as a successful refund.
8. Bad inputs -> structured error data, honest stop, no retry loop.
9. `ORD-2222`: nonexistent order -> no hallucination; ask customer to confirm order number.

The supplied FastAPI `/scenarios` helper simplifies the two-order boundary example. For agent eval truth, use `examples/scenarios.md` plus verification expectations and split the boundary into clear case-level expectations when useful.

## 22. Stage 2 extensibility

Prepare only lightweight seams:
- clear agent input/output interface;
- tool access separated from reasoning so future agents can receive subsets;
- provider abstraction.

Do **not** build:
- orchestrator;
- router;
- manager/workers;
- message bus;
- shared multi-agent memory;
- planner graph.

Design for extension, not imaginary requirements.

## 23. UI scope

Do not build frontend before core reliability is green.

Order:
1. contracts/runtime;
2. CLI;
3. evals/reliability;
4. README/polish;
5. optional small WhatsApp-style chat demo.

Future UI must reuse the exact same core agent API/runtime and contain no business logic.

## 24. Definition of Done

Stage 1 is done when:
- supplied starter-kit verification reports all 33 checks passing;
- all supplied business scenarios have explicit agent eval coverage and correct required outcomes;
- nonexistent orders never create invented facts;
- final output always parses through the response schema or fails safely;
- no customer response claims refund success without trusted `APPROVED` evidence;
- rejection and escalation are not confused;
- missing information triggers clarification rather than guessing;
- multi-order cases remain isolated;
- LLM/tool/output failures fail safely;
- loops are bounded and identical calls are cached;
- clean CLI and `--verbose` CLI work;
- verbose trace includes useful latency/token/cost information;
- `.env`/secrets never enter Git;
- README explains architecture, framework/provider choice, tools, run instructions, reasoning/error/edge-case handling, and eval evidence;
- implementation stays explainable and small enough for the developer to understand deeply.

## 25. Explicit non-goals

Do not add these unless a later explicit requirement demands them:
- LangGraph/LangChain orchestration;
- MCP;
- multi-agent runtime;
- database/Redis;
- message bus;
- distributed tracing;
- persistent customer memory;
- production idempotency/reconciliation subsystem;
- separate business-rule engine;
- LLM judge as policy authority;
- sophisticated sentiment classifier;
- speculative partial-refund logic.
