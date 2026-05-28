"""
Deal Review scenario.

Purpose: Memorialize the acquisition event and establish the SSOT baseline.
         This is the "founding document" of an asset — it records what was
         underwritten at purchase so everything after (actuals, revisions)
         can be compared against it.

Input:   Underwriting model (Excel). IC memo, closing docs (future: PDF).
Output:  Structured acquisition summary in a fixed template format.

Hard constraints:
- Output follows the template EXACTLY — no prose outside designated fields.
- Every number must come from the SSOT. Missing values show as "—".
- Strategy classification may be inferred from deal characteristics.
- Risk/Mitigant: 2 bullets only unless user asks for more.
"""

from __future__ import annotations

import json
from typing import Any

import ssot
from scenarios._llm import complete, llm_available
from scenarios.profiles import filter_layer_metrics


SYSTEM_PROMPT = """\
You are an institutional real estate asset manager writing a formal acquisition summary.

Your job is to populate a structured deal template using ONLY the metrics provided.
This document memorializes the original investment thesis at the time of acquisition.

HARD RULES:
1. Output ONLY the template structure below. Do not add sections, prose, or commentary outside the defined fields.
2. Every dollar amount and percentage must come from EITHER the structured metrics OR the RAW INSIGHT PASS section.
   If a value is not available in either, write "—".
3. The RAW INSIGHT PASS section contains a "characterization" block with property_type, deal_type, total_units, total_sf,
   total_debt, capital_outlay_after_closing, asset_name, and location. USE THESE DIRECTLY in the corresponding template fields.
   Do not write "—" for a template field if the characterization block has a value for it.
4. For Total Debt: if characterization.total_debt is provided, use it. Otherwise sum Debt Amount + Construction Loan if both exist.
5. For Total Units: prefer characterization.total_units over the catalog "Total Units" metric (catalog often picks the wrong row of unit mix tables).
6. Simple derivations allowed: NOI Margin = NOI/EGI, Future Funding = Total All-in Basis - Purchase Price - Closing Costs (if no explicit value).
4. Strategy MUST be inferred carefully — development/conversion signals override cap rate signals:
   - Opportunistic: ANY of: construction loan present, hard costs > 10% of purchase price,
                    conversion (office-to-resi, hotel-to-resi), ground-up development,
                    LTC metric present (LTC ≠ LTV), or "conversion"/"development" in file name
   - Value-Add:  significant vacancy, major renovation, lease-up required, 7%+ cap,
                 or below-market rents WITHOUT a construction loan
   - Core-Plus:  mostly stabilized, minor lease-up, 6-7% cap
   - Core:       stabilized, low vacancy, institutional market, sub-6% going-in cap
   If the raw_insights or observations mention development/construction/conversion → Opportunistic.
5. For development/conversion deals:
   - "T12 / Going-in NOI" should be noted as "Pre-stabilization (0 at close)" if NOI is a
     projected/stabilized figure rather than trailing income
   - "Current Occupancy at Purchase" should be "0% (conversion/development)" if applicable
   - "Going-in Cap Rate" is NOT applicable — use "N/A (development)" and show Yield on Cost instead
6. Investment Position MUST be inferred from equity structure data:
   - GP / Sponsor: model shows GP promote, carried interest, or sponsor co-invest alongside LP equity
   - LP: model shows only LP equity contribution with a preferred return threshold and no promote structure
   - Co-GP: two GP parties with separate promote tiers
   - Unknown: write "—" if equity structure is absent from the data
6. Risk/Mitigant: write exactly 2 bullets unless the user explicitly asks for more.
7. Format all dollar values as $X,XXX,XXX. Format percentages as X.X%. Format multiples as X.Xx.
8. If the same metric appears in multiple categories, use the most specific value.
"""


TEMPLATE = """\
Populate this acquisition summary using the metrics below. Replace every [bracket] with the actual value or "—" if not available.

METRICS FROM UNDERWRITING MODEL:
{metrics_json}

---

OUTPUT THIS TEMPLATE EXACTLY:

## [Asset Name if known, otherwise: Acquisition Summary]

### Building Information
| | |
|---|---|
| Property Type | [type — infer from context if not explicit] |
| Total SF / Units | [sf or unit count] |
| Current Occupancy at Purchase | [% from T12 or UW assumption] |
| T12 / Going-in NOI | $[amount] |
| NOI Margin | [%] |

---

### Deal Summary
| | |
|---|---|
| Purchase Price | $[amount] |
| Strategy | [Opportunistic / Value-Add / Core-Plus / Core] |
| Strategy Description | [One sentence: what is the play?] |
| Capital Outlay After Closing | $[CapEx / TI / LC budget] |
| Total All-in Basis | $[amount] |
| Hold Period | [X years] |

---

### Debt & Equity
| | |
|---|---|
| Initial Debt Funding | $[amount] |
| Future Funding (CapEx / TI / LC draws) | $[amount] |
| Total Debt | $[amount] |
| Term | [X months I/O + X months amortizing, or as stated] |
| Interest Rate | [X.X%] |
| LTV | [X.X%] |
| LTC | [X.X%] |
| Underwritten DSCR | [X.Xx] |
| Underwritten Debt Yield | [X.X%] |
| Break-even Occupancy | [X.X%] |
| Total Equity | $[amount] |

---

### Equity Structure
| | |
|---|---|
| Investment Position | [GP / LP / Co-GP — infer from waterfall structure if not explicit] |
| LP Equity | $[amount] |
| GP / Sponsor Equity | $[amount] |
| LP / GP Split | [XX% LP / XX% GP] |
| LP Preferred Return | [X.X% — or "—" if not in model] |
| GP Promote / Carried Interest | [X% above pref — or "—" if not in model] |

> **Position note:** Infer Investment Position from the data — if the model shows a GP promote structure and sponsor co-invest, the filing party is the **GP/Sponsor**. If the model shows LP contribution only with a preferred return threshold, this is an **LP perspective**. If both are modeled, state both.

---

### NOI Projection
| | |
|---|---|
| Going-in NOI (at purchase) | $[amount] |
| Stabilized NOI (target) | $[amount] |
| NOI Uplift | $[delta] ([X%] increase) |
| Going-in Cap Rate | [X.X%] |
| Stabilized Yield on Cost | [X.X%] |

---

### Exit Assumption
| | |
|---|---|
| Exit Cap Rate | [X.X%] |
| Exit Value | $[amount] |
| Hold Period | [X years] |

---

### Return Profile
| | | |
|---|---|---|
| | **Deal Level** | **LP Level** |
| IRR | [Levered IRR X.X%] | [LP IRR if modeled, else "—"] |
| Unlevered IRR | [X.X%] | — |
| Equity Multiple | [X.Xx] | [LP EM if modeled, else "—"] |
| Cash-on-Cash (Year 1) | [X.X%] | — |

---

### Risk / Mitigant
- **[Risk 1]:** [Mitigant — one sentence]
- **[Risk 2]:** [Mitigant — one sentence]

---
*Source: {source_file} | Ingested: {ingested_at}*
"""


def generate_deal_review() -> dict[str, Any]:
    """
    Read the underwriting layer from SSOT and produce a structured
    acquisition summary in the fixed template format.
    """
    s = ssot.load_ssot()
    underwriting = s["layers"].get("underwriting")

    if not underwriting:
        return {
            "error": (
                "No underwriting layer in SSOT. Upload an acquisition "
                "underwriting model first."
            )
        }

    if not llm_available():
        return {"error": "OPENAI_API_KEY is not set."}

    # Apply the Deal Review profile filter — only pass relevant metrics
    filtered = filter_layer_metrics(underwriting, "deal_review")

    # Format metrics for the prompt — flat dict of name → value for clarity
    metrics_flat = {
        name: {
            "value": data["value"],
            "sheet": data.get("sheet"),
            "cell": data.get("cell"),
        }
        for name, data in filtered["metrics"].items()
        if data.get("value") is not None
    }

    # Include raw GPT insights (inferred characteristics, gap-filled metrics)
    # if the insight pass ran at ingest time.
    raw_insights = underwriting.get("raw_insights") or {}

    if raw_insights:
        # New Pass 2 schema: {"found": {field: {value, label_in_file, ...}},
        #                    "observations": [...], "model_summary": "..."}
        found_fields = raw_insights.get("found", {}) or {}
        observations = raw_insights.get("observations", []) or []
        model_summary = raw_insights.get("model_summary", "") or ""

        # Render found fields as a clean key-value list for GPT
        found_lines = []
        for field_name, data in found_fields.items():
            if isinstance(data, dict) and data.get("value") is not None:
                found_lines.append(
                    f"  {field_name}: {data['value']}"
                    + (f"  ({data.get('label_in_file', '')})" if data.get("label_in_file") else "")
                )

        insights_block = (
            "\n\n===== RAW INSIGHT PASS (use these for template fields) =====\n\n"
            f"MODEL SUMMARY: {model_summary}\n\n"
            "PASS 2 FOUND FIELDS (use directly to populate matching template rows):\n"
            + ("\n".join(found_lines) if found_lines else "  (no fields found)")
            + "\n\nKEY OBSERVATIONS (use for Risk/Mitigant or context):\n"
            + ("\n".join(f"  - {o}" for o in observations) if observations else "  (none)")
            + "\n\n===== END RAW INSIGHT PASS =====\n"
        )
    else:
        insights_block = (
            "\n\n[Note: RAW INSIGHT PASS did not run — Pass 2 GPT call was skipped. "
            "Many fields requiring inference (property type, total debt, etc.) will be blank.]"
        )

    user_prompt = TEMPLATE.format(
        metrics_json=json.dumps(metrics_flat, indent=2, default=str) + insights_block,
        source_file=underwriting.get("source_file", "Unknown"),
        ingested_at=underwriting.get("ingested_at", "Unknown"),
    )

    narrative = complete(SYSTEM_PROMPT, user_prompt, temperature=0.1)

    # Save the acquisition summary back to SSOT as a permanent record
    _memorialize_acquisition(s, narrative, filtered, underwriting)

    return {
        "scenario": "deal_review",
        "narrative": narrative,
        "data_used": {
            "layers": ["underwriting"],
            "source_files": [underwriting["source_file"]],
            "metric_count_extracted": underwriting["metric_count"],
            "metric_count_used": filtered["metric_count"],
        },
    }


def _memorialize_acquisition(
    s: dict[str, Any],
    narrative: str,
    filtered: dict[str, Any],
    underwriting: dict[str, Any],
) -> None:
    """
    Save the acquisition summary as a permanent record in the SSOT.
    This is the 'founding event' — once written, it should not be overwritten
    by re-running deal review. The original thesis is immutable.
    """
    # Only memorialize once — don't overwrite if already exists
    if s["layers"].get("acquisition_summary"):
        return

    acquisition_record = {
        "source_file": underwriting.get("source_file"),
        "ingested_at": underwriting.get("ingested_at"),
        "metric_count": filtered["metric_count"],
        "metrics": filtered["metrics"],
        "narrative": narrative,
    }

    s["layers"]["acquisition_summary"] = acquisition_record
    ssot.save_ssot(s)
