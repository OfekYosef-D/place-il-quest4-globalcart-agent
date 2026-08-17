"""Tests for the CLI: render helpers, arg parsing, missing-config error path."""

import pytest

import run_agent
from app.agent import AgentRun
from app.config import Settings
from app.schemas import ActionTaken, AgentResult, CaseResult, Decision, FinalStatus
from app.state import AgentState, ToolInteraction, ToolInteractionOutcome
from app.tracing import BlockedToolEvent, ModelCallRecord, RunSummary
from run_agent import (
    CliConfigError,
    build_parser,
    ensure_llm_config,
    format_customer_output,
    format_verbose_trace,
    main,
)


def _synthetic_run(with_repair=False, with_cost=False) -> AgentRun:
    state = AgentState(sentiment="frustrated", urgency="high", step_count=3)
    state.tool_history.append(
        ToolInteraction(
            step=1,
            tool_name="get_order_details",
            arguments={"order_id": "ORD-1001"},
            outcome=ToolInteractionOutcome.EXECUTED,
            result={"order_id": "ORD-1001", "status": "delivered"},
            duration_ms=1.25,
        )
    )
    state.tool_history.append(
        ToolInteraction(
            step=2,
            tool_name="process_refund",
            arguments={"order_id": "ORD-1001", "amount": 35.0},
            outcome=ToolInteractionOutcome.BLOCKED,
            reason_code="MISSING_ELIGIBLE_POLICY_PRECONDITION",
        )
    )
    result = AgentResult(
        status=FinalStatus.COMPLETED,
        reasoning_chain=["Verified order", "Policy eligible", "Refund approved"],
        action_taken=ActionTaken(
            tools_called=["get_order_details", "check_return_policy", "process_refund"],
            cases=[
                CaseResult(
                    order_id="ORD-1001",
                    decision=Decision.AUTO_REFUND_APPROVED,
                    refund_amount=35.0,
                    refund_id="RF-1001-3500",
                )
            ],
        ),
        customer_response="Your refund was approved.",
    )
    model_calls = [
        ModelCallRecord(
            step=1, provider="groq", model="fake", kind="tool_call",
            latency_ms=12.0, total_tokens=15, retries=1,
        ),
        ModelCallRecord(step=2, provider="groq", model="fake", kind="final", latency_ms=8.0, total_tokens=10),
    ]
    if with_repair:
        # The repair pass is a real model call with real metadata (fix 7).
        model_calls.append(
            ModelCallRecord(
                step=2, provider="groq", model="fake", kind="repair",
                latency_ms=5.0, total_tokens=8, retries=0,
            )
        )
    summary = RunSummary(
        llm_calls=len(model_calls),
        tool_calls=2,
        total_tokens=25,
        total_duration_ms=123.0,
        estimated_cost_usd=0.000012 if with_cost else None,
    )
    return AgentRun(
        result=result,
        state=state,
        model_calls=model_calls,
        tool_interactions=list(state.tool_history),
        blocked_events=[
            BlockedToolEvent(
                step=2,
                tool_name="process_refund",
                arguments={"order_id": "ORD-1001", "amount": 35.0},
                reason="MISSING_ELIGIBLE_POLICY_PRECONDITION",
            )
        ],
        summary=summary,
    )


def test_customer_output_contains_only_customer_facing_text():
    run = _synthetic_run()
    assert format_customer_output(run) == "Agent: Your refund was approved."


def test_verbose_trace_covers_steps_tools_blocks_assessment_summary():
    run = _synthetic_run(with_repair=True)
    trace = format_verbose_trace(run)

    assert "[step 1] model tool_call: groq/fake, latency=12ms, tokens=15, retries=1" in trace
    assert "[step 2] model repair: groq/fake, latency=5ms, tokens=8" in trace
    assert "[step 1] tool get_order_details: EXECUTED, duration=1.25ms" in trace
    assert "[step 2] tool process_refund: BLOCKED, reason=MISSING_ELIGIBLE_POLICY_PRECONDITION" in trace
    assert "BLOCKED process_refund: MISSING_ELIGIBLE_POLICY_PRECONDITION" in trace
    assert "repair pass used: True" in trace
    assert "internal assessment: sentiment=frustrated, urgency=high" in trace
    assert "status=COMPLETED" in trace
    assert "llm_calls=3" in trace
    assert "estimated_cost_usd" not in trace, "cost only appears when pricing is configured"


def test_verbose_trace_cache_hit_and_cost_rendering():
    run = _synthetic_run(with_cost=True)
    run.tool_interactions[0] = ToolInteraction(
        step=1,
        tool_name="get_order_details",
        arguments={"order_id": "ORD-1001"},
        outcome=ToolInteractionOutcome.CACHED,
        result={"order_id": "ORD-1001"},
    )
    trace = format_verbose_trace(run)
    assert "tool get_order_details (cache hit): CACHED" in trace
    assert "estimated_cost_usd=0.000012" in trace
    assert "repair pass used: False" in trace


def test_arg_parsing():
    parser = build_parser()
    args = parser.parse_args(["--message", "Refund ORD-1001", "--verbose"])
    assert args.message == "Refund ORD-1001"
    assert args.verbose is True

    defaults = parser.parse_args([])
    assert defaults.message is None
    assert defaults.verbose is False


def test_ensure_llm_config_reports_missing_variables():
    with pytest.raises(CliConfigError, match="GROQ_API_KEY"):
        ensure_llm_config(Settings())
    with pytest.raises(CliConfigError, match="LLM_MODEL"):
        ensure_llm_config(Settings(groq_api_key="key"))
    ensure_llm_config(Settings(groq_api_key="key", llm_model="model"))  # no raise


def test_main_missing_config_exits_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr(run_agent, "load_settings", lambda: Settings())
    exit_code = main(["--message", "hello"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "GROQ_API_KEY" in captured.err
    assert "LLM_MODEL" in captured.err


def test_build_agent_rejects_unsupported_provider():
    """Fix 10: non-groq LLM_PROVIDER fails startup with an actionable error."""
    settings = Settings(groq_api_key="key", llm_model="model", llm_provider="openai")
    with pytest.raises(CliConfigError, match="LLM_PROVIDER"):
        run_agent.build_agent(settings)


def test_main_unsupported_provider_exits_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr(
        run_agent,
        "load_settings",
        lambda: Settings(groq_api_key="key", llm_model="model", llm_provider="anthropic"),
    )
    exit_code = main(["--message", "hello"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "LLM_PROVIDER" in captured.err


def test_main_one_shot_runs_agent_and_prints_customer_output(monkeypatch, capsys):
    run = _synthetic_run()

    class StubAgent:
        def handle_customer_message(self, text, previous=None):
            assert text == "Refund ORD-1001"
            return run

    monkeypatch.setattr(run_agent, "build_agent", lambda settings: StubAgent())
    monkeypatch.setattr(run_agent, "load_settings", lambda: Settings())

    exit_code = main(["--message", "Refund ORD-1001", "--verbose"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Agent: Your refund was approved." in captured.out
    assert "--- Developer trace ---" in captured.out
