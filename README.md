# GlobalCart Operations Resolver Agent

**Place IL Quest 4 — Stage 1**

A single autonomous customer-operations agent for the fictional GlobalCart retail environment. The LLM decides what the customer needs, which supplied tool to call next, and when it has enough evidence to stop. Deterministic Python owns execution authority, trusted state, irreversible business outcomes, customer-facing safety, loop bounds, and fail-safe behavior.

> **Probabilistic intelligence, deterministic guardrails.**
>
> The model decides what to do. Trusted code decides what actually happened.

## Submission status

**Submission-ready.** The final post-review runtime passed every deterministic and live gate:

```text
project tests:          215 passed
supplied verifier:      33/33 passed
eval dry-run:           green
final live catalog:     10/10 CLEAN_PASS
warnings:               0
critical failures:      0
Scenario 8 probes:      5/5 CLEAN_PASS
repair rate:            0%
release gate:           PASS
```

The final live run was executed against runtime commit:

```text
385e307e3526cc906bddf3675d2677da6c4a480c
```

It included all nine Stage 1 business scenarios plus the project-owned Hebrew end-to-end authority-boundary smoke. Average duration was **15.06 s**, average total tokens **9,912.5**, average tool calls **3.0**, and the maximum passing step count was **4**.

Automated review had previously identified one real gap: a `NEEDS_CLARIFICATION` draft could bypass terminal rendering and carry unsupported free-form business claims. The fix is structural, not phrase-specific: clarification responses are runtime-rendered from trusted state, and unresolved clarification cases are canonicalized to `NO_ACTION`. The final live run above validates the post-review runtime.

## Architecture

```text
Customer / CLI
      |
      v
Autonomous LLM loop
  understand request
  choose next supplied tool
  decide when enough evidence exists
      |
      v
Guarded ToolExecutor
      |
      v
Supplied TOOL_SCHEMAS + TOOL_REGISTRY
      |
      v
Trusted AgentState / CaseState
      |
      v
Deterministic outcome projector
      |
      v
Canonical CaseResult
      |
      v
Deterministic customer renderer
  terminal facts + clarification safety
      |
      v
Structural/evidence validator
      |
      v
Structured AgentResult
```

There is **no hardcoded `order -> user -> policy -> refund` workflow**. The model owns tool selection and ordering. Runtime code enforces only safety/consistency boundaries and maps trusted tool evidence into business truth.

### Authority boundary

The LLM owns:

- issue understanding;
- tool selection and ordering;
- deciding whether clarification is required;
- sentiment and urgency assessment for internal tone metadata;
- concise developer-facing reasoning.

Deterministic code owns:

- the tool allowlist and dispatch;
- argument validation and deterministic caching;
- the `process_refund` precondition;
- trusted state;
- exact terminal decisions, refund amounts/IDs, and error codes;
- safe customer-facing business/clarification wording;
- retry, no-progress, and max-step bounds;
- one bounded no-tools output repair;
- fail-safe behavior.

## Supplied tools

The agent uses the four Stage 1 tools without modifying or reimplementing their business logic:

- `get_order_details(order_id)`
- `get_user_profile(user_id)`
- `check_return_policy(order_id, reason)`
- `process_refund(order_id, amount, reason)`

The starter kit is loaded through the supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY`. Business rules remain inside the supplied tool layer.

## Guardrails that are code, not prompt text

- `process_refund` is blocked unless the same order already has trusted `check_return_policy -> eligible=true` evidence.
- `eligible=true` never means money moved; only `process_refund(status=APPROVED)` establishes a successful refund.
- Full/requested refund amounts are passed unchanged. Automatic authority caps are never treated as permission to invent a smaller refund.
- Ineligible policy outcomes terminate without calling `process_refund` merely to obtain another rejection.
- `ORDER_NOT_FOUND` safely terminates that case instead of fabricating order data or chasing a minimum tool count.
- `APPROVED`, `ESCALATION_REQUIRED`, `REJECTED`, ineligible-policy, and terminal-error outcomes are projected from trusted state; model-authored terminal fields cannot override them.
- Terminal customer facts are rendered from canonical outcomes, so safety does not depend on matching phrases such as “refund approved” or translations of “will contact you”.
- `NEEDS_CLARIFICATION` responses are also runtime-rendered. A free-form clarification draft cannot claim a refund, expose risk data, or promise future action.
- Internal fraud/risk/profile signals never become customer-facing explanations.
- Repeated deterministic calls are cache-served; no-progress cycles and maximum steps are bounded.
- Only known transient LLM failures are retried; tool business errors are data, not infrastructure retries.
- Malformed or inconsistent final output gets at most one no-new-tools repair pass, then fails safe.

See [`docs/GUARDRAILS.md`](docs/GUARDRAILS.md).

## Structured output

Every turn returns a strict Pydantic `AgentResult`:

```text
AgentResult
  status
  reasoning_chain
  action_taken
    tools_called
    cases[]
  customer_response
```

The Quest-required top-level fields — `reasoning_chain`, `action_taken`, and `customer_response` — are always present.

Per-case decisions are:

```text
AUTO_REFUND_APPROVED
REJECTED
HUMAN_ESCALATION
NO_ACTION
```

## Requirement coverage

| Stage 1 expectation | Implementation evidence |
| --- | --- |
| One autonomous resolver agent | `OperationsResolverAgent` runs one model-controlled tool loop; no router/sub-agents |
| Real supplied tools | `ToolKit` loads the supplied schemas/registry; `ToolExecutor` dispatches actual tool calls |
| 2+ tools when feasible | Live scenarios use logical multi-tool flows; terminal errors stop safely instead of calling irrelevant tools |
| Business decision | Trusted outcomes project to approve, reject, human escalation, or no action |
| Parseable output | Strict Pydantic `AgentResult` with the required top-level fields |
| Guardrails in code | Tool precondition, terminal projection, deterministic rendering, validator, bounded loops/retries |
| Edge cases / hallucination safety | Deterministic tests + project live catalog + supplied 33-check verifier |
| Clean engineering | Provider abstraction, typed state, isolated tools, CI, reproducible verification docs |

## Provider/model

Provider selection is configuration behind `LLMProvider`.

Selected Stage 1 path:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
```

`GroqProvider` remains as an alternate adapter, demonstrating that the agent runtime is not coupled to one gateway.

Normal autonomous calls keep tools available. Native JSON Schema is reserved for a verified **no-tools** repair call rather than assuming every route supports simultaneous tool calling and structured-output mode.

See [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md).

## Setup

Python 3.11+ is required.

```bash
python -m pip install -r requirements-dev.txt
bash scripts/bootstrap_upstream.sh
```

The bootstrap script creates a git-ignored authorized checkout under `.vendor/` and runs the supplied verifier. The pinned Stage 1 source commit is:

```text
250a2e9e43189f8cf3e19260f633ed636996d68c
```

The current upstream `main` adds Stage 2 material on top of that commit; the Stage 1 files are unchanged.

Copy `.env.example` to `.env` and add the local OpenRouter API key:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=your-local-secret
LLM_REASONING_EFFORT=
```

`.env`, `.vendor/`, and generated `eval-results/` are git-ignored.

## Run the agent

One-shot:

```bash
python run_agent.py --message "My order ORD-1001 arrived damaged."
```

Developer trace:

```bash
python run_agent.py --verbose --message "Order ORD-1002 arrived damaged. I paid $150 and want a full refund."
```

Interactive short-term conversation:

```bash
python run_agent.py --verbose
```

Example authority-boundary behavior:

```text
get_order_details(ORD-1002)
check_return_policy(... damaged_on_arrival ...) -> ELIGIBLE
process_refund(amount=150, ...) -> ESCALATION_REQUIRED
customer -> additional review required; no refund issued
```

English and Hebrew are supported at the customer presentation boundary. Internal enums, tool names, policy IDs, and developer traces remain in English.

## Verification

Deterministic gate:

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

Live gate:

```bash
python run_evals.py --repetitions 1
```

The final post-review live gate passed **10/10 CLEAN_PASS**, **0 warnings**, **0 critical failures**, **0% repair rate**, and **5/5 clean Scenario 8 probes** at runtime commit `385e307e3526cc906bddf3675d2677da6c4a480c`.

The live scorer is deterministic — there is no evaluator LLM. It checks decisions, policy verdicts, trusted refund status, exact authority-boundary amounts, forbidden refund execution, hallucinated/missing cases, refund-precondition ordering, runtime failures/loops, repairs, caching/tool efficiency, latency, tokens, and steps.

The catalog contains the nine Stage 1 business entries plus a project-owned Hebrew end-to-end Scenario 2 smoke, and five direct Scenario 8 bad-input probes.

See [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

## Scope and Stage 2 readiness

Stage 1 intentionally does not add LangGraph/LangChain orchestration, MCP, multiple agents, a database, Redis, persistent customer memory, an LLM-as-judge business authority, a duplicate policy engine, or production payment infrastructure.

The seams needed for later expansion are already explicit: typed agent I/O, isolated tool access, provider abstraction, trusted short-term state, deterministic business projection, and a separate presentation boundary.

## Documentation

- [`docs/IMPLEMENTATION_SPEC.md`](docs/IMPLEMENTATION_SPEC.md) — architecture and runtime contract
- [`docs/GUARDRAILS.md`](docs/GUARDRAILS.md) — safety invariants
- [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md) — provider/model evidence
- [`docs/VERIFICATION.md`](docs/VERIFICATION.md) — reproducible deterministic/live checks
- [`docs/UPSTREAM.md`](docs/UPSTREAM.md) — pinned upstream source handling
