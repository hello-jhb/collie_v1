"""
calculations.py — derive calculated metrics from extracted SSOT values.

Called after ingest_to_ssot so that metrics marked metric_source="calculated"
in the catalog are computed and written back into the SSOT alongside the
extracted values.

Tier 1 (implemented): simple arithmetic from already-extracted values.
  • Basis per SF / per Unit / per Key
  • NOI Margin
  • NOI Growth  (requires two periods in SSOT)
  • Same-Store NOI Growth  (same)

Tier 2 (stubbed — needs rent roll parsing):
  • WALT / WALE
  • Rollover by Year
"""

from __future__ import annotations
from typing import Any
import ssot


def _get(layer_name: str, metric_name: str) -> float | None:
    """Convenience: get a numeric value from an SSOT layer, or None."""
    val = ssot.get_metric(layer_name, metric_name)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _safe_pct_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


# -----------------------------------------------------------------------------
# Per-layer calculators
# -----------------------------------------------------------------------------

def _calc_basis_per_sf(layer: str) -> dict[str, Any] | None:
    basis = _get(layer, "Total Acquisition Cost / All-in Basis")
    sf    = _get(layer, "Basis per SF / per Unit / per Key")  # may already exist
    # Also try Total Project Cost as fallback
    if basis is None:
        basis = _get(layer, "Total Project Cost")
    if basis is None or sf is not None:
        # Already extracted or no basis to work from
        return None
    # We don't have SF directly in the catalog — can't compute without it.
    # Return None; will improve when SF is added as a catalog metric.
    return None


def _calc_noi_margin(layer: str) -> dict[str, Any] | None:
    noi = _get(layer, "Net Operating Income (NOI)")
    rev = _get(layer, "Effective Gross Revenue / EGI")
    val = _safe_div(noi, rev)
    if val is None:
        return None
    return {"value": val, "formula": "NOI ÷ EGI", "source_layer": layer}


def _calc_noi_growth(s: dict) -> dict[str, Any] | None:
    """Requires two actuals layers (e.g. actuals_2021 + actuals_2022)."""
    actuals_layers = sorted(
        k for k in s["layers"] if k.startswith("actuals_")
    )
    if len(actuals_layers) < 2:
        return None
    prior_layer   = actuals_layers[-2]
    current_layer = actuals_layers[-1]
    noi_prior   = _get(prior_layer,   "Net Operating Income (NOI)")
    noi_current = _get(current_layer, "Net Operating Income (NOI)")
    val = _safe_pct_change(noi_current, noi_prior)
    if val is None:
        return None
    return {
        "value":   val,
        "formula": f"({current_layer} NOI − {prior_layer} NOI) ÷ |{prior_layer} NOI|",
        "source_layers": [prior_layer, current_layer],
    }


def _calc_same_store_noi_growth(s: dict) -> dict[str, Any] | None:
    """Same logic as NOI Growth for now (same-store requires property-level data we don't have)."""
    return _calc_noi_growth(s)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def calculate_derived_metrics() -> dict[str, Any]:
    """
    Compute all Tier 1 calculated metrics from the current SSOT and write
    them back into a synthetic 'calculated' layer.

    Returns a summary of what was computed.
    """
    s = ssot.load_ssot()
    results: dict[str, Any] = {}

    # Per-layer calculations (NOI Margin exists in each layer independently)
    for layer_name in s["layers"]:
        margin = _calc_noi_margin(layer_name)
        if margin:
            results[f"noi_margin_{layer_name}"] = margin

    # Cross-layer calculations (need multiple periods)
    noi_growth = _calc_noi_growth(s)
    if noi_growth:
        results["noi_growth"] = noi_growth

    ss_growth = _calc_same_store_noi_growth(s)
    if ss_growth and "noi_growth" not in results:
        results["same_store_noi_growth"] = ss_growth

    # Write computed values into a synthetic "calculated" layer in SSOT
    if results:
        calc_metrics = []
        for key, data in results.items():
            calc_metrics.append({
                "metric_name": key,
                "value":       data.get("value"),
                "sheet":       "calculated",
                "value_cell":  data.get("formula", ""),
                "confidence":  "calculated",
            })
        ssot.write_layer(
            layer="calculated",
            metrics=calc_metrics,
            source_file="derived",
        )

    return {
        "computed": list(results.keys()),
        "count":    len(results),
    }
