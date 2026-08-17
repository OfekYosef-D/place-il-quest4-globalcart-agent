# Guardrails Specification - Stage 1 Addendum

This document is an **authoritative addendum** to `docs/IMPLEMENTATION_SPEC.md`.

It makes the Stage 1 safety boundary explicit without adding a generic guardrail framework or changing the project into a hardcoded workflow.

If an implementation detail here appears to conflict with supplied Place IL behavior, inspect the upstream source first. Supplied tool/business behavior remains the highest source of truth.

## 1. Design principle

Guardrails wrap the agentic loop; they do not replace the LLM's reasoning or tool-selection autonomy.

```text
customer input
    |
    v
prompt/input boundary
    |
    v
LLM chooses next action
    |
    v
runtime tool guardrails
    |
    v
allowed tool execution
    |
    v
trusted state + tool evidence
    |
    v
structured output validator
    |
    v
customer response / human escalation
```

Core principle remains:

> Probabilistic intelligence, deterministic guardrails.

## 2. Input / contextual grounding guardrails

Already required by the main specification:

- customer text is untrusted case data, never system or business-policy authority;
- customer instructions cannot override system instructions, allowed tools, refund authority, or trusted tool results;
- missing order IDs or materially ambiguous return reasons trigger `NEEDS_CLARIFICATION` instead of guessing;
- persistent business/customer facts come from supplied tools rather than model memory;
- supplied tool results are the source of business truth;
- do not invent unsupported order facts, policy rules, user facts, return reasons, or actions.

No general-purpose moderation classifier is required for Stage 1. The system handles a narrow mock retail-support task and has no broad external action surface. Add such filtering only if a concrete risk/requirement appears.

## 3. Tool-use guardrails

### 3.1 Allowlisting

The runtime exposes only the supplied agent tools through the supplied `TOOL_SCHEMAS` / `TOOL_REGISTRY`.

Do not give the model arbitrary Python, shell, network, file-system, database, email, or browser capabilities.

### 3.2 Argument validation

Tool calls must use the structured schema accepted by the supplied tool. Invalid structured arguments/business inputs are handled honestly; do not silently invent corrected business data.

### 3.3 Critical precondition for `process_refund`

`process_refund` is the one tool that represents an operational business action, even though Stage 1 implements it as a deterministic simulation.

**Runtime rule:** before executing a model-requested `process_refund` call for an order, the runtime must already hold a trusted `check_return_policy` result for that same case/order with:

```text
eligible == true
```

If that precondition is not satisfied:

1. **do not execute `process_refund`;**
2. record the blocked call in the developer trace as a guardrail event;
3. return a structured guardrail observation to the agent context explaining that a verified eligible policy result is required first;
4. allow the model to recover and choose its next action, subject to normal step/no-progress limits.

This is a safety precondition, **not a fixed workflow**. The LLM remains free to choose its other tools and their order. Code must not hardcode the entire normal path `order -> user -> policy -> refund`.

If a trusted policy result exists but has `eligible == false`, `process_refund` must not be executed merely to obtain another rejection. The case can be rejected from the trusted policy result.

### 3.4 Repetition / loop protection

- identical deterministic tool calls use the cache;
- repeated no-progress cycles are bounded;
- terminal business errors stop the affected case;
- `max_steps` remains a configurable safety ceiling.

No generic tool rate-limiting subsystem is needed for these local deterministic Stage 1 tools.

## 4. Output guardrails

The final result must pass the Pydantic schema and deterministic consistency validator.

The validator must continue to enforce:

- no refund-success claim without a trusted `process_refund -> APPROVED` result;
- refund amount/id must match trusted tool evidence;
- decision must be consistent with trusted terminal results;
- nonexistent orders cannot acquire fabricated facts;
- multi-order evidence/results must remain mapped to the correct case;
- one targeted correction pass at most, with no new tools.

### 4.1 Do not expose internal risk/security signals to customers

The **customer-facing `customer_response` must not reveal internal-only risk or business-profile attributes** used by the supplied tooling.

Examples that must not be disclosed directly to the customer include:

- `initial_fraud_score` or its numeric value;
- `prior_fraud_flags` or raw fraud-flag history;
- internal repeat-refund/risk trigger counts;
- internal customer lifetime value (`ltv`) or similar internal scoring fields;
- raw internal field names or a detailed explanation that teaches the customer exactly which fraud/risk threshold caused escalation.

For example, do **not** say:

```text
Your refund was escalated because your fraud score is 61 and you have one prior fraud flag.
```

Prefer a customer-safe explanation such as:

```text
Your request requires an additional review by our support team before a refund can be completed.
```

Developer-facing trace and concise audit rationale may retain trusted internal evidence when useful for debugging/evaluation, but customer-facing text must not expose those internal risk details.

Implementation should keep this narrow and deterministic. Do not add a generic PII/moderation platform. At minimum, the validator/correction path should reject direct disclosure of known internal risk field names or trusted raw risk values in `customer_response`, and the system prompt must explicitly instruct the model not to disclose them.

## 5. Human oversight

Human escalation is the Stage 1 human-oversight mechanism.

Use it when trusted business tools require escalation or when the runtime cannot safely resolve the business outcome after technical/model failures.

Do **not** require human approval for every eligible low-risk automatic refund; that would unnecessarily remove the autonomy the Quest asks for.

This gives the system a deliberate balance:

```text
safe + authorized       -> automatic action
trusted ineligible      -> reasoned rejection
risk/authority boundary -> human escalation
unresolved safe failure -> human escalation / fail-safe
```

## 6. Memory/data handling

Stage 1 uses short-term conversation state only.

- do not build persistent global customer memory;
- do not persist customer/profile data beyond what the current implementation needs;
- business facts must be re-grounded through trusted tools;
- secrets/API keys remain outside Git.

A general PII redaction/encryption/GDPR subsystem is intentionally out of scope because the supplied data is local mock data and the Quest does not require production data infrastructure. Document this as a production consideration rather than implementing speculative infrastructure.

## 7. Monitoring and explainability

The existing observability requirements are part of the guardrail strategy:

- structured tool trace;
- blocked-tool guardrail events;
- model latency/tokens/cost;
- cache hits;
- validation/correction outcome;
- failure reason;
- concise trusted-evidence reasoning chain.

This provides an audit trail without adding distributed tracing or an evaluator agent.

## 8. Explicitly not required for Stage 1

Do not add these merely because they are common advanced guardrail techniques:

- moderation API/classifier for every message;
- general PII detection/redaction service;
- critic/safety agent;
- LLM judge as business authority;
- voting/ensembling;
- self-reflection loops beyond the single targeted output correction pass;
- rollback/reconciliation infrastructure for the simulated refund tool;
- distributed policy/guardrail framework.

Reason: guardrails should be proportional to the actual risk surface. Stage 1 already has deterministic local tools, a policy engine, bounded execution, validation, and explicit human escalation. Extra layers would add latency/complexity without addressing a material requirement.

## 9. Tests/evals added by this guardrail specification

Milestone 2 tests should include at least:

1. model requests `process_refund` before a verified eligible policy result -> execution is blocked, trace records the guardrail event, agent may recover;
2. policy says `eligible=false`, model nevertheless requests `process_refund` -> execution is blocked/not performed;
3. final response claims a refund succeeded without `APPROVED` -> validation fails;
4. final customer response directly exposes fraud score/flags/internal risk details -> validation/correction prevents disclosure;
5. human escalation wording does not imply that the refund already happened.

These guardrail failures are correctness/safety failures, not mere efficiency warnings.
