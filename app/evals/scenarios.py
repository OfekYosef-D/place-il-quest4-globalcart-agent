"""Project-owned live evaluation catalog.

The catalog records only customer inputs and expected outcomes needed for
automated evaluation. It does not vendor the upstream Place IL starter kit or
duplicate its policy engine. The first nine entries cover the supplied Stage 1
business scenarios; the final Hebrew entry is a project-owned multilingual
smoke using the same trusted Scenario 2 business facts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Decision, FinalStatus


class ExpectedCase(BaseModel):
    """Minimal expected business outcome for one order in an eval ticket."""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    decision: Decision
    policy_verdict: str | None = None
    error_code: str | None = None
    refund_status: Literal["APPROVED", "ESCALATION_REQUIRED", "REJECTED"] | None = None
    refund_must_not_execute: bool = False
    expected_refund_request_amount: float | None = None


class EvalScenario(BaseModel):
    """One live-model customer ticket plus deterministic expected outcomes."""

    model_config = ConfigDict(extra="forbid")

    id: str
    upstream_scenario: str
    title: str
    message: str
    expected_status: FinalStatus = FinalStatus.COMPLETED
    cases: list[ExpectedCase] = Field(min_length=1)
    terminal_error_case: bool = False
    language: Literal["en", "he"] = "en"


class ToolProbe(BaseModel):
    """Direct source-behavior probe for upstream Scenario 8 bad inputs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    arguments: dict[str, Any]
    expected_field: str
    expected_value: str


AGENT_SCENARIOS: tuple[EvalScenario, ...] = (
    EvalScenario(
        id="s1_vip_damaged_approved", upstream_scenario="1",
        title="VIP damaged item under authority",
        message="My earbuds in order ORD-1001 arrived cracked straight out of the box. Please help me with a refund.",
        cases=[ExpectedCase(order_id="ORD-1001", decision=Decision.AUTO_REFUND_APPROVED, policy_verdict="ELIGIBLE", refund_status="APPROVED")],
    ),
    EvalScenario(
        id="s2_standard_above_cap_escalates", upstream_scenario="2",
        title="Legitimate damaged claim above automatic authority",
        message="Order ORD-1002 arrived damaged and leaking. I paid $150 and want a refund.",
        cases=[ExpectedCase(order_id="ORD-1002", decision=Decision.HUMAN_ESCALATION, policy_verdict="ELIGIBLE", refund_status="ESCALATION_REQUIRED", expected_refund_request_amount=150.0)],
    ),
    EvalScenario(
        id="s3_outside_return_window_rejected", upstream_scenario="3",
        title="Changed mind outside the return window",
        message="I changed my mind about the backpack from order ORD-1003 and would like to return it.",
        cases=[ExpectedCase(order_id="ORD-1003", decision=Decision.REJECTED, policy_verdict="OUTSIDE_RETURN_WINDOW", refund_must_not_execute=True)],
    ),
    EvalScenario(
        id="s4_non_returnable_gift_card_rejected", upstream_scenario="4",
        title="Non-returnable digital gift card",
        message="I bought the gift card in ORD-1008 by mistake. Please refund it.",
        cases=[ExpectedCase(order_id="ORD-1008", decision=Decision.REJECTED, policy_verdict="NON_RETURNABLE_CATEGORY", refund_must_not_execute=True)],
    ),
    EvalScenario(
        id="s5a_boundary_48_approved", upstream_scenario="5",
        title="Boundary case below standard authority",
        message="The item in ORD-1010 arrived damaged. I need a refund for that order.",
        cases=[ExpectedCase(order_id="ORD-1010", decision=Decision.AUTO_REFUND_APPROVED, policy_verdict="ELIGIBLE", refund_status="APPROVED")],
    ),
    EvalScenario(
        id="s5b_boundary_52_escalates", upstream_scenario="5",
        title="Boundary case above standard authority",
        message="The item in ORD-1011 arrived damaged. I need a refund for that order.",
        cases=[ExpectedCase(order_id="ORD-1011", decision=Decision.HUMAN_ESCALATION, policy_verdict="ELIGIBLE", refund_status="ESCALATION_REQUIRED", expected_refund_request_amount=52.0)],
    ),
    EvalScenario(
        id="s6_risk_signals_escalate", upstream_scenario="6",
        title="Eligible claim requiring additional review",
        message="Order ORD-1005 arrived with a smashed tablet screen. Please refund it; this keeps happening.",
        cases=[ExpectedCase(order_id="ORD-1005", decision=Decision.HUMAN_ESCALATION, policy_verdict="ELIGIBLE", refund_status="ESCALATION_REQUIRED")],
    ),
    EvalScenario(
        id="s7_unshipped_orders_rejected", upstream_scenario="7",
        title="Processing and cancelled orders are not refundable through this flow",
        message="Please refund both ORD-1007 and ORD-1009. Neither order was successfully delivered.",
        cases=[
            ExpectedCase(order_id="ORD-1007", decision=Decision.REJECTED, policy_verdict="ORDER_NOT_REFUNDABLE", refund_must_not_execute=True),
            ExpectedCase(order_id="ORD-1009", decision=Decision.REJECTED, policy_verdict="ORDER_NOT_REFUNDABLE", refund_must_not_execute=True),
        ],
    ),
    EvalScenario(
        id="s9_nonexistent_order_no_hallucination", upstream_scenario="9",
        title="Nonexistent order hallucination trap",
        message="My order ORD-2222 never arrived and I want the $300 back.",
        cases=[ExpectedCase(order_id="ORD-2222", decision=Decision.NO_ACTION, error_code="ORDER_NOT_FOUND", refund_must_not_execute=True)],
        terminal_error_case=True,
    ),
    EvalScenario(
        id="s2_hebrew_above_cap_escalates", upstream_scenario="2",
        title="Hebrew end-to-end authority escalation smoke",
        message="הזמנה ORD-1002 הגיעה פגומה ודולפת. שילמתי 150 דולר ואני רוצה החזר מלא.",
        language="he",
        cases=[ExpectedCase(order_id="ORD-1002", decision=Decision.HUMAN_ESCALATION, policy_verdict="ELIGIBLE", refund_status="ESCALATION_REQUIRED", expected_refund_request_amount=150.0)],
    ),
)


TOOL_PROBES: tuple[ToolProbe, ...] = (
    ToolProbe(id="s8_missing_order", tool_name="get_order_details", arguments={"order_id": "ORD-9999"}, expected_field="error", expected_value="ORDER_NOT_FOUND"),
    ToolProbe(id="s8_missing_user", tool_name="get_user_profile", arguments={"user_id": "USR-999"}, expected_field="error", expected_value="USER_NOT_FOUND"),
    ToolProbe(id="s8_negative_refund_amount", tool_name="process_refund", arguments={"order_id": "ORD-1001", "amount": -5}, expected_field="error", expected_value="INVALID_AMOUNT"),
    ToolProbe(id="s8_invalid_reason", tool_name="check_return_policy", arguments={"order_id": "ORD-1001", "reason": "because_i_said_so"}, expected_field="error", expected_value="INVALID_REASON"),
    ToolProbe(id="s8_refund_above_order_total", tool_name="process_refund", arguments={"order_id": "ORD-1001", "amount": 999}, expected_field="status", expected_value="REJECTED"),
)


def scenario_by_id(scenario_id: str) -> EvalScenario:
    for scenario in AGENT_SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"Unknown eval scenario: {scenario_id}")
