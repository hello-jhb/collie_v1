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
# Layer-aware system prompts for the raw insight pass
# ---------------------------------------------------------------------------
_INSIGHT_SYSTEM: dict[str, str] = {
    "underwriting": """\
You are a real estate investment analyst reading a raw dump of labeled values
from an acquisition underwriting or closing model. Your job is to characterize
the deal and fill in anything the structured metric extractor may have missed.

Return ONLY valid JSON matching this schema (use null for unknown fields):
{
  "property_type": string | null,
  "investment_position": "GP" | "LP" | "Co-GP" | "JV" | null,
  "equity_structure": {
    "lp_pct": number | null,
    "gp_pct": number | null,
    "preferred_return": number | null,
    "promote_description": string | null
  },
  "strategy": "Core" | "Core-Plus" | "Value-Add" | "Opportunistic" | null,
  "hold_period_years": number | null,
  "gap_filled_metrics": {
    "<metric_name>": "<value with units>"
  },
  "key_observations": [string]
}

Rules:
- investment_position: infer from model structure — GP if it shows a promote/carry
  above a preferred return alongside LP equity; LP if only LP returns are modeled
  with a pref threshold; null if neither is evident.
- gap_filled_metrics: include values present in the raw data but absent from the
  structured extraction — e.g. exit cap rate, hold period, LP/GP split percentages.
- key_observations: 3-5 bullets noting anything analytically significant that isn't
  captured in structured metrics (unusual assumptions, flags, model conventions).
- Return ONLY the JSON object. No prose, no markdown fences.
""",

    "business_plan": """\
You are reading a raw dump of labeled values from a real estate business plan
or budget model. Characterize the plan and fill gaps.

Return ONLY valid JSON:
{
  "plan_period": string | null,
  "key_changes_from_uw": [string],
  "revised_noi": number | null,
  "revised_exit_value": number | null,
  "capex_plan_summary": string | null,
  "gap_filled_metrics": { "<metric_name>": "<value with units>" },
  "key_observations": [string]
}
Return ONLY the JSON object.
""",

    "actuals": """\
You are reading a raw dump of labeled values from a real estate financial
statement or operating report. Characterize the period and surface anything
the structured extractor may have missed.

Return ONLY valid JSON:
{
  "reporting_period": string | null,
  "noi": number | null,
  "revenue": number | null,
  "expenses": number | null,
  "occupancy": number | null,
  "notable_items": [string],
  "gap_filled_metrics": { "<metric_name>": "<value with units>" },
  "key_observations": [string]
}
Return ONLY the JSON object.
""",
}

_INSIGHT_SYSTEM_DEFAULT = _INSIGHT_SYSTEM["actuals"]


def _insight_system_for_layer(layer: str) -> str:
    if layer == "underwriting":
        return _INSIGHT_SYSTEM["underwriting"]
    if layer == "business_plan":
        return _INSIGHT_SYSTEM["business_plan"]
    return _INSIGHT_SYSTEM_DEFAULT   # actuals_*, rent_roll, debt, etc.


def run_raw_insight_pass(
    labeled_pairs: list[dict],
    layer: str,
    source_file: str,
) -> dict:
    """
    Send raw labeled (sheet, label, value) pairs to GPT and return structured
    insights as a dict. Uses the fast/cheap model since this runs at ingest time
    for every file.

    Returns {} if the LLM is unavailable or the call fails.
    """
    if not client or not labeled_pairs:
        return {}

    # Format pairs as a compact text table GPT can read easily
    lines = []
    current_sheet = None
    for p in labeled_pairs:
        if p["sheet"] != current_sheet:
            current_sheet = p["sheet"]
            lines.append(f"\n=== {current_sheet} ===")
        lines.append(f"  {p['label']:<45} {p['value']}")

    raw_text = "\n".join(lines)
    user_msg = (
        f"Source file: {source_file}\n"
        f"Layer: {layer}\n\n"
        f"RAW LABELED VALUES FROM WORKBOOK:\n{raw_text}"
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_FAST,
            temperature=0.1,
            messages=[
                {"role": "system", "content": _insight_system_for_layer(layer)},
                {"role": "user",   "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if the model adds them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception:
        return {}
