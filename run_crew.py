"""CLI entry point for the Stage 2 GlobalCart multi-agent crew."""

from __future__ import annotations

import argparse
import json
import sys

from app.crew.bootstrap import build_crew
from app.crew.orchestrator import CrewRun


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_crew.py",
        description="GlobalCart Stage 2 multi-agent operations crew.",
    )
    parser.add_argument("--message", required=True, help="Customer ticket to resolve.")
    parser.add_argument("--verbose", action="store_true", help="Print structured handoffs and per-agent tool trace.")
    return parser


def format_verbose(run: CrewRun) -> str:
    lines = ["--- Stage 2 crew trace ---"]
    if run.result.risk_report is not None:
        lines.append("RiskReport:")
        lines.append(run.result.risk_report.model_dump_json(indent=2))
    if run.result.decision is not None:
        lines.append("Decision:")
        lines.append(run.result.decision.model_dump_json(indent=2))

    for specialist in (run.trace.researcher, run.trace.decision, run.trace.comms):
        if specialist is None:
            continue
        lines.append(f"[{specialist.role}] steps={specialist.steps} failure={specialist.failure_reason}")
        for item in specialist.interactions:
            result_note = ""
            if isinstance(item.result, dict):
                if "error" in item.result:
                    result_note = f" -> error={item.result['error']}"
                elif "status" in item.result:
                    result_note = f" -> status={item.result['status']}"
                elif "verdict" in item.result:
                    result_note = f" -> verdict={item.result['verdict']}"
                elif "risk_band" in item.result:
                    result_note = f" -> risk={item.result.get('risk_score')}/{item.result['risk_band']}"
            lines.append(
                f"  step {item.step}: {item.tool_name} {item.outcome} "
                f"args={json.dumps(item.arguments, sort_keys=True)}{result_note}"
            )
    if run.result.stop_reason:
        lines.append(f"stop_reason={run.result.stop_reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        crew = build_crew()
        run = crew.handle_customer_message(args.message)
    except Exception as exc:
        print(f"Startup/runtime error: {exc}", file=sys.stderr)
        return 2

    print(f"Crew: {run.result.communication.customer_response}")
    if args.verbose:
        print(format_verbose(run))
    return 0 if run.result.status != "FAILED_SAFE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
