"""
tools.py — the agent's callable tools.

Wraps deterministic Python (extraction, SSOT writes, classification) into
small, well-described functions the LLM can call via OpenAI function-calling.

Design rules:
  - Every tool returns a JSON-serializable dict.
  - Errors are returned as {"error": "..."} rather than raised. The agent
    reads the message and reacts (this is much more forgiving than exceptions).
  - Tools never call other tools internally except through composition
    (e.g. `ingest_to_ssot` calls `classify_file` and `extract_from_file`).
  - The scenario tools (`run_deal_review`, `run_perf_vs_plan`) are the only
    tools that themselves invoke an LLM; everything else is pure Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ssot
from metric_catalog import load_metric_catalog
import calculations
import logging, sys
from flexible_extractor import (
    scan_workbook_for_all_metrics,
    extract_raw_labeled_pairs,
    classify_file_layer,
    filter_catalog_for_layer,
)
from scenarios._llm import run_raw_insight_pass, llm_available

# Logger writes to stdout so messages show up in Streamlit Cloud logs
_log = logging.getLogger("fb.tools")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[fb.tools] %(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)


UPLOAD_DIR = Path("uploads")


# =============================================================================
# Ingestion tools — get files into SSOT
# =============================================================================

def list_uploaded_files() -> dict[str, Any]:
    """List files currently in the uploads/ directory."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    files = [f.name for f in UPLOAD_DIR.iterdir() if f.is_file() and not f.name.startswith(".")]
    return {"files": sorted(files), "count": len(files)}


def classify_file(filename: str) -> dict[str, Any]:
    """
    Classify a single file by its investment lifecycle layer.
    Uses filename heuristics; reliable when files follow conventional naming
    (e.g. 'Acquisition Underwriting.xlsx', 'Financial Statement 2022.xlsx').
    """
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        return {"error": f"File not found in uploads/: {filename}"}

    layer = classify_file_layer(filename)

    return {
        "filename": filename,
        "layer": layer,
        "confidence": "high" if layer != "unknown" else "low",
    }


def extract_from_file(filename: str, layer: str | None = None) -> dict[str, Any]:
    """
    Extract metrics from a single Excel file using the metric catalog.

    If `layer` is provided, only scans metrics relevant to that SSOT layer:
      - skips calculated metrics (derived later, not extracted from cells)
      - skips metrics whose data_nature doesn't match the layer's expected type
        (e.g. won't scan for Current LTV in an underwriting file)
    """
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        return {"error": f"File not found in uploads/: {filename}"}

    if file_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return {"error": f"Only Excel files supported in v2. Got: {file_path.suffix}"}

    full_catalog = load_metric_catalog()
    scan_catalog = (
        filter_catalog_for_layer(full_catalog, layer) if layer
        else [m for m in full_catalog if m.get("metric_source", "extracted") == "extracted"]
    )

    matches_by_id = scan_workbook_for_all_metrics(file_path, scan_catalog)

    extracted = []
    for match in matches_by_id.values():
        if match:
            extracted.append({
                "metric_name": match["metric_name"],
                "value":       match["value"],
                "sheet":       match["sheet"],
                "value_cell":  match["value_cell"],
                "confidence":  match["confidence"],
            })

    return {
        "filename":        filename,
        "metrics":         extracted,
        "extracted_count": len(extracted),
        "catalog_size":    len(full_catalog),
        "scanned_count":   len(scan_catalog),
        "layer":           layer,
    }


def ingest_to_ssot(filename: str) -> dict[str, Any]:
    """
    Classify + extract + write to SSOT in a single operation.
    This is the tool an agent should typically call when a file is uploaded.
    """
    classification = classify_file(filename)
    if "error" in classification:
        return classification

    layer = classification["layer"]
    if layer == "unknown":
        return {
            "error": (
                f"Could not auto-classify '{filename}' from its name. "
                "Either rename it (e.g. add 'Acquisition Underwriting', "
                "'Business Plan', or 'Financial Statement 2022' to the filename) "
                "or use the manual layer-override below."
            ),
            "needs_manual_classification": True,
            "filename": filename,
        }

    return ingest_to_ssot_with_layer(filename, layer)


# =============================================================================
# Pass 2 field list — what to ask GPT to find for each layer
# =============================================================================
#
# These are the fields the SCENARIOS need that the catalog often can't capture:
#   - inferred characteristics (property_type, deal_type, strategy)
#   - derived sums (total_debt = acquisition + construction loans)
#   - context-dependent fields (capital outlay after closing)
#   - metrics the catalog might know about but with aliases not matching this
#     specific model
#
# Each entry: {name, type, hint}
#   name: stable field name the scenario template will reference
#   type: "number" | "string" — guides GPT's output format
#   hint: short note on what to look for (helps GPT find non-obvious fields)
#
# Adding a new field here makes it available to ALL scenarios. Each scenario's
# template prompt then decides which of these fields it wants to surface.

_UNDERWRITING_FIELDS_TO_FIND = [
    # ─── Characterization (inferred, not extracted) ──────────────────────
    {"name": "property_type",       "type": "string",
     "hint": "Multifamily / Office / Industrial / Retail / Hotel / Mixed-use / Conversion / Ground-up Development"},
    {"name": "deal_type",           "type": "string",
     "hint": "Acquisition / Ground-up Development / Conversion / Value-Add Renovation / Recapitalization"},
    {"name": "strategy",            "type": "string",
     "hint": "Core / Core-Plus / Value-Add / Opportunistic — infer from cap rate, occupancy, capex intensity"},
    {"name": "investment_position", "type": "string",
     "hint": "GP/Sponsor / LP / Co-GP / JV — infer from waterfall structure if present"},
    {"name": "asset_name",          "type": "string",
     "hint": "Property name (often in row 1-3 of summary sheet)"},
    {"name": "location",            "type": "string",
     "hint": "City, State or Submarket"},

    # ─── Asset basics ────────────────────────────────────────────────────
    {"name": "total_units",         "type": "number",
     "hint": "Total residential units / hotel keys / doors. Often in unit mix total row labeled 'Total / Wtd. Avg.'"},
    {"name": "total_sf",            "type": "number",
     "hint": "Total GSF / NRA / GLA — gross or rentable square footage of the asset"},

    # ─── Debt structure ──────────────────────────────────────────────────
    {"name": "total_debt",          "type": "number",
     "hint": "SUM of ALL loans (acquisition + construction + mezz). Show derivation in label_in_file."},
    {"name": "construction_loan",   "type": "number",
     "hint": "Construction loan / future funding commitment if separate from acquisition loan"},
    {"name": "loan_term_months",    "type": "number",
     "hint": "Loan term in months (e.g. '36 months I/O + 24 amortizing' → 60)"},
    {"name": "io_period_months",    "type": "number",
     "hint": "Interest-only period in months if specified"},
    {"name": "ltv",                 "type": "number",
     "hint": "Loan-to-value (debt / value). May be implied if not labeled — debt / purchase price for stabilized, or debt / stabilized value for dev."},

    # ─── Equity & waterfall ──────────────────────────────────────────────
    {"name": "lp_equity",           "type": "number",
     "hint": "LP equity contribution amount in $"},
    {"name": "gp_equity",           "type": "number",
     "hint": "GP/Sponsor equity contribution amount in $"},
    {"name": "lp_gp_split",         "type": "string",
     "hint": "Equity split as 'XX% LP / XX% GP'"},
    {"name": "preferred_return",    "type": "number",
     "hint": "LP preferred return percentage (e.g. 0.08 for 8%)"},
    {"name": "gp_promote",          "type": "string",
     "hint": "GP promote structure (e.g. '20% above 8% pref')"},

    # ─── Capital plan ────────────────────────────────────────────────────
    {"name": "capital_outlay_after_closing", "type": "number",
     "hint": "Total spend post-close: CapEx + construction draws + interest reserve + post-close soft costs"},
    {"name": "ti_lc_budget",        "type": "number",
     "hint": "Tenant improvement and leasing commission budget if present"},

    # ─── NOI bridge ──────────────────────────────────────────────────────
    {"name": "going_in_noi",        "type": "number",
     "hint": "NOI at acquisition (year 1, T12, or as-is). May be 0 for ground-up development."},
    {"name": "stabilized_noi",      "type": "number",
     "hint": "Stabilized / target NOI after lease-up or business plan completion"},
    {"name": "noi_uplift_pct",      "type": "number",
     "hint": "Percentage increase from going-in to stabilized NOI"},

    # ─── Returns the catalog might miss ──────────────────────────────────
    {"name": "cash_on_cash_year1",  "type": "number",
     "hint": "Year 1 cash-on-cash return — cash flow after debt service / equity invested"},
    {"name": "lp_irr",              "type": "number",
     "hint": "LP-specific IRR (after promote distribution) if modeled separately from deal-level IRR"},
    {"name": "lp_equity_multiple",  "type": "number",
     "hint": "LP-specific equity multiple if modeled separately"},
    {"name": "break_even_occupancy", "type": "number",
     "hint": "Occupancy required to cover debt service + operating expenses"},

    # ─── Risk context ────────────────────────────────────────────────────
    {"name": "key_risks",           "type": "string",
     "hint": "2-3 most material risks visible from the model — lease-up risk, market rent assumption risk, construction cost overrun risk, exit cap rate sensitivity, etc."},
]

_PERF_VS_PLAN_FIELDS_TO_FIND = [
    {"name": "reporting_period",    "type": "string",
     "hint": "What period does this report cover? (Year/Quarter/Month)"},
    {"name": "property_type",       "type": "string",
     "hint": "Property type — same options as underwriting"},
    {"name": "noi",                 "type": "number",
     "hint": "Actual NOI for the period"},
    {"name": "revenue",             "type": "number",
     "hint": "Effective Gross Revenue / Total Revenue for the period"},
    {"name": "expenses",            "type": "number",
     "hint": "Total Operating Expenses for the period"},
    {"name": "occupancy",           "type": "number",
     "hint": "Physical occupancy as percentage"},
    {"name": "current_loan_balance", "type": "number",
     "hint": "Outstanding loan balance at period-end"},
    {"name": "current_ltv",         "type": "number",
     "hint": "Current LTV based on most recent value"},
    {"name": "covenant_status",     "type": "string",
     "hint": "Debt covenant compliance status if mentioned"},
    {"name": "key_observations",    "type": "string",
     "hint": "Notable variances, unusual items, one-time events"},
]


def _build_fields_to_find(layer: str, found_metric_names: list[str]) -> list[dict]:
    """
    Return the list of fields Pass 2 should look for, scoped to the layer.
    Filters out fields whose name overlaps with metrics already found
    (so Pass 2 doesn't re-do deterministic work).
    """
    if layer == "underwriting":
        base = _UNDERWRITING_FIELDS_TO_FIND
    elif layer == "business_plan":
        base = _UNDERWRITING_FIELDS_TO_FIND + _PERF_VS_PLAN_FIELDS_TO_FIND
    else:
        base = _PERF_VS_PLAN_FIELDS_TO_FIND

    # Lowercase the found metric names for fuzzy overlap check
    found_lower = {n.lower() for n in found_metric_names}

    def already_found(field_name: str) -> bool:
        fn_lower = field_name.lower().replace("_", " ")
        return any(
            fn_lower in found.lower() or found.lower() in fn_lower
            for found in found_metric_names
        )

    return [f for f in base if not already_found(f["name"])]


def ingest_to_ssot_with_layer(filename: str, layer: str) -> dict[str, Any]:
    """
    Classify + extract + GPT insight pass + write to SSOT.

    Two-pass ingest:
      Pass 1 (deterministic): metric catalog extraction → structured SSOT metrics
      Pass 2 (GPT):           raw labeled-pair read → inferred characteristics,
                              gap-filled metrics, key observations

    Pass 2 runs at ingest time so every downstream scenario benefits automatically.
    It uses gpt-4o-mini to stay cheap (~$0.01–0.02 per file).
    If no API key is set, Pass 2 is silently skipped.
    """
    if layer not in ssot.KNOWN_LAYERS:
        return {"error": f"Unknown layer: {layer!r}. Valid: {sorted(ssot.KNOWN_LAYERS)}"}

    file_path = UPLOAD_DIR / filename

    # --- Pass 1: deterministic metric extraction ---
    extraction = extract_from_file(filename, layer=layer)
    if "error" in extraction:
        return extraction

    _log.info(
        "INGEST %s as layer=%s — Pass 1 found %d metrics, llm_available=%s",
        filename, layer, extraction["extracted_count"], llm_available(),
    )

    # --- Pass 2: targeted GPT gap-fill + surface insights ---
    raw_insights: dict[str, Any] | None = None
    if llm_available():
        found_names = [m["metric_name"] for m in extraction["metrics"]]

        # Build the FIELDS TO FIND list for Pass 2.
        # Pass 2 needs to find:
        #   (a) Characterization fields (property_type, etc.) that are inferred,
        #       not catalog-extracted
        #   (b) Catalog metrics the deterministic pass missed (filtered to ones
        #       relevant for this layer)
        #   (c) Derived/summed fields (Total Debt, Capital Outlay After Closing)
        #       that no single catalog metric captures
        fields_to_find = _build_fields_to_find(layer, found_names)

        labeled_pairs = extract_raw_labeled_pairs(file_path)
        raw_insights = run_raw_insight_pass(
            labeled_pairs,
            layer,
            filename,
            found_metric_names=found_names,
            fields_to_find=fields_to_find,
        )

    # Write both passes to SSOT
    ssot.write_layer(
        layer=layer,
        metrics=extraction["metrics"],
        source_file=filename,
        raw_insights=raw_insights,
    )

    # Recompute derived metrics now that SSOT has new data
    calc_result = calculations.calculate_derived_metrics()

    return {
        "filename":           filename,
        "layer":              layer,
        "metric_count":       extraction["extracted_count"],
        "scanned_count":      extraction.get("scanned_count", extraction["extracted_count"]),
        "catalog_size":       extraction["catalog_size"],
        "layers_now_present": ssot.list_layers(),
        "calculated":         calc_result["computed"],
        "insight_pass":       "completed" if raw_insights else "skipped (no API key)",
    }


# =============================================================================
# SSOT read tools
# =============================================================================

def get_ssot_summary() -> dict[str, Any]:
    """Compact summary: layers present, files ingested, last update time."""
    return ssot.ssot_summary()


def get_layer_details(layer: str) -> dict[str, Any]:
    """Return all metrics stored in one SSOT layer."""
    layer_data = ssot.read_layer(layer)
    if not layer_data:
        return {"error": f"Layer '{layer}' is not present in SSOT yet."}
    return {
        "layer": layer,
        "source_file": layer_data["source_file"],
        "metric_count": layer_data["metric_count"],
        "metrics": layer_data["metrics"],
    }


def check_scenario_ready(scenario: str) -> dict[str, Any]:
    """Check whether SSOT has enough data to run a given scenario."""
    return ssot.scenario_ready(scenario)


# =============================================================================
# Scenario tools — the only tools that themselves invoke an LLM
# =============================================================================

def run_deal_review() -> dict[str, Any]:
    """
    Run the Deal Review scenario. Reads the underwriting layer from SSOT and
    returns an executive summary + missing-info checklist.
    """
    from scenarios.deal_review import generate_deal_review
    return generate_deal_review()


def run_perf_vs_plan() -> dict[str, Any]:
    """
    Run the Performance vs Plan scenario. Reads UW (or BP) + actuals from SSOT
    and returns a chronological variance narrative.
    """
    from scenarios.perf_vs_plan import generate_perf_vs_plan
    return generate_perf_vs_plan()


# =============================================================================
# OpenAI function-calling schemas
# =============================================================================

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_uploaded_files": {
        "type": "function",
        "function": {
            "name": "list_uploaded_files",
            "description": "List files currently sitting in the uploads/ folder, so you can see what the user has provided.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "classify_file": {
        "type": "function",
        "function": {
            "name": "classify_file",
            "description": "Classify a single uploaded file by its investment lifecycle layer (underwriting, business_plan, actuals_2021, actuals_2022, etc.). Filename-based heuristic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of a file in the uploads/ folder."},
                },
                "required": ["filename"],
            },
        },
    },
    "extract_from_file": {
        "type": "function",
        "function": {
            "name": "extract_from_file",
            "description": "Run the metric catalog against one Excel file and return all metrics it finds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of a file in the uploads/ folder."},
                },
                "required": ["filename"],
            },
        },
    },
    "ingest_to_ssot": {
        "type": "function",
        "function": {
            "name": "ingest_to_ssot",
            "description": "Classify + extract + write to SSOT in one operation. This is the standard way to onboard a file. Call this for each uploaded file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of a file in the uploads/ folder."},
                },
                "required": ["filename"],
            },
        },
    },
    "get_ssot_summary": {
        "type": "function",
        "function": {
            "name": "get_ssot_summary",
            "description": "Get a compact summary of what's currently in SSOT: which layers, which files were ingested, last update time. Call this to orient yourself.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "get_layer_details": {
        "type": "function",
        "function": {
            "name": "get_layer_details",
            "description": "Get all metrics stored in one SSOT layer (e.g. underwriting, actuals_2022). Use this when you need specific numbers to cite.",
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "description": "Layer name like 'underwriting', 'business_plan', 'actuals_2021', 'actuals_2022'.",
                    },
                },
                "required": ["layer"],
            },
        },
    },
    "check_scenario_ready": {
        "type": "function",
        "function": {
            "name": "check_scenario_ready",
            "description": "Check whether SSOT has enough data to run a given scenario. Returns {ready: true/false, reason, layers_present}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario": {
                        "type": "string",
                        "enum": ["deal_review", "perf_vs_plan"],
                    },
                },
                "required": ["scenario"],
            },
        },
    },
    "run_deal_review": {
        "type": "function",
        "function": {
            "name": "run_deal_review",
            "description": "Generate the Deal Review narrative. Call this ONLY after the underwriting layer is in SSOT. Returns markdown text summarizing the deal thesis and listing missing data.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "run_perf_vs_plan": {
        "type": "function",
        "function": {
            "name": "run_perf_vs_plan",
            "description": "Generate the Performance vs Plan narrative. Call this ONLY after both a plan layer (UW or BP) AND at least one actuals layer are in SSOT. Returns markdown text with chronological variance analysis.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
}


# Tool name -> Python implementation
TOOL_IMPLEMENTATIONS: dict[str, Any] = {
    "list_uploaded_files": list_uploaded_files,
    "classify_file": classify_file,
    "extract_from_file": extract_from_file,
    "ingest_to_ssot": ingest_to_ssot,
    "get_ssot_summary": get_ssot_summary,
    "get_layer_details": get_layer_details,
    "check_scenario_ready": check_scenario_ready,
    "run_deal_review": run_deal_review,
    "run_perf_vs_plan": run_perf_vs_plan,
}


# Tool subsets exposed per scenario. The Deal Review agent literally cannot
# call run_perf_vs_plan, and vice versa. This is what prevents v1's failure
# mode (the agent inventing scenarios that weren't asked for).
_SHARED_TOOLS = [
    "list_uploaded_files",
    "classify_file",
    "extract_from_file",
    "ingest_to_ssot",
    "get_ssot_summary",
    "get_layer_details",
    "check_scenario_ready",
]

TOOLS_FOR_DEAL_REVIEW = _SHARED_TOOLS + ["run_deal_review"]
TOOLS_FOR_PERF_VS_PLAN = _SHARED_TOOLS + ["run_perf_vs_plan"]


def get_tool_schemas(tool_names: list[str]) -> list[dict[str, Any]]:
    """Return the OpenAI tool-schemas list for a given subset of tool names."""
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch a tool call. Used by the agent loop. Catches exceptions and
    returns them as error dicts so the agent can recover.
    """
    impl = TOOL_IMPLEMENTATIONS.get(tool_name)
    if impl is None:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return impl(**(arguments or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {tool_name}: {e}"}
    except Exception as e:
        return {"error": f"{tool_name} crashed: {type(e).__name__}: {e}"}
