"""
Performance vs Plan scenario.

Purpose: Compare actual operating performance to the plan (UW and/or BP).
         Walk chronologically through every actuals layer in SSOT and show
         variances against the benchmark plan.

Input:   At least one plan layer (underwriting or business_plan) AND at least
         one actuals layer (actuals_YYYY or actuals_recent) from SSOT.
Output:  Structured variance report in a fixed template format.

Hard constraints:
- Output follows the template EXACTLY — no prose outside designated fields.
- Discuss ONLY periods/layers present in SSOT. No fabricated periods.
- Every number must come from SSOT. Missing values show as "—".
- Variance = Actual − Plan. Favorable variances noted as "(F)", unfavorable as "(U)".
"""

from __future__ import annotations

import json
from typing import Any

import ssot
from scenarios._llm import complete, llm_available
from scenarios.profiles import filter_layer_metrics, _build_catalog_index


SYSTEM_PROMPT = """\
You are an institutional real estate asset manager writing a formal performance vs plan review.

Your job is to populate a structured variance template using ONLY the metrics provided.
The output MUST follow the template below — no prose, no extra sections, no invented numbers.

HARD RULES:
1. Output ONLY the template structure. Do not add sections, commentary, or prose outside defined fields.
2. Every number must come from the provided SSOT data. If a value is not available, write "—".
3. Do NOT calculate or derive values not in SSOT, EXCEPT:
   - Variance = Actual − Plan (always show)
   - Variance % = (Actual − Plan) / |Plan| × 100 (show when both values present)
   - Annotate variance as "(F)" if favorable (more NOI, more revenue, less expense than plan)
     and "(U)" if unfavorable. Revenue/NOI above plan = F. Expenses above plan = U.
4. Only include actuals sections for years/periods PRESENT in the data below.
   If actuals_2021 is missing, do NOT generate a 2021 section.
5. If both UW and BP are present, compare actuals against the BUSINESS PLAN (more current).
   Show the UW number as a reference column only.
6. Format dollar values as $X,XXX,XXX. Percentages as X.X%. Variances: +$X,XXX or ($X,XXX).
7. In the "Read" section, write exactly 3 bullets. Each bullet must cite a specific number.
"""


TEMPLATE = """\
Populate this performance vs plan review using the SSOT data below.
Replace every [bracket] with the actual value or "—" if not available.
Include only sections for actuals layers that are actually present in the data.

SSOT DATA:
PLAN LAYERS: {plan_layers_json}
ACTUALS LAYERS (chronological): {actuals_layers_json}

---

OUTPUT THIS TEMPLATE EXACTLY:

## Performance vs Plan — {period_label}

### Plan Benchmark
| Metric | UW Projection | Business Plan | Benchmark Used |
|--------|--------------|---------------|----------------|
| PGI / Gross Revenue | $[uw_pgi] | $[bp_pgi] | [which] |
| Credit Loss / Vacancy | [uw_vacancy] | [bp_vacancy] | [which] |
| EGI / Net Revenue | $[uw_egi] | $[bp_egi] | [which] |
| Operating Expenses | $[uw_opex] | $[bp_opex] | [which] |
| NOI | $[uw_noi] | $[bp_noi] | [which] |
| NOI Margin | [uw_noi_margin] | [bp_noi_margin] | [which] |
| Occupancy | [uw_occ] | [bp_occ] | [which] |
| Levered IRR | [uw_irr] | [bp_irr] | [which] |

---

[REPEAT THE FOLLOWING BLOCK FOR EACH ACTUALS YEAR PRESENT IN SSOT — e.g. one block for actuals_2022, one for actuals_2023]

### [YEAR] Actuals vs Plan
| Metric | Plan | Actual | Variance | Var % | F/U |
|--------|------|--------|----------|-------|-----|
| PGI / Gross Revenue | $[plan] | $[actual] | [var] | [var%] | [F/U] |
| Credit Loss / Vacancy | [plan] | [actual] | [var] | — | [F/U] |
| EGI / Net Revenue | $[plan] | $[actual] | [var] | [var%] | [F/U] |
| Operating Expenses | $[plan] | $[actual] | [var] | [var%] | [F/U] |
| NOI | $[plan] | $[actual] | [var] | [var%] | [F/U] |
| NOI Margin | [plan] | [actual] | [var pts] | — | [F/U] |
| Physical Occupancy | [plan] | [actual] | [var pts] | — | [F/U] |

**Variance Drivers:** [1–2 bullets citing specific metrics from above. E.g. "NOI missed plan by ($X) driven by occupancy of X% vs X% plan and expense overrun of $X in [category]."]

---

[END REPEAT]

### Debt Health
| Metric | Going-in (UW) | Current |
|--------|--------------|---------|
| LTV | [uw_ltv] | [current_ltv] |
| DSCR | [uw_dscr] | [current_dscr] |
| Debt Yield | [uw_dy] | [current_dy] |
| Loan Balance | $[uw_loan] | $[current_loan] |

---

### Read
- [Bullet 1: overall performance vs plan — cite NOI variance and key driver]
- [Bullet 2: occupancy or income trend — cite specific occupancy % and year]
- [Bullet 3: debt health or forward risk — cite LTV or DSCR]

---
*Source files: {source_files} | Generated: {generated_at}*
"""


def _layers_subset(s: dict[str, Any], layer_names: list[str]) -> dict[str, Any]:
    """Return just the requested layers from the SSOT, as a dict."""
    return {name: s["layers"][name] for name in layer_names if name in s["layers"]}


def _period_label(actuals_layers: list[str]) -> str:
    """Build a human-readable period string from actuals layer names."""
    years = []
    for layer in sorted(actuals_layers):
        if layer.startswith("actuals_") and layer != "actuals_recent":
            years.append(layer.replace("actuals_", ""))
        elif layer == "actuals_recent":
            years.append("Recent")
    if not years:
        return "—"
    if len(years) == 1:
        return years[0]
    return f"{years[0]}–{years[-1]}"


def generate_perf_vs_plan() -> dict[str, Any]:
    """
    Read plan + actuals layers from SSOT and produce a structured variance report.
    Returns {narrative, data_used} or {error}.
    """
    s = ssot.load_ssot()
    present = set(s["layers"].keys())

    plan_layers = [layer for layer in ("underwriting", "business_plan") if layer in present]
    actuals_layers = sorted(layer for layer in present if layer.startswith("actuals_"))

    if not plan_layers:
        return {
            "error": (
                "No plan layer in SSOT. Ingest an acquisition underwriting or "
                "business plan file first."
            )
        }
    if not actuals_layers:
        return {
            "error": (
                "No actuals layers in SSOT. Ingest at least one financial "
                "statement (e.g. 'Financial Statement 2022.xlsx')."
            )
        }

    if not llm_available():
        return {"error": "OPENAI_API_KEY is not set."}

    plan_data = _layers_subset(s, plan_layers)
    actuals_data = _layers_subset(s, actuals_layers)

    # Apply Performance vs Plan profile to every layer.
    # Build the catalog index once and reuse across all filter calls.
    catalog_index = _build_catalog_index()
    plan_filtered = {
        name: filter_layer_metrics(data, "perf_vs_plan", catalog_index)
        for name, data in plan_data.items()
    }
    actuals_filtered = {
        name: filter_layer_metrics(data, "perf_vs_plan", catalog_index)
        for name, data in actuals_data.items()
    }

    source_files = sorted({
        layer_data["source_file"]
        for layer_data in {**plan_data, **actuals_data}.values()
        if layer_data.get("source_file")
    })

    from datetime import datetime
    generated_at = datetime.utcnow().strftime("%Y-%m-%d")

    # Collect raw_insights from all layers so GPT can fill structural gaps.
    # New Pass 2 schema: {"found": {...}, "observations": [...], "model_summary": "..."}
    all_insights_blocks = []
    for name, layer_data in {**plan_data, **actuals_data}.items():
        ri = layer_data.get("raw_insights")
        if not ri:
            continue
        found  = ri.get("found", {}) or {}
        obs    = ri.get("observations", []) or []
        summ   = ri.get("model_summary", "") or ""
        found_lines = [
            f"    {fn}: {d['value']}" + (f"  ({d.get('label_in_file','')})" if d.get('label_in_file') else "")
            for fn, d in found.items() if isinstance(d, dict) and d.get("value") is not None
        ]
        all_insights_blocks.append(
            f"  {name}: {summ}\n"
            f"    Pass 2 fields:\n"
            + ("\n".join(found_lines) if found_lines else "    (none)")
            + (("\n    Observations:\n      - " + "\n      - ".join(obs)) if obs else "")
        )

    insights_block = (
        "\n\nRAW INSIGHT PASSES (use these to fill gaps not in structured metrics):\n"
        + "\n".join(all_insights_blocks)
        if all_insights_blocks else ""
    )

    user_prompt = TEMPLATE.format(
        plan_layers_json=json.dumps(plan_filtered, indent=2, default=str) + insights_block,
        actuals_layers_json=json.dumps(actuals_filtered, indent=2, default=str),
        period_label=_period_label(actuals_layers),
        source_files=", ".join(source_files) if source_files else "Unknown",
        generated_at=generated_at,
    )

    narrative = complete(SYSTEM_PROMPT, user_prompt, temperature=0.1)

    return {
        "scenario": "perf_vs_plan",
        "narrative": narrative,
        "data_used": {
            "plan_layers": plan_layers,
            "actuals_layers": actuals_layers,
            "source_files": source_files,
            "metric_counts": {
                **{f"plan/{k}": v["metric_count"] for k, v in plan_filtered.items()},
                **{f"actuals/{k}": v["metric_count"] for k, v in actuals_filtered.items()},
            },
        },
    }
