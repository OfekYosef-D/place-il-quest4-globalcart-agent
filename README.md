# GlobalCart Operations Resolver Agent

Place IL Quest 4, Stage 1: a single autonomous customer-operations agent for the fictional GlobalCart retail environment.

**Status:** final verification candidate. The current branch passes the deterministic project suite, the supplied 33-check verifier, and the eval dry-run. One final live catalog run is intentionally left as the submission gate after the latest customer-presentation hardening.

> **Probabilistic intelligence, deterministic guardrails.**
>
> The LLM decides what to investigate and which tool to call. Trusted code decides what actually happened. Customer-facing terminal action facts are rendered from that trusted outcome.

## Why this design

The project is deliberately small: one agent, four supplied tools, short-term conversation state, and no framework-heavy orchestration. The interesting engineering problem is the authority boundary between probabilistic reasoning and irreversible business claims.

```text
Customer / CLI
      |
      v
Autonomous LLM loop
  understand request
  choose next tool
  decide when enough evidence exists
      |
      v
Guarded ToolExecutor
      |
      v
Supplied GlobalCart tools
      |
      v
Trusted AgentState
      |
      v
Deterministic outcome projector
      |
      v
Canonical terminal CaseResult
      |
      v
Deterministic localized renderer
      |
      v
Structured AgentResult
```

There is no hardcoded `order -> user -> policy -> refund` workflow. The model remains autonomous about tool selection and ordering. Runtime code owns execution authority, trusted state, loop bounds, terminal business truth, and terminal customer-facing action facts.

## Authority boundary

The LLM owns:

- understanding the customer's issue;
- choosing which supplied tool to call next;
- tool ordering;
- deciding when clarification is needed;
- sentiment/urgency assessment for tone;
- developer-facing reasoning summary.

Deterministic code owns:

- the tool allowlist and execution;
- cached trusted tool results;
- refund execution preconditions;
- exact terminal decisions, refund amounts, refund IDs, and error codes;
- final tool-call accounting;
- terminal customer-facing business wording;
- retry, no-progress, and max-step bounds;
- fail-safe behavior.

The key rule is simple: **language is presentation; business truth is structured.**

## Terminal customer responses are not free-form business authority

An earlier version validated customer text with English phrase patterns such as future-contact or refund-success wording. A Hebrew live smoke exposed the limitation: business behavior was correct, but an unsupported future-contact promise could be phrased differently and bypass an English-only wording check.

The final design does not try to enumerate every sentence a model could produce.

Once trusted tools resolve a case, the runtime projects a canonical `CaseResult` and renders the terminal customer response from that structured outcome. English and Hebrew are presentation variants of the same internal facts.

For example, a trusted `ESCALATION_REQUIRED` result becomes:

```text
decision = HUMAN_ESCALATION
refund_amount = null
refund_id = null
```

English presentation:

```text
Order ORD-1002: additional review is required. No refund has been issued.
```

Hebrew presentation:

```text
הזמנה ORD-1002: נדרשת בדיקה נוספת. לא בוצע החזר כספי.
```

No phrase-level detector is needed to know whether the refund happened: the structured tool result already answers that question.

Clarification turns remain conversational because no terminal business action has been established yet.

## Business and safety invariants

- Supplied tool results are the only source of business truth.
- Customer text is untrusted case data, never policy or system authority.
- Only the four supplied tools are exposed.
- `process_refund` cannot execute unless the same order already has trusted `check_return_policy -> eligible=true` evidence.
- `eligible=true` does not mean a refund occurred; only `process_refund` determines the operational result.
- A requested/full refund amount is preserved exactly. Automatic authority caps are never used to silently invent a partial refund.
- Ineligible policy results terminate without calling `process_refund` merely to obtain another rejection.
- `ORDER_NOT_FOUND` terminates that case without invented order facts.
- `APPROVED`, `ESCALATION_REQUIRED`, `REJECTED`, ineligible-policy, and terminal-error outcomes are projected deterministically.
- Internal risk/profile signals never become customer-facing explanations.
- Repeated deterministic calls are cache-served and bounded by no-progress protection.
- LLM calls retry only known transient failures and remain bounded.
- Malformed/inconsistent final structured output gets at most one no-new-tools correction pass.
- Technical failures fail closed while preserving any trusted outcomes already established.

See `docs/GUARDRAILS.md` for the compact safety contract.

## Structured output

Every run returns the same Pydantic contract:

```text
AgentResult
  status
  reasoning_chain
  action_taken
    tools_called
    cases[]
  customer_response
```

The Quest-required top-level fields remain present: `reasoning_chain`, `action_taken`, and `customer_response`.

`CaseResult` uses one of:

```text
AUTO_REFUND_APPROVED
REJECTED
HUMAN_ESCALATION
NO_ACTION
```

## Provider architecture

The agent depends on a small `LLMProvider` abstraction. The selected Stage 1 path is:

```text
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
```

`GroqProvider` remains as an alternate adapter, demonstrating that the runtime is not tied to one gateway/model.

Normal autonomous calls keep tools available and do not combine tool calling with `response_format`. If final structured content needs the single bounded repair pass, a provider/model with verified schema support may use the Pydantic-derived JSON Schema on that no-tools repair call.

See `docs/MODEL_SELECTION.md` for measured model-selection evidence and caveats.

## Upstream starter kit

The Place IL starter kit is **not vendored** into this repository. The project bootstraps a pinned authorized checkout under the git-ignored `.vendor/` directory:

```bash
bash scripts/bootstrap_upstream.sh
```

Pinned upstream commit:

```text
250a2e9e43189f8cf3e19260f633ed636996d68c
```

See `docs/UPSTREAM.md` for details.

## Install

```bash
python -m pip install -r requirements-dev.txt
bash scripts/bootstrap_upstream.sh
```

Copy `.env.example` to `.env` and set the local OpenRouter key:

```dotenv
LLM_PROVIDER=openrouter
LLM_MODEL=qwen/qwen3.5-397b-a17b
OPENROUTER_API_KEY=your-local-secret
LLM_REASONING_EFFORT=
```

`.env`, `.vendor/`, and generated `eval-results/` are git-ignored.

## Run

One-shot:

```bash
python run_agent.py --message "My order ORD-1001 arrived damaged."
```

Developer trace:

```bash
python run_agent.py --verbose --message "My order ORD-1002 arrived damaged. I paid $150 and want a full refund."
```

Interactive short-term conversation:

```bash
python run_agent.py --verbose
```

Hebrew is supported at the customer boundary; internal enums, tool names, policy IDs, and traces remain in English.

## Verification

Current deterministic gate on the final-presentation code path:

```text
project tests:            212 passed
supplied verifier:        33/33 passed
eval catalog dry-run:     green
live catalog entries:     10 (9 business scenarios + Hebrew smoke)
Scenario 8 tool probes:   5
```

The lower project-test count versus earlier development snapshots is intentional: wording/regex-specific tests were removed when phrase matching was replaced by deterministic terminal rendering, and equivalent structural/rendering invariants are tested directly.

Historical live evidence for the selected OpenRouter/Qwen path, before the final presentation refactor, remains strong:

```text
9/9 agent scenarios passed
7 clean passes
2 bounded-repair warnings
0 critical failures
5/5 direct tool probes clean
```

That run was recorded at commit `06b0286ddf8309dbfa668e24543393e7a6099024`. Because runtime presentation code changed afterward, the repository does **not** reuse that result as proof for the final code revision. The final submission gate is one fresh complete live run.

Run it with:

```bash
python run_evals.py --repetitions 1
```

The catalog now includes the Hebrew authority-boundary smoke in addition to the nine existing agent scenarios. A release passes only with zero `CRITICAL_FAILURE` results. Warnings may record bounded repair/efficiency behavior but cannot hide a business or safety failure.

See `docs/VERIFICATION.md` for the exact reproducibility checklist.

## Scope

Stage 1 intentionally does not add LangGraph/LangChain orchestration, MCP, multiple agents, a database, Redis, persistent customer memory, an LLM evaluator, a duplicate policy engine, or production payment infrastructure.

The Stage 2 seams are already present: typed agent I/O, isolated tool access, provider abstraction, trusted short-term state, deterministic business projection, and a clear presentation boundary.

## Documentation

- `docs/IMPLEMENTATION_SPEC.md` - final architecture and runtime contract
- `docs/GUARDRAILS.md` - safety invariants
- `docs/MODEL_SELECTION.md` - provider/model evidence
- `docs/VERIFICATION.md` - deterministic/live verification procedure
- `docs/UPSTREAM.md` - pinned upstream source handling
