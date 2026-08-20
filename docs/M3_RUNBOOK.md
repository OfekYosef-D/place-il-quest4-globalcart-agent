# Milestone 3 Verification and Live-Eval Runbook

This runbook is the remaining empirical part of Milestone 3. The repository contains the runtime/eval implementation; live calls require only a local OpenRouter key.

## 1. Synchronize safely

The repository may have been updated directly on GitHub. Before testing:

```bash
git status --short
git fetch origin
git pull --ff-only origin main
git rev-parse HEAD
git rev-parse origin/main
```

Do not reset, rebase, force-pull, or discard uncommitted work just to synchronize. If the tree is dirty or `--ff-only` cannot proceed, stop and inspect the exact local changes first.

## 2. Verify deterministic behavior before spending API calls

```bash
python -m pytest tests -q
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
python run_evals.py --dry-run
```

Required before live evals:

- full pytest suite green;
- supplied verifier: 33/33;
- dry-run lists the configured OpenRouter candidate, all agent scenarios, and Scenario 8 direct tool probes;
- working tree remains clean.

## 3. Configure the secret locally

Create/update `.env` locally (already git-ignored):

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=...
LLM_REASONING_EFFORT=
```

Never paste the key into Git, logs, screenshots, or eval config files.

## 4. Run the two authority-boundary smoke scenarios first

These are the cases that exposed the silent partial-refund bug in the earlier model setup. They are the fastest meaningful release gate for the new provider/model:

```bash
python run_evals.py \
  --scenario s2_standard_above_cap_escalates \
  --scenario s5b_boundary_52_escalates \
  --repetitions 1 \
  --skip-tool-probes
```

Required outcomes:

- `ORD-1002`: `process_refund` requested with `150.0`, trusted result `ESCALATION_REQUIRED`, final decision `HUMAN_ESCALATION`;
- `ORD-1011`: `process_refund` requested with `52.0`, trusted result `ESCALATION_REQUIRED`, final decision `HUMAN_ESCALATION`;
- no `REFUND_REQUEST_AMOUNT_MISMATCH` or `REFUND_REQUEST_MISSING`;
- no false refund-success claim.

If either scenario is a `CRITICAL_FAILURE`, stop. Inspect the generated report before spending more calls.

## 5. Run the full catalog once

Only after the smoke is green:

```bash
python run_evals.py --repetitions 1
```

This is nine agent-eval entries plus five deterministic Scenario 8 tool probes. Scenario 5 is split into below/above-boundary cases while Scenario 7 keeps its two independent orders in one multi-order turn.

The process exits non-zero if any critical failure or source-behavior probe failure is observed.

## 6. Measure stability only after correctness

If all nine live scenarios are correct once and time/budget permits:

```bash
python run_evals.py --repetitions 3
```

Do not use repetitions to compensate for a systematic correctness failure. Fix the demonstrated issue or change the candidate first.

## 7. Read the evidence

Generated artifacts in git-ignored `eval-results/` include:

- timestamped JSON report with every run and issue;
- CSV of per-run correctness/safety/efficiency metrics;
- CSV candidate summary;
- `latest.json` convenience copy.

For each candidate review:

- `critical_failures` must be zero for release eligibility;
- prefer correctness/stability before latency/cost;
- inspect warning codes rather than optimizing tool count blindly;
- inspect `observed_max_passing_steps` before changing `AGENT_MAX_STEPS`;
- do not tune the step ceiling to one lucky run.

OpenRouter may route the same model through different inference providers, whose prices can differ. The active candidate therefore leaves configured per-million pricing unset until a backend is intentionally pinned; do not present an inaccurate estimated-cost number as measured spend.

## 8. Finalize Milestone 3 from evidence

Only after the final live suite is green:

1. record the model/provider and generated report evidence in README / model-selection notes;
2. calibrate `AGENT_MAX_STEPS` only if passing traces justify a change;
3. rerun pytest and the supplied 33 checks after any calibration change;
4. confirm no secrets, `.vendor` files, or generated `eval-results/` were committed;
5. perform the final IP/public-repository check before changing repository visibility.

Milestone 3 is complete from measured evidence, not merely because the harness exists.
