"""Shared OpenAI client for scenario narrative generation."""

from __future__ import annotations

import json
import os
import streamlit as st
from openai import OpenAI


def _get_api_key() -> str | None:
    try:
        key = st.secrets.get("OPENAI_API_KEY", None)
    except Exception:
        key = None
    return key or os.getenv("OPENAI_API_KEY")


_api_key = _get_api_key()
client: OpenAI | None = OpenAI(api_key=_api_key) if _api_key else None

MODEL       = "gpt-4o"
MODEL_FAST  = "gpt-4o-mini"   # used for ingest-time insight pass (cost-sensitive)


def llm_available() -> bool:
    return client is not None


def complete(system: str, user: str, temperature: float = 0.2) -> str:
    """Single chat completion. Returns the assistant text."""
    if client is None:
        return "[LLM unavailable — set OPENAI_API_KEY environment variable]"
    response = client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Pass 2: targeted gap-fill + surface insights
# ---------------------------------------------------------------------------

_INSIGHT_SYSTEM = """\
You are a real estate analyst assisting a structured data pipeline.
The pipeline has already extracted known metrics from this file using a catalog.
Your job is THREE things — do all three, do not expand beyond these:

1. CHARACTERIZE: Identify the asset's high-level characteristics that aren't
   captured by numeric metrics. The catalog can't capture these because they're
   inferred from structure, not labeled cells. Be decisive — pick the best label
   based on the data; use null only when truly unknowable.

2. GAP-FILL: For each metric listed as "NOT FOUND", look in the raw file content
   and report whether you found it under any label. If found, report the value
   and what label it appeared under. Do not invent values.

3. OBSERVATIONS: Report 3–5 things analytically significant but not captured
   by any catalog metric. Cite actual values from the file. Examples: unusual
   debt structure (e.g. acquisition + construction loan combined), aggressive
   assumptions, equity waterfall logic, capital outlay timing, strategy implied
   by the cost structure.

HARD RULES:
- Do not re-report metrics already found by the pipeline.
- Do not invent values. If genuinely absent, omit from gap_filled.
- Be concise. Each observation is one sentence with a specific number.
- Return ONLY valid JSON. No prose, no markdown fences.

JSON schema:
{
  "characterization": {
    "property_type":        string | null,    // "Multifamily", "Office", "Industrial", "Hotel", "Retail", "Mixed-use", "Multifamily Conversion", "Ground-up Multifamily", etc.
    "deal_type":            string | null,    // "Acquisition", "Ground-up Development", "Conversion", "Value-Add Renovation", "Refinance", etc.
    "investment_position":  string | null,    // "GP/Sponsor", "LP", "Co-GP", "JV", or null
    "strategy":             string | null,    // "Core", "Core-Plus", "Value-Add", "Opportunistic"
    "asset_name":           string | null,    // The property name if visible in the file
    "location":             string | null,    // "City, State" if visible
    "total_units":          number | null,    // Total residential units / keys / doors if visible
    "total_sf":             number | null,    // Total gross / rentable SF if visible
    "total_debt":           number | null,    // SUM of all loans (acquisition + construction + mezz if any)
    "capital_outlay_after_closing": number | null  // CapEx + post-close construction draws + reserves
  },
  "gap_filled": {
    "<exact metric name from the NOT FOUND list>": {
      "value": <number or string>,
      "label_in_file": "<what the cell actually said>",
      "sheet": "<sheet name>"
    }
  },
  "observations": [
    "<one sentence with specific value>"
  ]
}
"""


def run_raw_insight_pass(
    labeled_pairs: list[dict],
    layer: str,
    source_file: str,
    found_metric_names: list[str] | None = None,
    missing_metric_names: list[str] | None = None,
) -> dict:
    """
    Focused Pass 2: given what the metric catalog already found (found_metric_names)
    and what it expected but missed (missing_metric_names), ask GPT to:
      1. Find the missing metrics in the raw file content
      2. Surface 3-5 observations not captured by any catalog metric

    Only sends high-quality labeled pairs (label_ratio >= 0.5) to reduce noise
    and token cost. Uses gpt-4o-mini (~$0.01 per file).

    Returns {} if LLM unavailable or call fails.
    """
    if not client or not labeled_pairs:
        return {}

    # Filter to high-quality pairs only:
    #   - direction right/below: label directly precedes its value (high signal)
    #   - label_len >= 5: eliminates index headers, single-letter columns, etc.
    quality_pairs = [
        p for p in labeled_pairs
        if p.get("direction") in ("right", "below")
        and p.get("label_len", 0) >= 5
    ]
    # Fall back to all pairs if filtering leaves too few
    if len(quality_pairs) < 30:
        quality_pairs = labeled_pairs

    # Format as compact sheet-grouped text
    lines = []
    current_sheet = None
    for p in quality_pairs:
        if p["sheet"] != current_sheet:
            current_sheet = p["sheet"]
            lines.append(f"\n=== {current_sheet} ===")
        lines.append(f"  {p['label']:<45} {p['value']}")

    raw_text = "\n".join(lines)

    # Build the user message with explicit found/missing context
    found_block = (
        "ALREADY FOUND BY PIPELINE (do not re-report):\n"
        + "\n".join(f"  - {n}" for n in (found_metric_names or []))
        + "\n"
    )
    missing_block = (
        "\nNOT FOUND — look for these in the raw content below:\n"
        + "\n".join(f"  - {n}" for n in (missing_metric_names or []))
        + "\n"
        if missing_metric_names else
        "\nNOT FOUND list: (none — all catalog metrics were found)\n"
    )

    user_msg = (
        f"File: {source_file}  |  Layer: {layer}\n\n"
        f"{found_block}"
        f"{missing_block}"
        f"\nRAW FILE CONTENT:\n{raw_text}"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_FAST,
            temperature=0.1,
            messages=[
                {"role": "system", "content": _INSIGHT_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if model adds them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return {}
