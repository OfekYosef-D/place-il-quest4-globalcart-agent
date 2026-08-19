# Milestone 3 Verification and Live-Eval Runbook

This runbook is the remaining empirical part of Milestone 3. The repository contains the eval implementation; these steps must be executed in a local checkout that has the authorized upstream starter kit and Groq credentials.

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
- dry-run lists both configured candidates, all agent scenarios, and Scenario 8 direct tool probes;
- working tree remains clean.

## 3. Configure the secret locally

Create/update `.env` locally (already git-ignored):

```dotenv
GROQ_API_KEY=...
```

`run_evals.py` supplies model, reasoning effort, and per-candidate pricing from `evals/candidates.json`. No API key belongs in that file or in Git.

## 4. Optional one-scenario smoke test

Start with the happy path before the full matrix:

```bash
python run_evals.py --scenario s1_vip_damaged_approved --repetitions 1
```

Inspect the generated JSON/CSV in `eval-results/`. If the runner itself is misconfigured, fix the harness before spending calls on the full suite.

## 5. Run the comparison matrix

```bash
python run_evals.py --repetitions 3
```

The initial matrix is two candidates x nine agent-eval entries x three repetitions, plus five deterministic Scenario 8 tool probes. Scenario 5 is split into below/above-boundary cases while Scenario 7 keeps its two independent orders in one multi-order turn.

The process exits non-zero if any critical failure or source-behavior probe failure is observed.

## 6. Read the evidence

Generated artifacts include:

- timestamped JSON report with every run and issue;
- CSV of per-run correctness/safety/efficiency metrics;
- CSV candidate summary;
- `latest.json` convenience copy.

For each candidate review:

- `critical_failures` must be zero for release eligibility;
- prefer higher clean/stable performance before latency/cost;
- inspect warning codes instead of optimizing tool count blindly;
- inspect `observed_max_passing_steps` before changing `AGENT_MAX_STEPS`;
- do not tune the step ceiling to a single lucky run.

## 7. Finalize Milestone 3 from evidence

Only after the final matrix is green:

1. choose the release candidate based on reliability first, efficiency second;
2. set the release `LLM_MODEL` recommendation in documentation;
3. calibrate `AGENT_MAX_STEPS` from observed normal traces with conservative headroom, then rerun the full eval matrix;
4. record actual eval results in README / model-selection notes;
5. rerun pytest and the supplied 33 checks after any calibration change;
6. confirm no secrets, `.vendor` files, or generated `eval-results/` were committed.

Milestone 3 is complete only after those empirical steps, not merely because the eval harness exists.
