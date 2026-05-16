import os
import json
import streamlit as st
from openai import OpenAI


try:
    api_key = st.secrets.get("OPENAI_API_KEY", None)
except Exception:
    api_key = None

api_key = api_key or os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None


SYSTEM_PROMPT = """
You are a real estate investment manager with institutional asset management experience overseeing portfolios between approximately $150M and $1B in AUM.

You specialize in reconstructing investment performance, diagnosing operational and capital risks, and translating fragmented information into investment judgment.

In a typical workflow, you work across multiple disconnected information sources, including:
- acquisition underwriting models,
- business plan models,
- blended actual + forecast reporting models,
- T12 and monthly financial statements,
- rent rolls,
- debt service and loan models,
- LP/GP waterfall and distribution models,
- leasing reports,
- CapEx trackers,
- market leasing assumptions,
- valuation models,
- lender and investor reporting packages,
- lease abstracts and legal summaries,
- property management reports,
- portfolio dashboards,
- and ad hoc Excel analyses.

Your role is not simply to report metrics, but to:
- reconstruct the current investment state,
- identify performance drivers,
- understand how actual performance diverges from underwriting or business plan expectations,
- determine whether income and value are durable,
- evaluate leverage and capital risk,
- assess whether returns remain justified,
- and identify emerging operational or portfolio risks.

The system extracts candidate metrics from uploaded files using a predefined institutional metric catalog and core-question framework.

Your task is to:
1. interpret the extracted evidence,
2. identify the most useful preliminary asset management read,
3. explain why the evidence matters,
4. qualify uncertainty without making the output feel like a data audit,
5. recommend practical next actions.

Rules:
1. Do not invent numbers, assumptions, or missing documents.
2. Use only the structured evidence provided.
3. The extracted metrics may come from incomplete or fragmented files.
4. Distinguish between:
   - acquisition underwriting = original investment thesis,
   - business plan = updated expectation,
   - actuals = realized operating performance.
5. Do not just say “high confidence” or “partial confidence.” Convert coverage into narrative judgment.
6. Do not lead with missing data unless the uploaded files contain almost no usable evidence.
7. Do not produce a long missing-data inventory unless explicitly asked.
8. Prioritize what can be interpreted from the available evidence.
9. If extracted values appear inconsistent, briefly flag the reconciliation issue, then explain the most likely next AM action.
10. Treat missing data as a limitation, not the main output.
11. If information is insufficient, qualify the conclusion and identify the most useful next source or action.
12. Avoid generic “AI assistant” language.
13. Think and write like an experienced institutional asset manager.
14. Focus on diagnostic reasoning, not just reporting.
15. Explain relationships between metrics whenever possible.
16. Emphasize what matters operationally, financially, and from a return perspective.
17. If return adequacy is discussed, note that acceptability depends on investor return thresholds and strategy.
18. Provide clear, readable, and naturally flowing diagnostic analysis rather than fragmented or isolated observations.
19. Guide the reader logically from operating signals → performance implications → investment consequences.
20. Avoid excessive bullet points unless summarizing key findings.
21. Prefer synthesis over long lists.
22. The goal is not merely to summarize files, but to reconstruct investment reality from fragmented information.
"""


def generate_asset_management_narrative(analysis_context):
    if not client:
        return "[Narrative generation requires OPENAI_API_KEY environment variable]"

    prompt = {
        "task": "Generate a preliminary asset management assessment following a clear chronological narrative: what was planned at acquisition, how year 1 actually performed vs plan, what was revised in year 2 plan, and how year 2 actually performed.",
        "desired_output_style": (
            "Write in clear, flowing, executive-level prose. "
            "Follow the chronological structure rigorously. "
            "Distinguish between planned (underwriting/business plan) and actual (financial statements). "
            "Always lead each section with the key underwritten/planned metrics (these are FIXED and should always be extracted and reported). "
            "Only skip a section if the source document is completely missing."
        ),
        "desired_structure": [
            "## Original Investment Thesis (Acquisition Underwriting)",
            "  - Going-in basis: purchase price, closing costs, initial CapEx/TI/LC total",
            "  - Planned NOI, IRR, and exit value",
            "  - Debt structure (LTV, term, rate)",
            "",
            "## Year 1 Actual Performance (2021 Financial Statement vs Underwriting)",
            "  - What was 2021 actual NOI, revenue, expenses?",
            "  - Did it track the underwriting plan? What were the variance drivers (occupancy, expenses, tenant changes)?",
            "",
            "## Revised Plan (2022 Business Plan - what changed from underwriting)",
            "  - What assumptions were updated? (NOI expectations, capital allocation, exit value)",
            "  - Is the revised plan more or less aggressive than original?",
            "",
            "## Year 2 Actual vs Revised Plan (2022 Financial Statement vs 2022 Business Plan)",
            "  - What was 2022 actual NOI, revenue, expenses?",
            "  - Is performance tracking the revised plan? What are variance drivers?",
            "",
            "## Current Investment Status",
            "  - Income durability assessment (lease terms, tenant concentration, rollover risk)",
            "  - Leverage health (DSCR, LTV, debt yield)",
            "  - Whether current basis is justified by current and projected returns",
            "  - Risk trajectory (improving, stable, or deteriorating?)",
            "",
            "## Recommended Next AM Actions",
            "  - 3-5 specific action items"
        ],
        "analysis_context": analysis_context,
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, default=str)}
        ],
    )

    return response.choices[0].message.content


def ask_gpt(question, flexible_result, analysis_context):
    if not client:
        return "[Question answering requires OPENAI_API_KEY environment variable]"

    prompt = {
        "task": "Answer the user's follow-up question using the structured property evidence.",
        "user_question": question,
        "flexible_metric_scan_summary": {
            "total_metrics": flexible_result.get("total_metrics"),
            "extracted_count": flexible_result.get("extracted_count"),
            "missing_count": flexible_result.get("missing_count"),
            "sample_extracted_metrics": flexible_result.get("extracted_metrics", [])[:60],
            "sample_missing_metrics": flexible_result.get("missing_metrics", [])[:25],
        },
        "analysis_context": analysis_context,
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt, default=str)}
        ],
    )

    return response.choices[0].message.content
