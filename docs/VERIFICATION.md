# Verification

This is the reproducible verification path for the final Stage 1 submission candidate.

## 1. Environment

Install dependencies and bootstrap the pinned Place IL starter kit:

```bash
python -m pip install -r requirements-dev.txt
bash scripts/bootstrap_upstream.sh
```

The upstream checkout lives under git-ignored `.vendor/` and must not be committed.

For live calls, create a local `.env` from `.env.example` and configure:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=your-local-secret
LLM_REASONING_EFFORT=
```

Never commit the key.

## 2. Deterministic gate

Run:

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

Current CI result on the post-presentation-refactor code path:

```text
project tests:        212 passed
upstream verifier:    33/33 passed
eval dry-run:         green
```

The dry-run contains:

```text
10 live agent scenarios
5 deterministic Scenario 8 tool probes
```

The ten live scenarios are the nine Stage 1 business entries plus a project-owned Hebrew end-to-end smoke based on the same Scenario 2 business truth.

## 3. Final live submission gate

After the deterministic gate is green, run exactly one complete live catalog:

```bash
python run_evals.py --repetitions 1
```

A submission candidate passes only if:

```text
critical failures = 0
release gate = PASS
```

`PASS_WITH_WARNING` may record bounded repair or harmless efficiency behavior. It must never be used to hide a wrong decision, hallucinated case, incorrect refund amount, refund-precondition bypass, runtime failure, or other critical error.

## 4. What the live catalog verifies

The deterministic scorer checks, among other things:

- final case decision;
- expected policy verdict;
- trusted `process_refund` status;
- exact refund request amount on authority-boundary cases;
- no refund execution where policy already rejects;
- no unexpected/hallucinated case IDs;
- refund-precondition ordering;
- runtime failure and bounded-loop behavior;
- tool/cache/block warnings;
- repair usage;
- latency, token count, tool count, and steps.

The Hebrew scenario additionally exercises the same autonomous tool path from a Hebrew customer request. Terminal response language is then rendered from the trusted structured outcome rather than from language-specific safety phrase matching.

## 5. Historical selected-model evidence

Before the final presentation refactor, OpenRouter/Qwen completed one full nine-scenario run with:

```text
9/9 agent scenarios passed
7 clean
2 warning
0 critical
5/5 Scenario 8 probes clean
```

Commit:

```text
06b0286ddf8309dbfa668e24543393e7a6099024
```

This evidence selected the provider/model but is intentionally not treated as the final-code release gate because customer-presentation runtime code changed afterward.

## 6. Manual CLI smoke

English authority escalation:

```bash
python run_agent.py --verbose --message "Order ORD-1002 arrived damaged and leaking. I paid $150 and want a full refund."
```

Expected business trace:

```text
get_order_details
check_return_policy -> ELIGIBLE
process_refund(amount=150) -> ESCALATION_REQUIRED
final decision -> HUMAN_ESCALATION
refund amount/id -> none
```

Hebrew authority escalation:

```bash
python run_agent.py --verbose --message "הזמנה ORD-1002 הגיעה פגומה ודולפת. שילמתי 150 דולר ואני רוצה החזר מלא."
```

Expected terminal customer wording:

```text
הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי.
```

## 7. Interactive conversation smoke

Run:

```bash
python run_agent.py --verbose
```

Example:

```text
You: המוצר שלי הגיע שבור
Agent: <targeted clarification asking for the missing order id>

You: ORD-1001
Agent: <continues using the same short-term session state>
```

Session memory is intentionally short-lived. Restarting the CLI starts a new conversation state.

## 8. Submission hygiene

Before merge/submission confirm:

- `.env` is not tracked;
- `.vendor/` is not tracked;
- `eval-results/` is not tracked;
- no API keys or secret-looking values are committed;
- no Place IL starter-kit source is copied into this repository;
- CI is green on the final branch;
- the final live catalog reports zero critical failures.
