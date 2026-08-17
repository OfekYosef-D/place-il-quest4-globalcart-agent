# Qoder Handoff - Minimal-Interaction Workflow

The repository root contains `AGENTS.md`, which is the authoritative instruction file for the coding agent. The goal is that you should not have to restate the architecture manually in chat.

## First session

Open this repository in Qoder from the repository root.

Start with Plan mode:

```text
/plan
```

Then send:

```text
Read AGENTS.md, docs/IMPLEMENTATION_SPEC.md, docs/GUARDRAILS.md, docs/MILESTONES.md, and docs/UPSTREAM.md in full. Inspect the actual upstream Quest files after running the provided bootstrap if they are not present. Produce a plan for Milestone 0 and Milestone 1 only. Do not implement yet. Flag any conflict between our specification and the actual supplied Quest files. Do not introduce any architecture, guardrail subsystem, or business rule not explicitly justified by those sources.
```

Review the plan once. If it respects the repository facts and scope, leave Plan mode and run:

```text
/goal set Complete Milestone 0 and Milestone 1 from docs/MILESTONES.md exactly under AGENTS.md constraints. Read and preserve docs/GUARDRAILS.md for later runtime implementation, but do not start Milestone 2. Run all required verification/tests and stop only when the milestone acceptance criteria are satisfied or when a real blocker requires user input. Keep the implementation intentionally small and explainable.
```

## Second development goal

After reviewing Milestone 1 and understanding the main contracts:

```text
/goal set Complete Milestone 2 from docs/MILESTONES.md exactly under AGENTS.md constraints, including every required behavior and test in docs/GUARDRAILS.md. Do not start Milestone 3. Run all relevant tests and supplied verification. Preserve the autonomous agent requirement; do not replace it with a fixed workflow. Do not invent business rules or generic safety infrastructure. Stop when Milestone 2 acceptance criteria are satisfied or a real blocker requires user input.
```

## Third development goal

After reviewing the runtime/validator/CLI:

```text
/goal set Complete Milestone 3 from docs/MILESTONES.md exactly under AGENTS.md constraints. Run the full evaluation suite, including guardrail violation coverage from docs/GUARDRAILS.md, calibrate safety settings from observed traces, finish submission documentation, and stop when the Definition of Done in docs/IMPLEMENTATION_SPEC.md is satisfied. Do not start the optional UI unless explicitly asked.
```

## Review between goals

Use Qoder review functionality after each milestone. If `/review` is available in the installed version, use it; otherwise inspect the git diff and changed files directly.

For learning, focus on one layer after each milestone:

1. After Milestone 1: state, schemas, provider interface, tool adapter, trace/cache primitives.
2. After Milestone 2: agent loop, tool preconditions, validator, fail-safe behavior, clarification/multi-order handling, customer-safe output, CLI.
3. After Milestone 3: eval design, guardrail evidence, model/cost evidence, README and final tradeoffs.

A good learning checkpoint is to explain the code yourself before asking the agent for an explanation. Then compare your mental model with the implementation.

## Do not use one giant goal

A single “build everything” goal saves a few interactions but makes architecture drift harder to catch and makes the project harder to learn. Three bounded goals preserve most of the autonomy while still giving you meaningful review checkpoints.

## If Qoder proposes extra infrastructure

Reject or remove it unless a concrete upstream requirement justifies it. In particular, Stage 1 does not need LangGraph, MCP, multiple agents, a database, Redis, a message bus, distributed tracing, a generic moderation/PII platform, critic agents, voting/ensembling, or a second policy engine.
