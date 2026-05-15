import os
import json
import streamlit as st
from openai import OpenAI


try:
    api_key = st.secrets.get("OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
except Exception:
    api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None


SYSTEM_PROMPT = """
You are a real estate investment manager with institutional asset management experience overseeing portfolios between approximately $150M and $1B in AUM. You translate fragmented file evidence into investment judgment.

FILE TYPES YOU MAY SEE:
- Acquisition underwriting (UW)  — original investment thesis: basis, planned CapEx, debt, lease-up plan, exit value, target returns.
- Annual / monthly financial statements (FS)  — realized operating performance (revenue, expenses, NOI).
- Business plan (BP)  — revised post-acquisition assumptions; supersedes UW where applicable.
- Rent roll / lease schedule  — tenant-level lease detail.

CORE INVESTMENT QUESTIONS YOU ANSWER:
1. Are we performing vs plan? (compare actuals against the most recent plan)
2. Is the income durable? (lease structure, WALT, tenant concentration)
3. Is the leverage healthy? (DSCR, Debt Yield, LTV)
4. Is the asset worth its basis? (value vs. all-in cost basis)
5. Is risk increasing or decreasing? (operating trend direction)
NOTE: do NOT include "Is further capital justified?" as a separate question — capital efficiency is folded into questions 1 and 4.

OUTPUT RULES:
1. Use ONLY the extracted evidence in `analysis_context`. Do not invent numbers, debt terms, or documents.
2. Reference specific files by name when citing a number — e.g. "**$2.44M revenue** (Financial Statement 2022)".
3. Distinguish UW (original) from BP (revised) from actuals (realized). State which one a number came from.
4. When 2021 FS exists alongside UW, treat 2021 as the test of whether the UW plan started working.
5. When BP 2022 exists, treat it as the **revised plan** that supersedes UW for the variance check.
6. When FS 2022 exists alongside BP 2022, that's the primary "vs plan" comparison.
7. If a file type is missing, skip that section — do not write filler ("would require X file"). Just omit.
8. Flag math inconsistencies briefly (e.g. revenue − expenses ≠ NOI) and move on. Do not audit.
9. WALT, tenant concentration, occupancy → use the latest source (typically BP rent roll or FS 2022).
10. DSCR / Debt Yield / LTV → use BP debt terms; verify capacity against the latest FS NOI.
11. Worth-its-basis → compare BP exit value (or implied cap-rate value) to UW total basis.
12. Risk trajectory → compare NOI / occupancy / DSCR direction across 2021 → 2022.

FORMAT (STRICT):
- Markdown bullets only. NO prose paragraphs. NO transitional sentences.
- One sentence per bullet. Bold only the specific number or term that matters.
- 2–4 bullets per section, hard cap at 5.
- Total output under 500 words.
- Lead each bullet with the decision-relevant fact, not commentary.
- No "as we can see," "it is worth noting," "in summary."
"""


def generate_asset_management_narrative(analysis_context):
    prompt = {
        "task": (
            "Generate a preliminary asset management assessment by walking each uploaded "
            "file in chronological order, then answering the 5 core investment questions."
        ),
        "desired_output_style": (
            "Concise markdown bullets only. No paragraphs. "
            "Reference files by name. Bold key numbers. Skip any section whose source file isn't uploaded."
        ),
        "desired_structure": [
            "## Files Reviewed",
            "    - One bullet per uploaded file: file name + classified type + likely period.",
            "",
            "## Original Plan — Acquisition Underwriting",
            "    Skip this entire section if no acquisition_underwriting file is present.",
            "    - **Going-in basis** (purchase price + closing costs + initial CapEx/TI/LC).",
            "    - **Planned CapEx** at close vs future funding.",
            "    - **Debt structure** (floating/fixed, spread/rate, term, LTV).",
            "    - **Lease-up & stabilization assumption**.",
            "    - **Exit value / target IRR / equity multiple**.",
            "",
            "## Year 1 Actual — 2021 Financial Statement",
            "    Skip if no 2021 FS uploaded.",
            "    - **2021 revenue / expenses / NOI** (annual figures).",
            "    - Did Year 1 track UW? Identify the variance driver (occupancy lag, expense overrun, etc.).",
            "",
            "## Revised Plan — 2022 Business Plan",
            "    Skip if no BP uploaded.",
            "    - **What changed vs UW**: CapEx allocation, lease-up timing, exit value, return targets.",
            "    - Note whether revised plan is more or less aggressive than UW.",
            "",
            "## Year 2 Actual vs Revised Plan — 2022 FS vs BP 2022",
            "    This is the primary 'performing vs plan' read.",
            "    - **2022 revenue / expenses / NOI** vs BP 2022 expectations.",
            "    - Top variance drivers and whether they are timing or permanent.",
            "",
            "## Income Durability",
            "    - **WALT** from latest rent roll / BP.",
            "    - **Occupancy** and **tenant concentration**.",
            "    - Near-term rollover exposure if data exists.",
            "",
            "## Leverage Health",
            "    - **DSCR** = latest FS NOI / BP annual debt service (calculated if not labeled).",
            "    - **Debt Yield** = latest FS NOI / loan balance.",
            "    - **LTV** from BP debt terms.",
            "",
            "## Worth its Basis",
            "    - **Implied value** at BP exit cap rate vs **UW total basis**.",
            "    - Whether the spread is realized or still forecast-dependent.",
            "",
            "## Risk Trajectory",
            "    - Direction of NOI / occupancy / DSCR from 2021 → 2022.",
            "    - Whether risk is increasing, stable, or decreasing.",
            "",
            "## Recommended Next AM Actions",
            "    - 3–5 bullets, each starts with a verb (Reconcile, Pursue, Refinance, Stress-test, etc.).",
        ],
        "analysis_context": analysis_context,
    }

    response = client.responses.create(
        model="gpt-5.5",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, default=str)}
        ],
    )

    return response.output_text


def ask_gpt(question, flexible_result, analysis_context):
    prompt = {
        "task": "Answer the user's follow-up question using the structured property evidence.",
        "user_question": question,
        "desired_output_style": (
            "Concise markdown bullets only. No prose paragraphs. "
            "Lead with the direct answer, then 3-6 supporting bullets, then a short data caveat if relevant. "
            "Bold the specific number or fact in each bullet. Total response under 300 words."
        ),
        "flexible_metric_scan_summary": {
            "total_metrics": flexible_result.get("total_metrics"),
            "extracted_count": flexible_result.get("extracted_count"),
            "missing_count": flexible_result.get("missing_count"),
            "sample_extracted_metrics": flexible_result.get("extracted_metrics", [])[:60],
            "sample_missing_metrics": flexible_result.get("missing_metrics", [])[:25],
        },
        "analysis_context": analysis_context,
    }

    response = client.responses.create(
        model="gpt-5.5",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, default=str)}
        ],
    )

    return response.output_text
