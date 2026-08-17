# Upstream Verification Notes — Quest 4 Stage 1

Date verified: 2026-08-17

## Source checkout

- Official repository: `https://github.com/liraz-place-il/Quests`
- Pinned commit: `250a2e9e43189f8cf3e19260f633ed636996d68c` (sparse checkout of `Quest 4/Stage 1`)
- Local checkout: `.vendor/place-il-quests` (git-ignored, created by `scripts/bootstrap_upstream.sh`)
- Starter kit: `.vendor/place-il-quests/Quest 4/Stage 1/starter-kit`

The upstream material is read-only for this project. None of the supplied files
(starter kit, fixtures, scenarios, verification script) may be modified or
committed into this repository, per `docs/UPSTREAM.md` and the upstream IP notice.

## Supplied verification result

Command (Windows adaptation, see environment notes below):

```powershell
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
```

Result: **All 33 checks passed. The tool box is behaving as documented.**

## Facts verified against the supplied sources

Source facts (from `mock_services.py`, `data/`, `examples/scenarios.md`,
`examples/verify_scenarios.py`, and the Quest brief) that
`docs/IMPLEMENTATION_SPEC.md` §3 restates were all confirmed accurate:

- four agent tools: `get_order_details`, `get_user_profile`,
  `check_return_policy`, `process_refund`, exposed via `TOOL_SCHEMAS` /
  `TOOL_REGISTRY`;
- business failures are returned as `{"error": "<CODE>", ...}` data, not
  exceptions; only programmer errors (wrong types) raise `TypeError`;
- structured error codes: `ORDER_NOT_FOUND`, `USER_NOT_FOUND`,
  `INVALID_AMOUNT`, `INVALID_REASON`;
- policy verdicts: `ELIGIBLE`, `OUTSIDE_RETURN_WINDOW`,
  `NON_RETURNABLE_CATEGORY`, `ORDER_NOT_REFUNDABLE`;
- refund statuses: `APPROVED`, `REJECTED`, `ESCALATION_REQUIRED`;
- standard window 30 days / VIP 45 days; auto-refund cap 50 USD / VIP 75 USD;
- non-returnable categories: `digital_goods`, `perishables`, `gift_cards`;
- refundable statuses: `delivered`, `shipped`;
- escalation triggers: amount above cap (enforced in `process_refund`),
  fraud score >= 60, prior fraud flags > 0, >= 3 refund claims in trailing
  60 days;
- fixture reference date 2026-08-05 (overridable via `QUEST4_REFERENCE_DATE`);
- `process_refund` is simulated: no network, disk, or payment side effects,
  and the refund cap is enforced mechanically inside the tool;
- the FastAPI app in `api_docs/app.py` is a learning aid; the agent imports
  the local tool layer directly.

## Discrepancy notes (no behavioral conflicts found)

No substantive conflict was found between `docs/IMPLEMENTATION_SPEC.md` /
`docs/GUARDRAILS.md` and the actual supplied behavior. The following minor
documentation-level observations are recorded for transparency:

1. **`ORDER_NOT_REFUNDABLE` is a verdict, not an error code.** The upstream
   `mock_services.py` module docstring lists it under "Error codes", but the
   functions return it as `verdict` inside a successful policy result.
   Our spec (§21) already treats it as a verdict. No action needed.
2. **"At least 2 tools" rubric vs. terminal-error stop.** The Quest brief
   rubric asks for at least two tool calls, while AGENTS.md / spec §13 stop a
   case on trusted terminal errors such as `ORDER_NOT_FOUND` even if fewer
   than two tools were called. This is a deliberate engineering choice backed
   by the brief's own "stop condition" grading criterion and the
   hallucination-trap scenario (`ORD-2222`); it will be explained in the
   project README.
3. **Output shape.** The brief's example puts `decision` / `refund_amount` /
   `refund_id` directly under `action_taken` for a single case. Our spec uses
   `action_taken.cases: list[CaseResult]` so single- and multi-order runs
   share one schema. The brief explicitly allows any parseable format as long
   as `reasoning_chain`, `action_taken`, and `customer_response` exist at the
   top level.
4. **`TOOL_SCHEMAS` shape.** The supplied schemas are Anthropic-shaped
   (`input_schema`). For an OpenAI-compatible provider they are converted at
   the provider/tool boundary. The supplied objects are never modified.
5. **Refund-precondition guardrail interaction (important for Milestone 2).**
   `ORD-1002`, `ORD-1011`, and `ORD-1005` all return `eligible=true` from
   `check_return_policy`; their escalation is enforced *inside*
   `process_refund` (cap and risk checks). The runtime precondition "trusted
   eligible policy result before executing `process_refund`" therefore does
   not block the upstream-expected escalation flows and must not be
   implemented as a blanket block on escalation-prone orders.

## Environment notes (engineering, not source facts)

- This Windows machine has no `python3` alias and no working WSL bash;
  `python` and Git Bash (`C:\Program Files\Git\bin\bash.exe`) are used
  instead. The bootstrap script's final `python3` verify step was run
  manually with `python`.
- All date-dependent behavior is anchored to the fixture reference date, so
  results do not drift with the wall clock.
