# Verification

This is the reproducible verification path for the Stage 1 submission.

## 1. Environment

Install dependencies and bootstrap the pinned Place IL Stage 1 starter kit:

```bash
python -m pip install -r requirements-dev.txt
bash scripts/bootstrap_upstream.sh
```

The upstream checkout lives under git-ignored `.vendor/` and must not be committed.

For live calls, copy `.env.example` to `.env` and configure:

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

Final post-review deterministic result:

```text
project tests:        215 passed
upstream verifier:    33/33 passed
eval dry-run:         green
```

The dry-run contains 10 live agent entries — the nine Stage 1 business entries plus a project-owned Hebrew Scenario 2 smoke — and five deterministic Scenario 8 direct-tool probes.

## 3. Final live gate

Run exactly one complete live catalog after the deterministic gate is green:

```bash
python run_evals.py --repetitions 1
```

The final post-review run at runtime commit:

```text
385e307e3526cc906bddf3675d2677da6c4a480c
```

passed the submission gate:

```text
10/10 CLEAN_PASS
0 warnings
0 critical failures
release gate = PASS
5/5 Scenario 8 probes CLEAN_PASS
repair rate = 0%
average duration = 15.06 s
average total tokens = 9,912.5
average tool calls = 3.0
max passing steps = 4
```

Warnings are allowed only for bounded repair or harmless efficiency behavior; this final run had none. A warning can never be used to hide a wrong decision, hallucinated case, incorrect refund amount, refund-precondition bypass, runtime failure, or uncontrolled loop.

## 4. What the scorer verifies

The deterministic scorer checks, among other things:

- final case decision;
- expected policy verdict;
- trusted `process_refund` status;
- exact requested refund amount on authority-boundary cases;
- no refund execution where policy already rejects;
- no unexpected/hallucinated case IDs;
- refund-precondition ordering;
- runtime failure and bounded-loop behavior;
- tool/cache/block warnings;
- repair usage;
- latency, token count, tool count, and steps.

The Hebrew entry exercises the same autonomous tool path from a Hebrew customer request. Customer action facts are rendered from trusted structured outcomes rather than phrase matching.

## 5. Customer-presentation regression coverage

Terminal and clarification presentation are separately protected by deterministic tests.

The regression suite includes:

- incorrect free-form refund/future-contact language overwritten by trusted terminal rendering;
- the same safety invariant in Hebrew;
- approved responses using only trusted amount/refund ID;
- missing-order clarification rendered as a safe targeted question;
- identified-order clarification asking only for the missing return/refund reason;
- mixed resolved/unresolved turns preserving the resolved canonical fact while asking one clarification question;
- unresolved clarification cases canonicalized to `NO_ACTION` with no terminal fields.

This closes the code-review finding that a nonterminal free-form response could otherwise bypass the terminal presentation boundary.

## 6. Live evidence history

### Selected-model baseline

Commit `06b0286ddf8309dbfa668e24543393e7a6099024`:

```text
9/9 passed
7 clean
2 bounded-repair warnings
0 critical
5/5 Scenario 8 probes clean
```

### Post-terminal-renderer run

Commit `2be3a1a90114f40ce0bd96821b4ef65ceafb37dd`:

```text
10/10 CLEAN_PASS
0 warnings
0 critical failures
release gate PASS
5/5 Scenario 8 probes CLEAN_PASS
repair rate 0%
```

### Final post-review submission run

Commit `385e307e3526cc906bddf3675d2677da6c4a480c`:

```text
10/10 CLEAN_PASS
0 warnings
0 critical failures
release gate PASS
5/5 Scenario 8 probes CLEAN_PASS
repair rate 0%
```

The final run validates the clarification-safety fix that followed automated review and is the authoritative submission evidence.

## 7. Manual CLI smokes

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

Expected customer wording:

```text
הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי.
```

Clarification smoke:

```bash
python run_agent.py --verbose --message "My item arrived damaged and I want a refund."
```

Expected safe behavior: `NEEDS_CLARIFICATION` and one runtime-rendered question asking for the missing order number; no business outcome is claimed.

## 8. Interactive conversation

```bash
python run_agent.py --verbose
```

Example:

```text
You: המוצר שלי הגיע שבור
Agent: מה מספר ההזמנה שברצונך שנבדוק?

You: ORD-1001
Agent: <continues from the same short-term session state>
```

Restarting the process starts a new session by design.

## 9. Submission hygiene

Before submission confirm:

- `.env` is not tracked;
- `.vendor/` is not tracked;
- `eval-results/` is not tracked;
- no API keys or secret-looking values are committed;
- no Place IL starter-kit source is copied into this repository;
- CI is green on the final branch/main;
- the final live catalog reports zero critical failures;
- the submission GitHub repository is public/readable by evaluators.
