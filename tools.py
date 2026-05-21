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
from flexible_extractor import (
    scan_workbook_for_all_metrics,
    classify_file_layer,
    filter_catalog_for_layer,
)


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


def ingest_to_ssot_with_layer(filename: str, layer: str) -> dict[str, Any]:
    """
    Manual-override variant: skip classification, write to the user-specified
    layer. Used when the auto-classifier returns 'unknown' and the user picks
    the right layer from a dropdown.
    """
    if layer not in ssot.KNOWN_LAYERS:
        return {"error": f"Unknown layer: {layer!r}. Valid: {sorted(ssot.KNOWN_LAYERS)}"}

    # Pass layer so extraction only scans relevant metrics for this file type
    extraction = extract_from_file(filename, layer=layer)
    if "error" in extraction:
        return extraction

    ssot.write_layer(
        layer=layer,
        metrics=extraction["metrics"],
        source_file=filename,
    )

    # Recompute derived metrics now that SSOT has new data
    calc_result = calculations.calculate_derived_metrics()

    return {
        "filename":          filename,
        "layer":             layer,
        "metric_count":      extraction["extracted_count"],
        "scanned_count":     extraction.get("scanned_count", extraction["extracted_count"]),
        "catalog_size":      extraction["catalog_size"],
        "layers_now_present":ssot.list_layers(),
        "calculated":        calc_result["computed"],
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
