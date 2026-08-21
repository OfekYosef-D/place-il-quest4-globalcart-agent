# Implementation Specification

## Goal

Build one autonomous GlobalCart Operations Resolver Agent that understands a customer request, chooses and calls the supplied tools, reaches a safe business outcome, and returns a parseable result.

Stage 1 stays deliberately small: one agent, short-term session state, direct tool calling, and deterministic safety boundaries.

## Source of truth

Business facts come only from the supplied tools:

- `get_order_details`
- `get_user_profile`
- `check_return_policy`
- `process_refund`

The runtime consumes the supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY`. It does not reimplement return windows, caps, risk rules, or eligibility.

## Authority boundary

The LLM owns probabilistic judgment:

- understand the issue;
- choose the next supplied tool and ordering;
- decide whether clarification is needed;
- decide when enough evidence exists;
- summarize reasoning;
- assess sentiment/urgency for internal tone metadata.

Python owns deterministic authority:

- tool allowlisting, validation, dispatch, and caching;
- trusted state;
- refund execution preconditions;
- retry/no-progress/max-step bounds;
- terminal business outcomes;
- unresolved clarification structure;
- customer-facing business/clarification wording;
- structured validation and fail-safe behavior.

## Runtime flow

```text
customer turn
    -> autonomous LLM/tool loop
    -> trusted tool observations
    -> structured model draft
    -> deterministic outcome projection
    -> clarification canonicalization when needed
    -> deterministic customer renderer
    -> structural/evidence validator
       -> valid: return
       -> invalid: one no-tools repair -> validate again -> fail safe if still invalid
```

There is no hardcoded `order -> user -> policy -> refund` workflow. The model chooses actions; code decides whether an action may execute and what trusted evidence means.

## State and output

`AgentState` keeps short-term conversation messages, independent per-order `CaseState` objects, trusted tool history, sentiment/urgency, and runtime status. Restarting the process starts a new session; there is no persistent customer memory.

External output is a strict Pydantic `AgentResult`:

```text
status
reasoning_chain
action_taken
  tools_called
  cases[]
customer_response
```

Statuses: `COMPLETED`, `NEEDS_CLARIFICATION`, `FAILED_SAFE`.

Per-case decisions: `AUTO_REFUND_APPROVED`, `REJECTED`, `HUMAN_ESCALATION`, `NO_ACTION`.

## Tool guardrails

Only the four supplied tools are executable. Unknown tools or structurally invalid arguments are recorded and never dispatched as valid actions.

`process_refund` is blocked unless the same order already has trusted `check_return_policy` evidence with `eligible == true`. This is an execution precondition, not a duplicate policy engine.

Requested amount integrity:

- explicit requested amount -> preserve it;
- whole-order refund without a named amount -> use verified order total;
- never clip to `auto_refund_cap_usd` or `max_refundable_amount`;
- let `process_refund` approve, reject, or escalate the full request.

## Deterministic business projection

Trusted terminal evidence maps to public outcome fields:

| Trusted evidence | Decision | Refund fields |
| --- | --- | --- |
| `process_refund: APPROVED` | `AUTO_REFUND_APPROVED` | exact trusted amount/id |
| `process_refund: ESCALATION_REQUIRED` | `HUMAN_ESCALATION` | none |
| `process_refund: REJECTED` | `REJECTED` | none |
| policy `eligible=false` | `REJECTED` | none |
| terminal business error | `NO_ACTION` | none |

Model-authored terminal fields cannot override this mapping.

## Customer presentation boundary

Customer safety does not depend on matching English phrases or translated regex variants.

Terminal path:

```text
trusted evidence -> canonical CaseResult -> localized renderer
```

For example, `ESCALATION_REQUIRED` renders as additional review required and explicitly states that no refund was issued. No future-contact, payout, or timeline promise is added.

### Clarification path

A code review identified that free-form `NEEDS_CLARIFICATION` drafts could otherwise bypass terminal rendering. The final design closes that gap:

- the LLM still decides that clarification is needed;
- touched-but-unresolved cases become `NO_ACTION` with no terminal fields;
- no order identifier -> runtime asks for the order number;
- identified but unresolved order -> runtime asks for the refund/return reason;
- mixed resolved/unresolved turns render canonical resolved facts, then one clarification question.

Thus a clarification draft cannot fabricate a refund, rejection, escalation, internal-risk detail, timeline, or future promise.

## Confidentiality

Customer-facing output never exposes internal fraud/risk scores, flags, repeat-claim counts, LTV, raw internal field names, or exact thresholds. A risk-driven escalation is presented only as requiring additional review.

## Loops, retries, and failures

- deterministic tool calls are cached by normalized tool+arguments;
- repeated/no-progress cycles are bounded;
- maximum steps is a safety ceiling;
- only known transient LLM failures are retried with bounded backoff;
- tool business errors are data, not infrastructure retries;
- programmer/system failures fail closed;
- already-resolved trusted outcomes are preserved during fail-safe handling.

## Output repair

Malformed or structurally inconsistent final output gets at most one targeted repair call. Repair receives the validation problems, exposes no tools, may use native JSON Schema only on a verified no-tools route, and is projected/rendered/validated again. A second failure becomes `FAILED_SAFE`.

## Multi-order behavior

Orders remain independent within one turn. A failure or terminal result for one order does not erase another order's trusted outcome. Mixed resolved/unresolved turns can safely report resolved facts while asking for missing information.

## Provider abstraction

`LLMProvider.generate(...) -> ModelResponse` isolates provider-specific wire details.

Implemented adapters:

- OpenRouter — selected Stage 1 path;
- Groq — alternate adapter.

OpenAI-compatible message/tool conversion is shared. Normal autonomous calls keep tools available; provider-native response schema is reserved for supported no-tools repair calls.

## Observability and evaluation

Developer trace records model/provider, call kind, latency, tokens, retries, tool outcomes, cache/blocked events, duration, and steps. Secrets never enter the trace.

Business correctness is scored deterministically, not by an evaluator LLM. The live catalog checks decisions, policy verdicts, trusted refund status, exact authority-boundary amounts, forbidden refund execution, hallucinated cases, precondition ordering, runtime/loop safety, repair behavior, latency, tokens, tool count, and steps.

See `docs/VERIFICATION.md` for the reproducible gate.

## Non-goals

Stage 1 intentionally excludes LangGraph/LangChain orchestration, MCP, multiple agents, databases/Redis, persistent customer memory, an LLM-as-judge business authority, duplicate policy logic, speculative partial-refund policy, and production payment infrastructure.
