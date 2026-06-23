"""
eval/runner.py
--------------
Evaluation harness — runs the golden set and produces a scored report.

Modes:
  stub (default)  — fully offline: stub agent + stub judge. No API keys needed.
                    Uses the demo_conflicts.py stub pattern.
  live            — runs through HybridMindAgent (requires Ollama/Groq).

Usage:
  python -m eval.runner                          # offline stub report
  python -m eval.runner --live                   # live agent, stub judge
  python -m eval.runner --live --judge groq      # live agent + Groq judge
  python -m eval.runner --case G3 -v             # single case verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from eval.judge import Judge
from eval.metrics import routing_accuracy, latency_stats, token_stats, judge_metrics
from eval.tracer import record_trace

_GOLDEN_PATH = Path(__file__).parent / "golden" / "cases.json"
_REPORTS_DIR = Path(__file__).parent / "reports"

# Routing accuracy target (matches benchmark.py ROUTING_TARGET_PCT)
_ROUTING_TARGET = 1.0
# Judge TPR/TNR targets
_JUDGE_TPR_TARGET = 0.80
_JUDGE_TNR_TARGET = 0.80


# ---------------------------------------------------------------------------
# Golden set loader
# ---------------------------------------------------------------------------

def load_golden() -> list[dict]:
    """Load and filter out meta/skip entries from the golden dataset."""
    raw = json.loads(_GOLDEN_PATH.read_text())
    return [c for c in raw if not c.get("skip")]


# ---------------------------------------------------------------------------
# Stub agent (offline)
# ---------------------------------------------------------------------------

def _stub_run(case: dict) -> dict:
    """
    Offline stub that mimics agent.run() — returns canned answers without
    calling any LLM. Follows the demo_conflicts.py stub pattern.
    """
    routing_log = []
    for i, tool in enumerate(case.get("expected_tools", []), 1):
        decision = case.get("expected_routing", "local")
        routing_log.append({
            "step_id": i,
            "tool": tool,
            "decision": decision,
            "privacy_score": 0.92 if decision == "local" else 0.1,
            "complexity_score": 0.5,
            "reason": f"stub routing for {tool}",
        })
    return {
        "input": case["input"],
        "answer": case.get("stub_answer", "Stub answer for offline evaluation."),
        "routing_log": routing_log,
        "token_summary": None,
        "agency_level": "L2",
        "elapsed": 0.0,
        "session_id": "stub",
    }


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_eval(
    cases: Optional[list[dict]] = None,
    live: bool = False,
    judge_backend: str = "stub",
    verbose: bool = False,
    agency_level: str = "L2",
) -> dict:
    """
    Evaluate cases against the agent and judge.

    Returns a report dict that is also persisted to eval/reports/.
    """
    if cases is None:
        cases = load_golden()

    judge = Judge(backend=judge_backend)
    traces: list[dict] = []
    judge_verdicts: list[dict] = []
    silent_failures: list[dict] = []

    for case in cases:
        cid = case["id"]

        # --- Run agent (live or stub) ---
        if live:
            try:
                from agent.orchestrator import HybridMindAgent, AgencyLevel
                _level_map = {
                    "L1": AgencyLevel.L1_AUGMENTED,
                    "L2": AgencyLevel.L2_WORKFLOW,
                    "L3": AgencyLevel.L3_AUTONOMOUS,
                }
                agent = HybridMindAgent()
                run_out = agent.run(
                    case["input"],
                    agency_level=_level_map.get(agency_level, AgencyLevel.L2_WORKFLOW),
                )
                run_out["input"] = case["input"]
            except Exception as e:
                run_out = {
                    "input": case["input"],
                    "answer": f"I encountered an error: {e}",
                    "routing_log": [],
                    "token_summary": None,
                    "agency_level": agency_level,
                    "elapsed": 0.0,
                    "session_id": "error",
                }
        else:
            run_out = _stub_run(case)

        # --- Capture trace ---
        trace = record_trace(run_out, golden_id=cid)
        traces.append(trace)

        # --- Judge ---
        verdict = judge.evaluate(
            question=case["input"],
            answer=run_out["answer"],
            context=case.get("context", ""),
        )
        human_label = bool(case.get("judge_expected", True))

        judge_verdicts.append({
            "case_id": cid,
            "judge_correct": verdict["correct"],
            "human_label": human_label,
            "reason": verdict["reason"],
            "confidence": verdict["confidence"],
            "backend": verdict["backend"],
        })

        # Silent-failure: judge says wrong with high confidence
        if not verdict["correct"] and verdict.get("confidence", 0) >= 0.8:
            silent_failures.append({
                "case_id": cid,
                "reason": verdict["reason"],
                "confidence": verdict["confidence"],
                "answer_preview": run_out["answer"][:120],
            })

        if verbose:
            judge_matches = verdict["correct"] == human_label
            status = "PASS" if judge_matches else "MISMATCH"
            print(
                f"  {cid:<6} [{status}]  judge={verdict['correct']}  "
                f"human={human_label}  conf={verdict['confidence']:.2f}  "
                f"— {verdict['reason'][:55]}"
            )

    # --- Compute metrics ---
    ra = routing_accuracy(traces)
    lat = latency_stats(traces)
    tok = token_stats(traces)
    jm = judge_metrics(judge_verdicts)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "stub",
        "judge_backend": judge_backend,
        "agency_level": agency_level,
        "n_cases": len(cases),
        "metrics": {
            "routing_accuracy": ra.as_dict(),
            "latency": lat,
            "tokens": tok,
            "judge": jm,
        },
        "silent_failures": silent_failures,
        "judge_verdicts": judge_verdicts,
    }

    # --- Persist report ---
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    report_path = _REPORTS_DIR / f"report_{ts}.json"
    report_path.write_text(json.dumps(report, indent=2))
    report["_report_path"] = str(report_path)

    return report


# ---------------------------------------------------------------------------
# CLI reporter
# ---------------------------------------------------------------------------

def print_report(report: dict) -> None:
    m = report["metrics"]
    ra = m["routing_accuracy"]
    lat = m["latency"]
    tok = m["tokens"]
    jm = m["judge"]

    print(f"\n{'='*65}")
    print(
        f"KnowledgeMind Eval Report  |  mode={report['mode']}  |  "
        f"judge={report['judge_backend']}  |  n={report['n_cases']}"
    )
    print(f"{'='*65}")

    ra_pct = ra["value"] * 100
    print(
        f"Routing accuracy:   {ra_pct:5.1f}%  "
        f"[{ra['ci_low']*100:.1f}%–{ra['ci_high']*100:.1f}%]  "
        f"n={ra['n']}  "
        f"=> {ra['label']}"
    )
    print(f"Latency (mean/p95): {lat['mean_s']}s / {lat['p95_s']}s  n={lat['n']}")
    print(f"Tokens (total/avg): {tok['total']} / {tok['mean']}  n={tok['n']}")

    tpr_target = "PASS" if jm["tpr"] >= _JUDGE_TPR_TARGET else "FAIL"
    tnr_target = "PASS" if jm["tnr"] >= _JUDGE_TNR_TARGET else "FAIL"
    print(
        f"Judge TPR:          {jm['tpr']*100:5.1f}%  "
        f"[{jm['tpr_ci'][0]*100:.1f}%–{jm['tpr_ci'][1]*100:.1f}%]  "
        f"=> {tpr_target}  (target ≥{_JUDGE_TPR_TARGET*100:.0f}%)"
    )
    print(
        f"Judge TNR:          {jm['tnr']*100:5.1f}%  "
        f"[{jm['tnr_ci'][0]*100:.1f}%–{jm['tnr_ci'][1]*100:.1f}%]  "
        f"=> {tnr_target}  (target ≥{_JUDGE_TNR_TARGET*100:.0f}%)"
    )
    print(f"  TP={jm['tp']}  TN={jm['tn']}  FP={jm['fp']}  FN={jm['fn']}  n={jm['n']}")

    if report["silent_failures"]:
        print(f"\n⚠ Silent failures ({len(report['silent_failures'])}):")
        for sf in report["silent_failures"]:
            print(f"  {sf['case_id']}: [{sf['confidence']:.2f}] {sf['reason']}")
            print(f"         Answer: {sf['answer_preview'][:80]!r}")
    else:
        print("\n✓ No silent failures detected.")

    if report.get("_report_path"):
        print(f"\nReport saved → {report['_report_path']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="KnowledgeMind evaluation harness (Stream 4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m eval.runner                    # offline stub\n"
            "  python -m eval.runner --live             # live agent\n"
            "  python -m eval.runner --live --judge groq\n"
            "  python -m eval.runner --case G3 -v       # single case"
        ),
    )
    parser.add_argument("--live", action="store_true",
                        help="Run through the real agent (requires Ollama/Groq).")
    parser.add_argument("--judge", choices=["stub", "groq"], default="stub",
                        help="Judge backend (default: stub).")
    parser.add_argument("--level", choices=["L1", "L2", "L3"], default="L2",
                        help="Agency level for live mode.")
    parser.add_argument("--case", default=None,
                        help="Evaluate a single golden case by ID.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Per-case detail.")
    args = parser.parse_args()

    cases = load_golden()
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"Case '{args.case}' not found. Available: {[c['id'] for c in load_golden()]}")
            return 1

    print(
        f"== KnowledgeMind eval  |  mode={'live' if args.live else 'stub'}  |  "
        f"judge={args.judge}  |  n={len(cases)} case(s) =="
    )

    report = run_eval(
        cases=cases,
        live=args.live,
        judge_backend=args.judge,
        verbose=args.verbose,
        agency_level=args.level,
    )
    print_report(report)

    # Exit 1 if routing accuracy or judge TPR/TNR failed targets
    ra_ok = report["metrics"]["routing_accuracy"]["value"] >= _ROUTING_TARGET
    jm = report["metrics"]["judge"]
    judge_ok = jm["tpr"] >= _JUDGE_TPR_TARGET and jm["tnr"] >= _JUDGE_TNR_TARGET
    return 0 if (ra_ok and judge_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
