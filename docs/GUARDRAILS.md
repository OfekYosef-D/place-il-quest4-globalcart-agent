# Guardrails

This document is the compact safety contract for the Stage 1 agent.

## 1. Trust boundary

Customer messages are untrusted case data. They cannot:

- override system instructions;
- add tools;
- bypass policy/tool checks;
- grant refund authority;
- make an unconfirmed action true.

Trusted business facts come only from supplied tool results stored in runtime state.

## 2. Tool boundary

Only these supplied tools may execute:

- `get_order_details`
- `get_user_profile`
- `check_return_policy`
- `process_refund`

Unknown tools and invalid arguments are recorded and never dispatched as valid actions.

## 3. Refund execution precondition

Runtime blocks `process_refund` unless the same order already has trusted `check_return_policy` evidence with `eligible == true`.

This rule prevents the model from using the irreversible tool before policy eligibility has been established, while leaving policy calculation inside the supplied tool.

## 4. Refund amount integrity

A model may not turn an authority limit into an invented partial refund.

- explicit requested amount -> preserve it;
- full-order request with no amount -> use verified order total;
- never clip to an automatic cap;
- let `process_refund` decide approve/reject/escalate.

The eval scorer pins `$150` for `ORD-1002` and `$52` for `ORD-1011` at the actual tool-call boundary.

## 5. Terminal business truth

Terminal decisions are projected from trusted evidence instead of accepted from model wording.

| Trusted evidence | Canonical decision | Refund fields |
| --- | --- | --- |
| `process_refund: APPROVED` | `AUTO_REFUND_APPROVED` | exact trusted amount/id |
| `process_refund: ESCALATION_REQUIRED` | `HUMAN_ESCALATION` | none |
| `process_refund: REJECTED` | `REJECTED` | none |
| policy `eligible=false` | `REJECTED` | none |
| terminal business error | `NO_ACTION` | none |

A model-provided terminal decision cannot override this mapping.

## 6. Terminal customer presentation

Customer-facing terminal action claims are rendered from canonical structured outcomes.

The safety rule is **not** implemented as a dictionary of English forbidden phrases or translated regex variants. That approach does not generalize across languages or paraphrases.

Instead:

```text
trusted evidence -> canonical outcome -> localized terminal wording
```

Therefore:

- no refund success can be stated unless the canonical outcome is approved;
- escalation never includes a refund amount/id;
- escalation never promises future contact, payout, or a processing timeline;
- rejection wording comes from trusted policy outcome;
- nonexistent orders ask the customer to confirm the identifier;
- English and Hebrew use the same structured business truth.

## 7. Clarification

If required customer information is missing or ambiguous, the agent returns `NEEDS_CLARIFICATION` and asks one targeted question.

Clarification must not guess an order ID, reason, policy result, or completed action.

No terminal business action has been established at this stage, so clarification wording can remain conversational rather than using the terminal renderer.

## 8. Confidentiality

Never expose internal risk/profile information in customer-facing output, including:

- fraud/risk scores;
- fraud flags;
- repeat-claim counts;
- lifetime value/LTV;
- raw internal field names;
- exact internal thresholds.

A risk-triggered escalation is presented simply as additional review required.

## 9. Tool-result handling

Structured business errors from supplied tools are data and are not blindly retried.

Terminal errors such as `ORDER_NOT_FOUND` stop that case even if fewer than two tools were used. Continuing merely to satisfy a tool-count target would increase hallucination risk.

Programmer/system failures are treated separately and fail closed.

## 10. Loops, caching, and retries

- deterministic tool calls are cached by normalized tool+arguments;
- repeated/no-progress cycles are bounded;
- maximum steps is a safety ceiling;
- only known transient LLM failures are retried;
- retry count/backoff are bounded and configurable;
- tool business failures are not infrastructure retries.

## 11. Structured final output

Final output must parse as `AgentResult`.

Runtime verifies structured evidence consistency, including:

- grounded case IDs;
- no duplicate cases;
- resolved current-turn cases are not omitted;
- exact factual `tools_called` set;
- approved refund amount/id exactly match trusted evidence;
- escalation/rejection/error decisions match trusted evidence;
- refund fields never appear without trusted approval;
- `COMPLETED` does not contain an unresolved business case.

The validator intentionally does not infer natural-language semantics from phrase lists; terminal customer semantics are enforced by rendering from structured outcomes.

## 12. Repair and fail-safe

Malformed or structurally inconsistent final model output gets at most one no-new-tools repair pass.

If that correction fails, the run returns `FAILED_SAFE`. Trusted outcomes already established for other cases are preserved.

A fail-safe response never fabricates a successful refund.

## 13. Verification invariant

A change is not submission-ready unless:

1. project tests pass;
2. the pinned supplied verifier passes all 33 checks;
3. eval dry-run is valid;
4. the final live catalog produces zero critical failures.

See `docs/VERIFICATION.md` for commands.
