# GlobalCart Operations Resolver Agent - Place IL Quest 4 Stage 1

Status: **Milestones 0-2 complete (bootstrap, tool/config foundation, and the autonomous agent runtime with guardrails, validator, repair, and CLI); Milestone 3 (evals and polish) not started.**

This repository is prepared as an AI-development handoff for a single autonomous Operations Resolver Agent. The project is intentionally designed to be small, safe, measurable, and easy to explain in an interview.

## Start here

Human developer:
1. Read `docs/IMPLEMENTATION_SPEC.md` for the agreed architecture.
2. Read `docs/MILESTONES.md` for build order and acceptance criteria.
3. Read `docs/QODER_START.md` for the low-interaction Qoder workflow.

Coding agent:
- `AGENTS.md` is authoritative and must be read before changes.

## Architecture direction

```text
Customer / CLI
      |
      v
Single OperationsResolverAgent
      |
      +--> LLM provider abstraction
      +--> typed state
      +--> supplied tool schemas/registry
      +--> deterministic consistency validator
      +--> trace / token / latency / cost metrics
      v
Structured AgentResult
```

Core principle:

> Probabilistic intelligence, deterministic guardrails.

The model controls agentic tool selection and stopping decisions. Python owns execution, trusted state, validation, bounded loops, and fail-safe behavior.

## Upstream Quest material

The Place IL source repository contains an explicit intellectual-property notice. This repository does not republish the supplied challenge/starter-kit files. See `docs/UPSTREAM.md` for the pinned read-only local-development workflow.

## Implementation order

- Milestone 0: verify source facts and 33 supplied checks.
- Milestone 1: contracts/foundation.
- Milestone 2: real agent loop, safety, validator, CLI.
- Milestone 3: full evals and portfolio polish.
- Optional: small chat UI only after the core is green.

## Non-goals for Stage 1

No LangGraph, MCP, multi-agent system, database, message bus, elaborate memory service, duplicate policy engine, or production refund infrastructure unless later requirements explicitly demand them.
