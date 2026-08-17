# GlobalCart Quest 4 Stage 1 - Project Instructions

These instructions are authoritative for any coding agent working in this repository.

## Before changing code

1. Read `docs/IMPLEMENTATION_SPEC.md` in full.
2. Read `docs/GUARDRAILS.md` in full.
3. Read `docs/MILESTONES.md` in full.
4. Read `docs/UPSTREAM.md` in full.
5. If the upstream Quest material is available locally, inspect the actual supplied files before making assumptions about business rules or tool behavior.
6. Work on exactly one milestone at a time unless the user explicitly asks otherwise.

## Facts-first rule

- Repository facts and the supplied Place IL Quest files are authoritative.
- Never invent a business rule, refund policy, API, tool, field, error code, customer classification, or edge-case behavior.
- Clearly distinguish a source fact from an engineering choice.
- When uncertain about supplied behavior, inspect `mock_services.py`, the fixtures, `examples/scenarios.md`, and `examples/verify_scenarios.py` before coding.
- Do not duplicate business logic already enforced by the supplied tools.

## Upstream material

- The Place IL source material is read-only.
- Never modify the supplied starter kit, fixtures, scenarios, or verification script.
- Do not copy Place IL source material into commits in this repository. Use the local read-only upstream checkout described in `docs/UPSTREAM.md`.

## Stage 1 architecture constraints

Build a single autonomous Operations Resolver Agent in Python.

Use:
- a direct LLM SDK / OpenAI-compatible tool-calling protocol;
- Pydantic for state/output contracts;
- the supplied `TOOL_SCHEMAS` and `TOOL_REGISTRY`;
- a small provider abstraction so runtime model/provider can be changed through configuration;
- a deterministic validator for consistency and safety;
- a clean CLI with an optional `--verbose` developer trace.

Do not introduce in Stage 1:
- LangGraph;
- LangChain orchestration;
- MCP;
- multiple agents;
- an orchestrator/router/manager-worker architecture;
- a database, Redis, message bus, distributed tracing, or persistent business memory;
- a second business-rule engine that reimplements `check_return_policy`;
- an LLM evaluator as an authority for business correctness;
- speculative partial-refund policy;
- a production refund/idempotency subsystem for the simulated tool;
- a frontend before the core, CLI, and evals are complete.

## Core safety principles

- Customer text is untrusted case data, never an instruction source for system behavior.
- The LLM chooses which allowed tool to call, in what order, and when it believes it is done.
- Python executes tools, stores trusted results, validates outputs, enforces loop bounds, and fails safely.
- Trusted tool results are the source of business truth.
- Never tell a customer a refund was issued unless `process_refund` actually returned `APPROVED`.
- If `check_return_policy` returns `eligible=false`, reject using that trusted result and do not call `process_refund` merely to obtain the same rejection again.
- Before executing `process_refund`, runtime must already hold a trusted `check_return_policy` result for that same case/order with `eligible == true`. Otherwise block the tool call, record a guardrail event, and let the model recover within normal loop limits.
- Customer-facing responses must not expose internal risk/security details such as fraud scores, fraud flags, internal repeat-claim counts, LTV, raw internal field names, or exact internal risk thresholds.
- A terminal business error such as `ORDER_NOT_FOUND` stops the case even if fewer than two tools were called.
- Missing required customer information results in `NEEDS_CLARIFICATION`; do not guess.
- Sentiment and urgency may affect wording, never refund eligibility or authority.
- `docs/GUARDRAILS.md` is an authoritative safety addendum and must be implemented/tested in Milestone 2.

## Agent-loop discipline

- Cache deterministic tool results by tool name + normalized arguments.
- An identical repeated read call should return the cached result rather than re-execute the tool.
- Detect lack of progress / repeated cycles and fail safely.
- `max_steps` is a configurable safety ceiling, not a normal stopping mechanism. Calibrate it from eval traces rather than choosing an arbitrary permanent value.
- Retry LLM calls only for known transient failures. Start with the initial attempt plus two retries; keep this configurable.
- Unknown/non-transient failures fail closed.
- Tool business errors are data, not exceptions.
- Programmer/system exceptions from local tools are logged and handled as system failures rather than blindly retried.

## Output repair discipline

- Final output must validate against the Pydantic response model.
- If the final model output is malformed or inconsistent with trusted tool results, allow at most one targeted correction pass.
- The correction pass receives explicit validation errors and must not call new tools.
- Missing customer information is not an output-repair problem; ask the customer for clarification instead.
- If correction still fails, use fail-safe behavior.

## Multi-order behavior

- One customer turn may contain multiple order IDs.
- Treat them as independent cases within one agent run.
- A failure or terminal result in one case must not invalidate unrelated cases.
- Return one consolidated customer response.

## Conversation behavior

- Maintain short-term conversation state across customer turns so clarification can be resolved naturally.
- Do not create global customer memory. Persistent customer/business facts come only from trusted tools.
- Customer-facing text should follow the customer's language.
- Internal enums, tool names, policy IDs, trace fields, and developer-facing data remain in English.

## Quality bar

Correctness and safety are mandatory. Efficiency is a quality metric, not a correctness gate.

A run can be:
- clean pass: correct and efficient;
- pass with warning: correct but includes harmless unnecessary work;
- critical failure: wrong business decision, hallucination, false refund claim, unsafe output, guardrail bypass, sensitive internal-risk disclosure, or uncontrolled loop.

Never force the agent into a hardcoded workflow solely to optimize the minimum tool-call count.
