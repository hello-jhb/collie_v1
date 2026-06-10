"""
knowledge_layer.py - JSON-backed reusable knowledge loader.

The analyst bundle is deal-specific memory. Files under knowledge/observations
are reviewed learning candidates. Files under knowledge/patterns are the small,
distilled runtime layer that can be loaded cheaply on every analysis.

This module intentionally does not load raw observations at runtime. That keeps
the prompt and deterministic logic bounded: many observations become a few
evidence-scored patterns.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


KNOWLEDGE_DIR = Path("knowledge")
PATTERNS_DIR = KNOWLEDGE_DIR / "patterns"

PATTERN_FILES = {
    "model_patterns": "model_patterns.json",
    "metric_patterns": "metric_patterns.json",
    "business_plan_patterns": "business_plan_patterns.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_knowledge_layer(base_dir: Path | str = KNOWLEDGE_DIR) -> dict[str, Any]:
    """
    Load the distilled reusable knowledge layer.

    Missing pattern files return an empty section so local development remains
    forgiving, but malformed JSON should raise loudly.
    """
    root = Path(base_dir)
    patterns_dir = root / "patterns"
    loaded: dict[str, Any] = {}
    for key, filename in PATTERN_FILES.items():
        path = patterns_dir / filename
        loaded[key] = _load_json(path) if path.exists() else {}
    return {
        "knowledge_dir": str(root),
        "patterns": loaded,
    }


def _rule_contradiction_rate(rule: dict[str, Any]) -> float:
    evidence_count = int(rule.get("evidence_count") or 0)
    contradiction_count = int(rule.get("contradiction_count") or 0)
    total = evidence_count + contradiction_count
    return contradiction_count / total if total else 0.0


def active_metric_rules(metric_name: str | None = None) -> list[dict[str, Any]]:
    """
    Return active metric rules, optionally filtered by metric name.

    Candidate rules are intentionally excluded from runtime use. Promotion is a
    separate QC step driven by evidence count, contradiction rate, and review.
    """
    layer = load_knowledge_layer()
    metric_patterns = layer["patterns"].get("metric_patterns", {})
    policy = metric_patterns.get("promotion_policy", {})
    max_contradiction_rate = float(policy.get("max_contradiction_rate") or 1.0)

    out: list[dict[str, Any]] = []
    for metric in metric_patterns.get("metrics", []):
        if metric_name and metric.get("metric") != metric_name:
            continue
        for rule in metric.get("rules", []):
            if rule.get("status") != "active":
                continue
            if _rule_contradiction_rate(rule) > max_contradiction_rate:
                continue
            out.append({
                "metric": metric.get("metric"),
                "canonical_unit": metric.get("canonical_unit"),
                **rule,
            })
    return out


def knowledge_summary() -> dict[str, Any]:
    """Return a compact count summary for diagnostics/UI."""
    layer = load_knowledge_layer()
    patterns = layer["patterns"]

    model_patterns = patterns.get("model_patterns", {}).get("patterns", [])
    business_patterns = patterns.get("business_plan_patterns", {}).get("patterns", [])
    metric_groups = patterns.get("metric_patterns", {}).get("metrics", [])
    metric_rules = [
        rule
        for metric in metric_groups
        for rule in metric.get("rules", [])
    ]

    return {
        "model_patterns": len(model_patterns),
        "metric_groups": len(metric_groups),
        "metric_rules": len(metric_rules),
        "active_metric_rules": len(active_metric_rules()),
        "business_plan_patterns": len(business_patterns),
    }
