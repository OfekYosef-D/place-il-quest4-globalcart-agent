"""CLI entry point for the Operations Resolver Agent (Milestone 2).

Usage:
    python run_agent.py --message "..." [--verbose]   # one-shot turn
    python run_agent.py [--verbose]                   # interactive session

Default output is customer-facing text only. `--verbose` appends a
developer trace: model steps, tool interactions, blocked guardrail events,
repair/failure outcome, the internal sentiment/urgency assessment, and the
run summary. Missing LLM configuration exits with code 2 and an actionable
message.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.agent import AgentRun, OperationsResolverAgent
from app.config import Settings, load_settings
from app.llm.groq_provider import GroqProvider
from app.tools_adapter import load_toolkit


class CliConfigError(RuntimeError):
    """Required runtime configuration is missing."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_agent.py",
        description="GlobalCart operations resolver agent (Stage 1, Milestone 2).",
    )
    parser.add_argument(
        "--message",
        help="One-shot customer message. Omit to start an interactive session.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the developer trace after each turn.",
    )
    return parser


def ensure_llm_config(settings: Settings) -> None:
    missing = []
    if not settings.groq_api_key:
        missing.append("GROQ_API_KEY")
    if not settings.llm_model:
        missing.append("LLM_MODEL")
    if missing:
        raise CliConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Set them in .env or the environment (see .env.example)."
        )


def build_agent(settings: Settings) -> OperationsResolverAgent:
    ensure_llm_config(settings)
    if settings.llm_provider.strip().lower() != "groq":
        raise CliConfigError(
            f"Unsupported LLM_PROVIDER {settings.llm_provider!r}: the Stage 1 "
            "implementation supports only 'groq'. Set LLM_PROVIDER=groq in .env "
            "or the environment (see .env.example)."
        )
    provider = GroqProvider(settings)
    kit = load_toolkit(settings.quest4_starter_kit_path)
    return OperationsResolverAgent(settings, provider, kit)


# ----------------------------------------------------------------------
# Rendering (pure helpers, unit-tested without a live provider)
# ----------------------------------------------------------------------


def format_customer_output(run: AgentRun) -> str:
    return f"Agent: {run.result.customer_response}"


def format_verbose_trace(run: AgentRun) -> str:
    """Developer trace: model steps, tools, blocks, assessment, summary."""
    lines = ["--- Developer trace ---"]

    for call in run.model_calls:
        detail = ""
        if call.latency_ms is not None:
            detail += f", latency={call.latency_ms:.0f}ms"
        if call.total_tokens is not None:
            detail += f", tokens={call.total_tokens}"
        if call.retries:
            detail += f", retries={call.retries}"
        lines.append(
            f"[step {call.step}] model {call.kind}: {call.provider}/{call.model}{detail}"
        )

    for interaction in run.tool_interactions:
        cache_note = " (cache hit)" if interaction.outcome.value == "CACHED" else ""
        reason_note = f", reason={interaction.reason_code}" if interaction.reason_code else ""
        duration_note = (
            f", duration={interaction.duration_ms:.2f}ms"
            if interaction.duration_ms is not None
            else ""
        )
        lines.append(
            f"[step {interaction.step}] tool {interaction.tool_name}{cache_note}: "
            f"{interaction.outcome.value}{reason_note}{duration_note}, "
            f"args={json.dumps(interaction.arguments, sort_keys=True)}"
            f"{_result_summary(interaction.result)}"
        )

    for event in run.blocked_events:
        lines.append(f"[step {event.step}] BLOCKED {event.tool_name}: {event.reason}")

    repair_used = any(call.kind == "repair" for call in run.model_calls)
    lines.append(f"repair pass used: {repair_used}")
    if run.state.failure_reason:
        lines.append(f"failure_reason: {run.state.failure_reason}")

    if run.state.sentiment is not None or run.state.urgency is not None:
        lines.append(
            "internal assessment: "
            f"sentiment={run.state.sentiment}, urgency={run.state.urgency}"
        )

    summary = run.summary
    cost_note = (
        f", estimated_cost_usd={summary.estimated_cost_usd:.6f}"
        if summary.estimated_cost_usd is not None
        else ""
    )
    lines.append(
        "summary: "
        f"status={run.result.status.value}, "
        f"steps={run.state.step_count}, "
        f"llm_calls={summary.llm_calls}, "
        f"tool_calls={summary.tool_calls}, "
        f"total_tokens={summary.total_tokens}, "
        f"duration={summary.total_duration_ms:.0f}ms"
        f"{cost_note}"
    )
    return "\n".join(lines)


def _result_summary(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return ""
    if "error" in result:
        return f" -> error={result['error']}"
    if "status" in result:
        return f" -> status={result['status']}"
    if "verdict" in result:
        return f" -> verdict={result['verdict']}"
    return ""


# ----------------------------------------------------------------------
# Session runners
# ----------------------------------------------------------------------


def run_repl(agent: OperationsResolverAgent, verbose: bool) -> None:
    """Interactive session: keeps the last AgentRun for short-term continuation."""
    print("Interactive session. Type 'exit' or press Ctrl+C to quit.")
    previous: AgentRun | None = None
    while True:
        try:
            text = input("Customer: ")
        except (KeyboardInterrupt, EOFError):
            print()
            break
        text = text.strip()
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            break
        run = agent.handle_customer_message(text, previous=previous)
        print(format_customer_output(run))
        if verbose:
            print(format_verbose_trace(run))
        previous = run


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        agent = build_agent(settings)
    except CliConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # startup problems (e.g. starter kit missing)
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2

    if args.message:
        run = agent.handle_customer_message(args.message)
        print(format_customer_output(run))
        if args.verbose:
            print(format_verbose_trace(run))
        return 0

    run_repl(agent, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
