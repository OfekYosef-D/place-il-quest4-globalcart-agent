"""Live Milestone 3 evaluation runner with deterministic scoring."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from app.agent import OperationsResolverAgent
from app.config import Settings, load_settings
from app.evals.models import CandidateModel, load_candidate_config
from app.evals.reporting import build_report, write_report
from app.evals.scenarios import AGENT_SCENARIOS, TOOL_PROBES, EvalScenario
from app.evals.scoring import EvalClassification, EvalRecord, ToolProbeRecord, record_exception, score_agent_run, score_tool_probe
from app.llm.groq_provider import GroqProvider
from app.tools_adapter import load_toolkit

DEFAULT_CANDIDATES = Path("evals/candidates.json")
DEFAULT_OUTPUT_DIR = Path("eval-results")
SUPPORTED_EVAL_PROVIDERS = frozenset({"groq"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live GlobalCart agent evals with deterministic scoring.")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--scenario", action="append", dest="scenario_ids", help="Run only a named scenario id; repeat for multiple scenarios.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-tool-probes", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print the matrix without calling a model or tools.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repetitions < 1:
        print("--repetitions must be >= 1", file=sys.stderr)
        return 2
    try:
        base_settings = load_settings()
        candidate_config = load_candidate_config(args.candidates)
        _validate_candidates(candidate_config.candidates)
        scenarios = _select_scenarios(args.scenario_ids)
    except Exception as exc:
        print(f"Evaluation configuration error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_dry_run(candidate_config.candidates, scenarios, args.repetitions, not args.skip_tool_probes)
        return 0
    if not base_settings.groq_api_key:
        print("Evaluation configuration error: GROQ_API_KEY is required for live evals. Set it in .env or the environment; never commit it.", file=sys.stderr)
        return 2
    try:
        kit = load_toolkit(base_settings.quest4_starter_kit_path)
    except Exception as exc:
        print(f"Unable to load supplied starter kit: {exc}", file=sys.stderr)
        return 2

    records: list[EvalRecord] = []
    for candidate in candidate_config.candidates:
        settings = _settings_for_candidate(base_settings, candidate)
        try:
            agent = OperationsResolverAgent(settings, GroqProvider(settings), kit)
        except Exception as exc:
            for scenario in scenarios:
                for repetition in range(1, args.repetitions + 1):
                    records.append(record_exception(scenario, exc, candidate_id=candidate.id, model=candidate.model, reasoning_effort=candidate.reasoning_effort, repetition=repetition))
            continue
        for scenario in scenarios:
            for repetition in range(1, args.repetitions + 1):
                print(f"[{candidate.id}] {scenario.id} repetition {repetition}/{args.repetitions}", flush=True)
                try:
                    run = agent.handle_customer_message(scenario.message)
                    record = score_agent_run(scenario, run, candidate_id=candidate.id, model=candidate.model, reasoning_effort=candidate.reasoning_effort, repetition=repetition)
                except Exception as exc:
                    record = record_exception(scenario, exc, candidate_id=candidate.id, model=candidate.model, reasoning_effort=candidate.reasoning_effort, repetition=repetition)
                records.append(record)
                print(f"  -> {record.classification.value}", flush=True)

    if not records:
        print("Evaluation failed closed: no agent-run records were produced.", file=sys.stderr)
        return 2

    probe_records = [] if args.skip_tool_probes else _run_tool_probes(kit)
    report = build_report(records, probe_records, repetitions=args.repetitions, git_head=_git_head())
    paths = write_report(report, args.output_dir)
    _print_summary(report, paths)
    has_critical = any(r.classification is EvalClassification.CRITICAL_FAILURE for r in records) or any(p.classification is EvalClassification.CRITICAL_FAILURE for p in probe_records)
    return 1 if has_critical else 0


def _validate_candidates(candidates: list[CandidateModel]) -> None:
    ids = [candidate.id for candidate in candidates]
    duplicates = sorted({candidate_id for candidate_id in ids if ids.count(candidate_id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate candidate id(s): {', '.join(duplicates)}")
    unsupported = sorted({candidate.provider for candidate in candidates if candidate.provider.lower() not in SUPPORTED_EVAL_PROVIDERS})
    if unsupported:
        raise ValueError(
            "Unsupported eval provider(s): "
            + ", ".join(unsupported)
            + ". Stage 1 live evals currently support only Groq."
        )


def _settings_for_candidate(base: Settings, candidate: CandidateModel) -> Settings:
    return base.model_copy(update={"llm_provider": candidate.provider, "llm_model": candidate.model, "llm_reasoning_effort": candidate.reasoning_effort, "llm_input_cost_per_million": candidate.input_cost_per_million, "llm_output_cost_per_million": candidate.output_cost_per_million})


def _select_scenarios(ids: list[str] | None) -> list[EvalScenario]:
    if not ids:
        return list(AGENT_SCENARIOS)
    by_id = {scenario.id: scenario for scenario in AGENT_SCENARIOS}
    unknown = sorted(set(ids) - set(by_id))
    if unknown:
        raise ValueError(f"Unknown scenario id(s): {', '.join(unknown)}")
    wanted = set(ids)
    return [scenario for scenario in AGENT_SCENARIOS if scenario.id in wanted]


def _run_tool_probes(kit) -> list[ToolProbeRecord]:
    records: list[ToolProbeRecord] = []
    for probe in TOOL_PROBES:
        try:
            records.append(score_tool_probe(probe, result=kit.call(probe.tool_name, **probe.arguments)))
        except Exception as exc:
            records.append(score_tool_probe(probe, exc=exc))
    return records


def _git_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _print_dry_run(candidates: list[CandidateModel], scenarios: list[EvalScenario], repetitions: int, include_tool_probes: bool) -> None:
    print(f"Candidates: {len(candidates)}")
    for candidate in candidates:
        print(f"- {candidate.id}: {candidate.model} reasoning_effort={candidate.reasoning_effort or 'provider-default'}")
    print(f"Agent scenarios: {len(scenarios)} x {repetitions} repetition(s)")
    for scenario in scenarios:
        print(f"- {scenario.id} (upstream scenario {scenario.upstream_scenario})")
    if include_tool_probes:
        print(f"Scenario 8 tool probes: {len(TOOL_PROBES)}")


def _print_summary(report, paths: tuple[Path, Path, Path]) -> None:
    print("\nCandidate summary")
    for summary in report.candidate_summaries:
        gate = "PASS" if summary.release_gate_passed else "FAIL"
        cost = f"${summary.total_estimated_cost_usd:.6f}" if summary.total_estimated_cost_usd is not None else "n/a"
        print(f"- {summary.candidate_id}: gate={gate}, clean={summary.clean_passes}/{summary.total_runs}, warnings={summary.passes_with_warning}, critical={summary.critical_failures}, avg={summary.average_duration_ms:.0f}ms, avg_tokens={summary.average_total_tokens:.0f}, cost={cost}, max_passing_steps={summary.observed_max_passing_steps}")
    if report.tool_probes:
        failed = sum(p.classification is EvalClassification.CRITICAL_FAILURE for p in report.tool_probes)
        print(f"Scenario 8 tool probes: {len(report.tool_probes) - failed}/{len(report.tool_probes)} clean")
    print("Reports:")
    for path in paths:
        print(f"- {path}")


if __name__ == "__main__":
    raise SystemExit(main())
