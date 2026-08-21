# Implementation Specification

This document describes the final Stage 1 engineering contract for the GlobalCart Operations Resolver Agent.

## 1. Goal

Build one autonomous operations agent that can understand a retail-support request, choose and call the supplied tools, reach a business outcome, and return a parseable customer-facing result.

The design must remain small enough to explain and extend into Stage 2 without introducing multi-agent or infrastructure complexity in Stage 1.

## 2. Source of truth

Business facts come only from the supplied GlobalCart tool layer:

- `get_order_details(order_id)`
- `get_user_profile(user_id)`
- `check_return_policy(order_id, reason)`
- `process_refund(order_id, amount, reason)`

The runtime uses the supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY`; it does not reimplement return windows, refund caps, risk rules, or eligibility.

Business-error payloads are data, not retryable exceptions. Programmer/system failures fail closed.

## 3. Authority boundary

### LLM responsibilities

The model owns probabilistic judgment:

- understand the customer request;
- identify whether clarification is needed;
- choose the next supplied tool;
- choose tool ordering;
- decide when enough evidence has been gathered;
- provide a concise developer-facing reasoning summary;
- assess sentiment/urgency for tone only.

### Runtime responsibilities

Python owns deterministic authority:

- execute only allowed tools;
- validate tool arguments;
- cache deterministic calls;
- store trusted state;
- enforce refund preconditions;
- bound retries and loop progress;
- project terminal business outcomes from trusted evidence;
- render terminal customer-facing business facts;
- validate the structured result;
- fail safely.

The model can choose an action; it cannot redefine the result of an executed business tool.

## 4. Runtime flow

```text
customer turn
    |
    v
LLM + supplied tool schemas
    |
    +--> tool call? --> guarded executor --> trusted observation --> loop
    |
    `--> final structured draft
                |
                v
       parse AgentResult
                |
                v
       project trusted outcomes
                |
                v
       render terminal customer facts
                |
                v
       structural/evidence validation
                |
       valid ----+---- invalid
        |                  |
        v                  v
      return       one no-tools repair
                           |
                           v
                    project + render + validate
                           |
                    valid --+-- invalid
                      |          |
                      v          v
                    return    FAILED_SAFE
```

## 5. State

`AgentState` is short-term conversation state. It persists only inside the active CLI/session and contains:

- canonical conversation messages;
- per-order `CaseState` entries;
- trusted tool history;
- sentiment/urgency metadata;
- current step count/status;
- final/failure metadata.

There is no persistent customer memory or database.

`CaseState` holds trusted evidence such as verified order/profile data, policy result, refund result, terminal error, and decision state.

## 6. Structured result

The external contract is `AgentResult`:

```text
status
reasoning_chain
action_taken
  tools_called
  cases[]
customer_response
```

Top-level status:

- `COMPLETED` - the current business case(s) reached trusted terminal outcomes;
- `NEEDS_CLARIFICATION` - required customer information is still missing/ambiguous;
- `FAILED_SAFE` - a technical/model/safety failure prevented a safe normal completion.

Per-case decision:

- `AUTO_REFUND_APPROVED`
- `REJECTED`
- `HUMAN_ESCALATION`
- `NO_ACTION`

## 7. Tool execution guardrails

### Allowlist

Only the four supplied tools are executable. Unknown tool names are recorded and never dispatched.

### Refund precondition

`process_refund` is blocked unless the same order already has trusted `check_return_policy` evidence with `eligible == true`.

This is an execution guardrail, not a duplicate policy engine.

### Requested amount discipline

The model must preserve the customer's requested/full refund amount:

- explicit amount -> pass that amount;
- full/whole-order refund with no explicit amount -> use verified order total;
- never clip to `auto_refund_cap_usd` or `max_refundable_amount`.

`process_refund` is the authority that approves, rejects, or escalates the requested amount.

## 8. Deterministic terminal outcome projection

When trusted evidence is terminal, the model's terminal outcome fields are not authoritative.

The projector maps trusted evidence as follows:

- `process_refund.status == APPROVED`
  - `AUTO_REFUND_APPROVED`
  - exact trusted `approved_amount`
  - exact trusted `refund_id`
- `process_refund.status == ESCALATION_REQUIRED`
  - `HUMAN_ESCALATION`
  - no refund amount/id
  - trusted escalation reasons
- `process_refund.status == REJECTED`
  - `REJECTED`
- `check_return_policy.eligible == false` with no refund execution
  - `REJECTED`
  - exact trusted policy verdict
- terminal business error such as `ORDER_NOT_FOUND`
  - `NO_ACTION`
  - exact error code

For fully resolved touched cases, the top-level status becomes `COMPLETED` regardless of a conflicting model label.

## 9. Customer presentation boundary

Terminal business claims are rendered from the projected structured result, not interpreted from free-form model wording.

This is deliberately language-neutral at the business layer:

```text
trusted outcome
     -> canonical CaseResult
     -> localized renderer
```

The renderer currently supports English and Hebrew customer presentation. Language selection is presentation routing; it is not a business or safety decision.

Examples:

`HUMAN_ESCALATION`:

```text
EN: Order ORD-1002: additional review is required. No refund has been issued.
HE: הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי.
```

The system therefore does not depend on regex lists for phrases such as "will contact", "refund approved", or their translations.

Clarification remains conversational because a clarification turn has not yet established a terminal business action. The model is instructed to ask one targeted question and not invent facts.

## 10. Confidentiality

Internal risk/profile information is never a valid customer-facing reason. Fraud scores, fraud flags, repeat-claim counts, LTV, raw internal fields, and exact internal thresholds stay internal.

When a trusted tool requires escalation, customer presentation says only that additional review is required.

## 11. Caching and progress

Deterministic calls are cached by tool name plus normalized arguments. Identical repeated calls can be cache-served rather than executed again.

The runtime detects repeated/no-progress cycles and has a configurable maximum step ceiling. `max_steps` is a safety bound, not the normal stopping strategy.

## 12. Retry policy

LLM retries are limited to known transient failures. The configured default is the initial call plus at most two retries with short bounded backoff.

Business errors from supplied tools are not retried as infrastructure failures.

Unknown/non-transient provider failures fail closed.

## 13. Output repair

If the model's final structured draft cannot be parsed or is inconsistent with trusted structured evidence:

1. exactly one targeted repair call is allowed;
2. the repair gets the validation errors and existing trusted observations;
3. no tools are exposed during repair;
4. provider-native JSON Schema may be used only on a route whose no-tools structured-output capability is supported;
5. the repaired result is projected/rendered/validated again;
6. a second failure becomes `FAILED_SAFE`.

Repair is never used to gather missing business evidence.

## 14. Multi-order behavior

A customer turn may include multiple order IDs. Cases are independent inside one run:

- each order retains its own trusted evidence/outcome;
- one terminal failure does not erase another order's resolved result;
- final response consolidates the cases;
- current-turn `tools_called` reflects factual executed/cache-served tools only.

## 15. Provider abstraction

`LLMProvider.generate(messages, tools, ...) -> ModelResponse` isolates the runtime from provider-specific wire details.

Implemented adapters:

- OpenRouter (selected release path)
- Groq (alternate adapter)

OpenAI-compatible message/tool conversion is shared rather than duplicated.

Normal autonomous tool calls do not rely on simultaneous tool-calling plus `response_format`. Native schema is reserved for supported no-tools repair calls.

## 16. Observability

The developer trace records:

- model/provider;
- call type (tool/final/repair);
- latency;
- token usage;
- retries;
- tool arguments/outcome summaries;
- cache/blocked/error events;
- total run duration and step count.

No secret/API key enters the trace.

## 17. Evaluation

Business correctness is scored deterministically; there is no evaluator LLM.

The live catalog checks:

- expected decision and policy verdict;
- trusted refund status;
- exact requested amount on authority-boundary cases;
- forbidden refund execution on ineligible cases;
- hallucinated/missing cases;
- refund-precondition bypass;
- runtime failure/loop safety;
- repair/caching/tool-efficiency warnings;
- latency, tokens, steps, and tool count.

The catalog contains the nine Stage 1 agent cases plus a project-owned Hebrew end-to-end smoke using the same Scenario 2 business truth. Scenario 8 bad-input behavior is covered by five deterministic direct-tool probes.

## 18. Non-goals

Stage 1 intentionally excludes:

- LangGraph/LangChain orchestration;
- MCP;
- multiple agents or a manager/router hierarchy;
- database/Redis/message bus;
- persistent customer memory;
- LLM-as-judge business authority;
- duplicated policy rules;
- speculative partial-refund policy;
- production payment/idempotency infrastructure;
- frontend work.
