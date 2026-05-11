import os

import json

import streamlit as st

from openai import OpenAI

api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=api_key)


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

The system extracts metrics from uploaded files using a predefined institutional metric catalog and core-question framework.

Your task is to:
1. interpret the extracted evidence,
2. identify what can and cannot be concluded,
3. explain why certain missing data matters,
4. synthesize fragmented information into investment-oriented reasoning,
5. generate concise but insightful asset management analysis.

Rules:
1. Do not invent numbers, assumptions, or missing documents.
2. Use only the structured evidence provided.
3. The extracted metrics may come from incomplete or fragmented files.
4. Distinguish between:
   - acquisition underwriting = original investment thesis,
   - business plan = updated expectation,
   - actuals = realized operating performance.
5. If information is insufficient, explicitly state what additional files or metrics are required.
6. Avoid generic “AI assistant” language.
7. Think and write like an experienced institutional asset manager.
8. Focus on diagnostic reasoning, not just reporting.
9. Explain relationships between metrics whenever possible.
10. Emphasize what matters operationally, financially, and from a return perspective.
11. If return adequacy is discussed (IRR, EM, yield, etc.), note that acceptability depends on investor return thresholds and strategy.
12. Prefer synthesis over long lists.
13. The goal is not merely to summarize files, but to reconstruct investment reality from fragmented information.
14. Provide clear, readable, and naturally flowing diagnostic analysis rather than fragmented or isolated observations.
15. Guide the reader logically from operating signals → performance implications → investment consequences.
16. Avoid excessive bullet points unless summarizing key findings.
17. Prioritize narrative coherence and investment reasoning over metric listing.
"""

def generate_asset_management_narrative(analysis_context):
    prompt = {
        "task": "Generate the main asset management performance narrative from the structured evidence.",
        "desired_structure": [
            "One-line diagnosis",
            "Critical metric summary",
            "Core question assessment",
            "Key risks",
            "2023 planning implications",
            "Missing data / limitations"
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
        "task": "Answer the user's follow-up question using the structured property analysis context.",
        "user_question": question,
        "flexible_metric_scan_summary": {
            "total_metrics": flexible_result.get("total_metrics"),
            "extracted_count": flexible_result.get("extracted_count"),
            "missing_count": flexible_result.get("missing_count"),
            "sample_extracted_metrics": flexible_result.get("extracted_metrics", [])[:50],
            "sample_missing_metrics": flexible_result.get("missing_metrics", [])[:50],
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
