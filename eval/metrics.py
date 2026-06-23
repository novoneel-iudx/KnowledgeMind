"""
eval/metrics.py
---------------
Core metric computation for the Stream-4 eval harness.

Metrics:
  routing_accuracy   — fraction of privacy-sensitive tool calls that went LOCAL.
  latency_stats      — mean + p95 elapsed seconds across traces.
  token_stats        — total + mean tokens across traces.
  judge_metrics      — TPR / TNR with Wilson 95% CIs from labelled judge verdicts.
  commitment_recall  — TP/(TP+FN) on golden commitment extractions (stub-safe).
  conflict_precision — TP/(TP+FP) on conflict alerts (stub-safe when no alerts.jsonl).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from routing.router import ALWAYS_LOCAL_TOOLS, TOOL_PRIVACY_FLOORS, PRIVACY_LOCAL_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_privacy_sensitive(tool_name: str) -> bool:
    return (
        tool_name in ALWAYS_LOCAL_TOOLS
        or TOOL_PRIVACY_FLOORS.get(tool_name, 0.0) >= PRIVACY_LOCAL_THRESHOLD
    )


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — valid even for small n."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ---------------------------------------------------------------------------
# MetricResult
# ---------------------------------------------------------------------------

@dataclass
class MetricResult:
    name: str
    value: float
    ci_low: float
    ci_high: float
    n: int
    label: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": round(self.value, 4),
            "ci_low": round(self.ci_low, 4),
            "ci_high": round(self.ci_high, 4),
            "n": self.n,
            "label": self.label,
        }


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------

def routing_accuracy(traces: list[dict]) -> MetricResult:
    """
    From a list of traces, count routing_log entries for privacy-sensitive tools
    and check they went LOCAL. Returns a MetricResult with Wilson CI.
    """
    total = correct = 0
    for trace in traces:
        for entry in trace.get("routing_log", []):
            tool = entry.get("tool", "")
            if _is_privacy_sensitive(tool):
                total += 1
                if entry.get("decision") == "local":
                    correct += 1
    val = correct / total if total else 1.0
    lo, hi = _wilson_ci(correct, total)
    return MetricResult(
        name="routing_accuracy",
        value=val,
        ci_low=lo,
        ci_high=hi,
        n=total,
        label="PASS" if val >= 1.0 else "FAIL",
    )


def latency_stats(traces: list[dict]) -> dict:
    """Mean and p95 elapsed time (seconds) across traces."""
    latencies = sorted(t["elapsed"] for t in traces if "elapsed" in t)
    if not latencies:
        return {"mean_s": 0.0, "p95_s": 0.0, "n": 0}
    n = len(latencies)
    mean = sum(latencies) / n
    p95_idx = max(0, int(math.ceil(n * 0.95)) - 1)
    p95 = latencies[p95_idx]
    return {"mean_s": round(mean, 3), "p95_s": round(p95, 3), "n": n}


def token_stats(traces: list[dict]) -> dict:
    """Total and average tokens. Skips traces with no token summary."""
    totals = [
        t.get("token_summary", {}).get("total_tokens", 0)
        for t in traces
    ]
    n = len(totals)
    return {
        "total": sum(totals),
        "mean": round(sum(totals) / n, 1) if n else 0.0,
        "n": n,
    }


def judge_metrics(verdicts: list[dict]) -> dict:
    """
    TPR / TNR from judge verdicts against human labels.

    Each verdict dict must have:
      judge_correct: bool
      human_label:   bool   (True = answer should be correct)
    """
    tp = tn = fp = fn = 0
    for v in verdicts:
        j = bool(v["judge_correct"])
        h = bool(v["human_label"])
        if h and j:
            tp += 1
        elif not h and not j:
            tn += 1
        elif not h and j:
            fp += 1
        else:
            fn += 1
    tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    tnr = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    tpr_lo, tpr_hi = _wilson_ci(tp, tp + fn) if (tp + fn) > 0 else (0.0, 1.0)
    tnr_lo, tnr_hi = _wilson_ci(tn, tn + fp) if (tn + fp) > 0 else (0.0, 1.0)
    return {
        "tpr": round(tpr, 4),
        "tnr": round(tnr, 4),
        "tpr_ci": [round(tpr_lo, 4), round(tpr_hi, 4)],
        "tnr_ci": [round(tnr_lo, 4), round(tnr_hi, 4)],
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "n": tp + tn + fp + fn,
    }


def commitment_recall(
    extracted: list[str],
    golden: list[str],
) -> MetricResult:
    """
    Soft-commitment recall: TP / (TP + FN).
    Both lists are commitment text strings; matching is substring-based.
    """
    tp = sum(1 for g in golden if any(g.lower() in e.lower() for e in extracted))
    fn = len(golden) - tp
    n = len(golden)
    val = tp / n if n else 1.0
    lo, hi = _wilson_ci(tp, n)
    return MetricResult(
        name="commitment_recall",
        value=val, ci_low=lo, ci_high=hi, n=n,
        label="PASS" if val >= 0.75 else "FAIL",
    )


def conflict_precision(
    detected: list[str],
    true_positives: list[str],
) -> MetricResult:
    """
    Conflict-alert precision: TP / (TP + FP).
    `detected` = all conflict IDs the system raised.
    `true_positives` = which of those are genuinely conflicts.
    """
    tp = len(true_positives)
    n = len(detected)
    val = tp / n if n else 1.0
    lo, hi = _wilson_ci(tp, n)
    return MetricResult(
        name="conflict_precision",
        value=val, ci_low=lo, ci_high=hi, n=n,
        label="PASS" if val >= 0.8 else "FAIL",
    )
