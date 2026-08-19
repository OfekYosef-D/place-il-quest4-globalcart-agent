"""Aggregate and persist deterministic evaluation evidence."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from pydantic import BaseModel, ConfigDict, Field

from app.evals.scoring import EvalClassification, EvalRecord, ToolProbeRecord


class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    model: str
    reasoning_effort: str | None = None
    total_runs: int
    clean_passes: int
    passes_with_warning: int
    critical_failures: int
    release_gate_passed: bool
    pass_rate: float
    clean_rate: float
    average_duration_ms: float
    average_total_tokens: float
    average_tool_calls: float
    total_estimated_cost_usd: float | None = None
    repair_rate: float
    max_steps_seen: int
    observed_max_passing_steps: int | None = None


class EvalSuiteReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    git_head: str | None = None
    repetitions: int
    agent_runs: list[EvalRecord] = Field(default_factory=list)
    tool_probes: list[ToolProbeRecord] = Field(default_factory=list)
    candidate_summaries: list[CandidateSummary] = Field(default_factory=list)


def build_report(records: list[EvalRecord], tool_probes: list[ToolProbeRecord], *, repetitions: int, git_head: str | None) -> EvalSuiteReport:
    candidate_ids = sorted({record.candidate_id for record in records})
    summaries = [summarize_candidate([record for record in records if record.candidate_id == candidate_id]) for candidate_id in candidate_ids]
    summaries.sort(key=lambda s: (not s.release_gate_passed, -s.clean_rate, s.average_duration_ms, s.total_estimated_cost_usd if s.total_estimated_cost_usd is not None else float("inf")))
    return EvalSuiteReport(generated_at=datetime.now(timezone.utc).isoformat(), git_head=git_head, repetitions=repetitions, agent_runs=records, tool_probes=tool_probes, candidate_summaries=summaries)


def summarize_candidate(records: list[EvalRecord]) -> CandidateSummary:
    if not records:
        raise ValueError("Cannot summarize an empty candidate record set.")
    clean = sum(r.classification is EvalClassification.CLEAN_PASS for r in records)
    warnings = sum(r.classification is EvalClassification.PASS_WITH_WARNING for r in records)
    critical = sum(r.classification is EvalClassification.CRITICAL_FAILURE for r in records)
    costs = [r.metrics.estimated_cost_usd for r in records if r.metrics.estimated_cost_usd is not None]
    passing_steps = [r.metrics.steps for r in records if r.classification is not EvalClassification.CRITICAL_FAILURE]
    return CandidateSummary(
        candidate_id=records[0].candidate_id, model=records[0].model, reasoning_effort=records[0].reasoning_effort,
        total_runs=len(records), clean_passes=clean, passes_with_warning=warnings, critical_failures=critical,
        release_gate_passed=critical == 0, pass_rate=(clean + warnings) / len(records), clean_rate=clean / len(records),
        average_duration_ms=mean(r.metrics.duration_ms for r in records), average_total_tokens=mean(r.metrics.total_tokens for r in records),
        average_tool_calls=mean(r.metrics.tool_calls for r in records), total_estimated_cost_usd=sum(costs) if len(costs) == len(records) else None,
        repair_rate=sum(r.metrics.repair_used for r in records) / len(records), max_steps_seen=max(r.metrics.steps for r in records),
        observed_max_passing_steps=max(passing_steps) if passing_steps else None,
    )


def write_report(report: EvalSuiteReport, output_dir: str | Path) -> tuple[Path, Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = directory / f"eval-report-{stamp}.json"
    run_csv_path = directory / f"eval-runs-{stamp}.csv"
    summary_csv_path = directory / f"eval-summary-{stamp}.csv"
    payload = report.model_dump(mode="json")
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(json_text, encoding="utf-8")
    (directory / "latest.json").write_text(json_text, encoding="utf-8")
    _write_run_csv(report.agent_runs, run_csv_path)
    _write_summary_csv(report.candidate_summaries, summary_csv_path)
    return json_path, run_csv_path, summary_csv_path


def _write_run_csv(records: list[EvalRecord], path: Path) -> None:
    fieldnames = ["candidate_id","model","reasoning_effort","scenario_id","upstream_scenario","repetition","classification","issue_codes","steps","llm_calls","tool_calls","total_tokens","duration_ms","estimated_cost_usd","repair_used","cache_hits","blocked_calls","final_status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({"candidate_id":r.candidate_id,"model":r.model,"reasoning_effort":r.reasoning_effort,"scenario_id":r.scenario_id,"upstream_scenario":r.upstream_scenario,"repetition":r.repetition,"classification":r.classification.value,"issue_codes":";".join(i.code for i in r.issues),"steps":r.metrics.steps,"llm_calls":r.metrics.llm_calls,"tool_calls":r.metrics.tool_calls,"total_tokens":r.metrics.total_tokens,"duration_ms":f"{r.metrics.duration_ms:.3f}","estimated_cost_usd":r.metrics.estimated_cost_usd,"repair_used":r.metrics.repair_used,"cache_hits":r.metrics.cache_hits,"blocked_calls":r.metrics.blocked_calls,"final_status":r.final_status})


def _write_summary_csv(summaries: list[CandidateSummary], path: Path) -> None:
    fieldnames = list(CandidateSummary.model_fields)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(summary.model_dump(mode="json"))
