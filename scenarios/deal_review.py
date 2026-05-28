"""
Deal Review scenario — institutional acquisition memo (Path B architecture).

Architecture:
  - Catalog provides verified facts with cell-level provenance.
  - GPT acts as the analyst: reads catalog facts + raw file content +
    multi-year time series, then writes a deal memo.
  - Output adapts to deal type (ground-up dev, value-add, core, etc.)
    rather than forcing a rigid 30-field template.

Why this design:
  - Real analysts don't fill forms when they read closing files — they
    write a thesis. The output should match what an institutional asset
    manager actually produces.
  - Templates force every field to be populated, even when irrelevant
    (e.g. "Going-in NOI" on a ground-up dev = always $0). Adaptive
    sections handle that without "—" noise.
  - GPT is good at synthesis. Don't limit it to fill-in-the-blanks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ssot
from scenarios._llm import complete, llm_available
from scenarios.profiles import filter_layer_metrics
from flexible_extractor import extract_time_series_rows


UPLOAD_DIR = Path("uploads")


SYSTEM_PROMPT = """\
You are a senior real estate investment professional writing an acquisition memo.
The user is an institutional asset manager evaluating whether to fund this deal.

You have THREE inputs:
  1. CATALOG-VERIFIED FACTS — numbers extracted from the file by a deterministic
     pipeline, each with a cell reference. These are trustworthy. Cite them.
  2. PASS 2 INFERRED FIELDS — fields GPT inferred from the file structure
     (property type, deal type, total debt, etc.). Less certain than catalog
     facts but still grounded in the data.
  3. TIME SERIES — multi-year projections (NOI, revenue, expenses, cash flow)
     showing how the deal evolves. Use these to construct the NOI/cash flow
     trajectory and identify going-in vs stabilized periods.

YOUR JOB: write a clear, readable memo that an investment committee would actually
use. Not a form. Not a checklist. A memo.

STYLE RULES:
- Cite specific numbers with cell references when possible: "$28.8M (Summary!L22)"
- Inferred values get noted as "(inferred)" without a cell reference
- Adapt sections to the deal type. A ground-up dev needs lease-up timing; a core
  acquisition needs T12 vs UW comparison.
- Be specific. Every risk must reference a specific number, not generic warnings.
- Use markdown. Tables are fine where they clarify (e.g., sources & uses).
- ~500–900 words total. No filler.
- If a section has no meaningful content for THIS deal, omit it entirely.
  Do not write "—" or "N/A".

STRUCTURE (skip or merge sections as the deal warrants):

## Snapshot
One paragraph: asset name, location, property type, size, deal type, basis,
debt/equity split, target return, hold period. This is the elevator pitch.

## Investment Thesis
2–3 paragraphs answering: what is the deal, what's the play, why does this work?
Be specific about value creation drivers (lease-up, rent uplift, cap rate
compression, ground-up build-to-stabilization, etc.).

## Capital Structure
Sources & uses if a development or value-add deal (where post-close capital
is material). Debt terms (amount, rate, term, IO period, future funding).
Equity structure (LP/GP split, pref, promote) if disclosed.

## Cash Flow / NOI Trajectory
Walk through how NOI and cash flow evolve. Use the time series to identify:
  - When NOI begins (year of lease-up or stabilization)
  - Stabilized NOI level and timing
  - Exit NOI and any growth assumptions
For a ground-up dev: NOI starts at $0, ramps over lease-up period to stabilized.
For value-add: walk from current NOI through post-renovation NOI.
For core: focus on T12 vs UW Year 1 assumptions and growth trajectory.

## Return Profile
Levered IRR, equity multiple, cash-on-cash. Mention LP-level returns if
modeled separately from deal-level (most equity waterfalls show this).
Note the assumptions driving returns (exit cap rate, rent growth, hold period).

## Key Risks
3–5 risks SPECIFIC TO THIS DEAL. Each must reference a specific number from
the data. Generic risks ("market risk", "interest rate risk") are forbidden
unless tied to an actual model assumption.

## Verified Data Appendix
Bullet list of every catalog-extracted fact with cell reference. This is the
audit trail — every cited number in the memo above can be traced here.
Format: `**Metric Name**: $X,XXX,XXX (Sheet!Cell)`
"""


def _format_time_series_block(series: list[dict], max_rows: int = 25) -> str:
    """Render time series as a readable text table for GPT."""
    if not series:
        return "(no time series extracted from this file)"

    # Group by sheet, take most analytically relevant rows
    # Priority: NOI, Revenue, EGI, Operating Expenses, Cash Flow, Debt Service
    priority_terms = [
        "noi", "net operating income", "egi", "effective gross",
        "operating expense", "total expense", "cash flow",
        "debt service", "rental income", "total income",
        "potential gross", "occupancy", "stabilized",
        "total project", "total uses", "total sources", "equity funded",
    ]

    def row_priority(s):
        label_lower = s["label"].lower()
        for i, kw in enumerate(priority_terms):
            if kw in label_lower:
                return i
        return 999

    series_sorted = sorted(series, key=row_priority)[:max_rows]

    lines = []
    current_sheet = None
    for s in series_sorted:
        if s["sheet"] != current_sheet:
            current_sheet = s["sheet"]
            lines.append(f"\n[{current_sheet}]")
            # Print headers once per sheet
            lines.append("  " + " | ".join(s["headers"][:8]))
        # Format values
        vals = []
        for v in s["values"][:8]:
            if v is None:
                vals.append("—")
            elif abs(v) >= 1_000_000:
                vals.append(f"${v/1_000_000:.2f}M")
            elif abs(v) >= 1_000:
                vals.append(f"${v/1_000:.0f}K")
            elif isinstance(v, float) and abs(v) < 1:
                vals.append(f"{v:.1%}")
            else:
                vals.append(f"{v:,.0f}")
        lines.append(f"  {s['label'][:40]:<40} {' | '.join(vals)}")
    return "\n".join(lines)


def _format_catalog_facts(metrics: dict) -> str:
    """Render catalog metrics as a citable list with cell references."""
    lines = []
    for name, data in metrics.items():
        if data.get("value") is None:
            continue
        val = data["value"]
        cell = f"{data.get('sheet','?')}!{data.get('cell','?')}"
        # Format number nicely
        if isinstance(val, (int, float)):
            if abs(val) >= 1_000_000:
                v_str = f"${val/1_000_000:.2f}M"
            elif abs(val) >= 1_000:
                v_str = f"${val:,.0f}"
            elif abs(val) < 1 and val != 0:
                v_str = f"{val:.2%}"
            else:
                v_str = f"{val:,.2f}"
        else:
            v_str = str(val)
        lines.append(f"  - **{name}**: {v_str}  ({cell})")
    return "\n".join(lines) if lines else "  (no catalog facts extracted)"


def _format_pass2_fields(raw_insights: dict) -> str:
    """Render Pass 2 found fields as a list."""
    if not raw_insights:
        return "(Pass 2 did not run — no inferred fields available)"
    found = raw_insights.get("found", {}) or {}
    if not found:
        return "(Pass 2 ran but found no additional fields)"
    lines = []
    for field_name, data in found.items():
        if not isinstance(data, dict) or data.get("value") is None:
            continue
        val = data["value"]
        label = data.get("label_in_file", "")
        sheet = data.get("sheet", "")
        loc = f" [{sheet}: {label}]" if label or sheet else ""
        lines.append(f"  - **{field_name}**: {val}{loc}")
    return "\n".join(lines) if lines else "(no fields populated)"


def generate_deal_review() -> dict[str, Any]:
    """
    Generate the institutional deal memo.
    """
    s = ssot.load_ssot()
    underwriting = s["layers"].get("underwriting")
    if not underwriting:
        return {"error": "No underwriting layer in SSOT. Upload an acquisition file first."}

    if not llm_available():
        return {"error": "OPENAI_API_KEY is not set."}

    # Apply scenario profile so we only pass relevant catalog metrics
    filtered = filter_layer_metrics(underwriting, "deal_review")
    catalog_metrics = filtered.get("metrics", {})

    # Pass 2 inferred fields
    raw_insights = underwriting.get("raw_insights") or {}

    # Time series from the source file (NOI/revenue/cash flow trajectory)
    source_file = underwriting.get("source_file")
    time_series_block = ""
    if source_file:
        file_path = UPLOAD_DIR / source_file
        if file_path.exists():
            try:
                ts = extract_time_series_rows(file_path)
                time_series_block = _format_time_series_block(ts)
            except Exception as e:
                time_series_block = f"(time series extraction failed: {e})"
        else:
            time_series_block = f"(source file not found in uploads: {source_file})"

    # Pass 2 observations (free-form context GPT noted at ingest)
    observations = raw_insights.get("observations", []) or []
    model_summary = raw_insights.get("model_summary", "") or ""

    # Build the user prompt
    user_prompt = f"""\
ASSET: {source_file or 'Unknown'}
INGESTED: {underwriting.get('ingested_at', 'Unknown')}

{f'PASS 2 MODEL SUMMARY: {model_summary}' if model_summary else ''}

===== CATALOG-VERIFIED FACTS (cite these with cell references) =====

{_format_catalog_facts(catalog_metrics)}

===== PASS 2 INFERRED FIELDS (use; note as "(inferred)" if cited) =====

{_format_pass2_fields(raw_insights)}

===== PASS 2 OBSERVATIONS (use for context / risks) =====

{chr(10).join(f'  - {o}' for o in observations) if observations else '  (none)'}

===== TIME SERIES (multi-year projections — use for NOI / cash flow trajectory) =====
{time_series_block}

Now write the deal memo following the structure in your system prompt.
Adapt sections to this deal's type. Be specific. Cite cell references where
catalog facts are used.
"""

    narrative = complete(SYSTEM_PROMPT, user_prompt, temperature=0.2)

    # Memorialize the acquisition (write-once)
    _memorialize_acquisition(s, narrative, filtered, underwriting)

    return {
        "scenario": "deal_review",
        "narrative": narrative,
        "data_used": {
            "layers": ["underwriting"],
            "source_files": [source_file] if source_file else [],
            "catalog_metric_count": len(catalog_metrics),
            "pass2_field_count": len(raw_insights.get("found", {}) if raw_insights else {}),
            "time_series_rows": len(time_series_block.splitlines()) if time_series_block else 0,
        },
    }


def _memorialize_acquisition(
    s: dict[str, Any],
    narrative: str,
    filtered: dict[str, Any],
    underwriting: dict[str, Any],
) -> None:
    """Save the acquisition memo as a permanent record (write-once)."""
    if s["layers"].get("acquisition_summary"):
        return
    s["layers"]["acquisition_summary"] = {
        "source_file": underwriting.get("source_file"),
        "ingested_at": underwriting.get("ingested_at"),
        "metric_count": filtered.get("metric_count", 0),
        "metrics":      filtered.get("metrics", {}),
        "narrative":    narrative,
    }
    ssot.save_ssot(s)
