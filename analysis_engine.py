def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def get_extracted_metrics(flexible_result):
    return flexible_result.get("extracted_metrics", [])


def metric_matches(item, keywords):
    text = " ".join([
        normalize_text(item.get("metric_name")),
        normalize_text(item.get("category")),
        normalize_text(item.get("source_file")),
        normalize_text(item.get("sheet")),
    ])

    return any(normalize_text(keyword) in text for keyword in keywords)


def has_metric(metrics, keywords):
    return any(metric_matches(item, keywords) for item in metrics)


def has_layer_signal(metrics, layer_keywords):
    return any(
        any(normalize_text(keyword) in normalize_text(item.get("source_file")) for keyword in layer_keywords)
        or any(normalize_text(keyword) in normalize_text(item.get("sheet")) for keyword in layer_keywords)
        for item in metrics
    )


def available_metric_names(metrics, keywords):
    names = []

    for item in metrics:
        if metric_matches(item, keywords):
            name = item.get("metric_name")
            if name and name not in names:
                names.append(name)

    return names


def relationship_check(metrics):
    """
    Relationship-aware evidence checks.
    This is stronger than metric presence alone.
    """

    has_actual = has_layer_signal(
        metrics,
        ["actual", "financial statement", "fs", "2021", "2022", "t12"]
    )

    has_plan = has_layer_signal(
        metrics,
        ["business plan", "budget", "bp", "forecast", "proforma"]
    )

    has_underwriting = has_layer_signal(
        metrics,
        ["acquisition", "underwriting", "uw"]
    )

    has_debt_source = has_layer_signal(
        metrics,
        ["debt", "loan", "dscr"]
    )

    has_leasing_source = has_layer_signal(
        metrics,
        ["rent roll", "lease", "tenant", "rollover"]
    )

    has_noi = has_metric(metrics, ["noi", "net operating income"])
    has_revenue = has_metric(metrics, ["revenue", "income"])
    has_expense = has_metric(metrics, ["expense", "opex", "operating expense"])
    has_occupancy = has_metric(metrics, ["occupancy"])
    has_variance = has_metric(metrics, ["variance", "budget vs actual"])

    has_dscr = has_metric(metrics, ["dscr", "debt service coverage"])
    has_debt_yield = has_metric(metrics, ["debt yield"])
    has_ltv = has_metric(metrics, ["ltv", "loan to value"])
    has_debt_service = has_metric(metrics, ["debt service", "loan payment"])
    has_debt_balance = has_metric(metrics, ["debt balance", "loan balance", "outstanding debt"])

    has_capex = has_metric(metrics, ["capex", "capital expenditure", "capital cost"])
    has_capex_roi = has_metric(metrics, ["capex roi", "return on capex"])
    has_yield_on_cost = has_metric(metrics, ["yield on cost"])
    has_incremental_noi = has_metric(metrics, ["incremental noi"])
    has_cost_to_complete = has_metric(metrics, ["cost to complete", "remaining cost"])

    has_basis = has_metric(metrics, ["basis", "cost basis", "total basis"])
    has_value = has_metric(metrics, ["value", "valuation", "market value"])
    has_cap_rate = has_metric(metrics, ["cap rate", "capitalization rate"])
    has_irr = has_metric(metrics, ["irr", "internal rate"])
    has_equity_multiple = has_metric(metrics, ["equity multiple", "multiple"])

    has_walt = has_metric(metrics, ["walt", "wale", "weighted average lease"])
    has_tenant_concentration = has_metric(metrics, ["tenant concentration", "top tenant"])
    has_delinquency = has_metric(metrics, ["delinquency", "bad debt", "credit loss"])
    has_rollover = has_metric(metrics, ["rollover", "expiration", "lease expiry"])

    return {
        "layer_signals": {
            "actuals_present": has_actual,
            "business_plan_or_forecast_present": has_plan,
            "acquisition_underwriting_present": has_underwriting,
            "debt_source_present": has_debt_source,
            "leasing_source_present": has_leasing_source,
        },
        "metric_signals": {
            "noi": has_noi,
            "revenue": has_revenue,
            "expense": has_expense,
            "occupancy": has_occupancy,
            "variance": has_variance,
            "dscr": has_dscr,
            "debt_yield": has_debt_yield,
            "ltv": has_ltv,
            "debt_service": has_debt_service,
            "debt_balance": has_debt_balance,
            "capex": has_capex,
            "capex_roi": has_capex_roi,
            "yield_on_cost": has_yield_on_cost,
            "incremental_noi": has_incremental_noi,
            "cost_to_complete": has_cost_to_complete,
            "basis": has_basis,
            "value": has_value,
            "cap_rate": has_cap_rate,
            "irr": has_irr,
            "equity_multiple": has_equity_multiple,
            "walt": has_walt,
            "tenant_concentration": has_tenant_concentration,
            "delinquency": has_delinquency,
            "rollover": has_rollover,
        },
    }


def assess_question(question, available, missing, relationship_required, relationship_met, limitations):
    metric_coverage_pct = len(available) / (len(available) + len(missing)) if (available or missing) else 0

    if relationship_required and not relationship_met:
        coverage = "partial" if metric_coverage_pct >= 0.4 else "low"
    elif metric_coverage_pct >= 0.75:
        coverage = "high"
    elif metric_coverage_pct >= 0.4:
        coverage = "partial"
    else:
        coverage = "low"

    return {
        "question": question,
        "coverage": coverage,
        "available_metrics": available,
        "missing_metrics": missing,
        "coverage_pct": round(metric_coverage_pct, 2),
        "relationship_required": relationship_required,
        "relationship_met": relationship_met,
        "limitations": limitations,
    }


def core_question_coverage(flexible_result):
    metrics = get_extracted_metrics(flexible_result)
    signals = relationship_check(metrics)

    layers = signals["layer_signals"]
    m = signals["metric_signals"]

    results = []

    # 1. Are we performing vs plan?
    available = []
    missing = []

    for label, exists in [
        ("NOI", m["noi"]),
        ("Revenue", m["revenue"]),
        ("Expense", m["expense"]),
        ("Occupancy", m["occupancy"]),
        ("Variance / Budget vs Actual", m["variance"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        layers["actuals_present"]
        and layers["business_plan_or_forecast_present"]
        and (m["noi"] or m["revenue"] or m["expense"])
    )

    limitations = []
    if not layers["actuals_present"]:
        limitations.append("Actual operating data not clearly detected.")
    if not layers["business_plan_or_forecast_present"]:
        limitations.append("Business plan, budget, or forecast data not clearly detected.")
    if not relationship_met:
        limitations.append("Performance vs plan requires both actuals and plan/budget data for comparable periods.")

    results.append(assess_question(
        "Are we performing vs plan?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    # 2. Is income durable?
    available = []
    missing = []

    for label, exists in [
        ("WALT / WALE", m["walt"]),
        ("Occupancy", m["occupancy"]),
        ("Tenant Concentration", m["tenant_concentration"]),
        ("Delinquency / Bad Debt", m["delinquency"]),
        ("Rollover / Expiration", m["rollover"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        layers["leasing_source_present"]
        and (m["walt"] or m["rollover"] or m["tenant_concentration"])
    )

    limitations = []
    if not layers["leasing_source_present"]:
        limitations.append("Rent roll, lease, or tenant-level source not clearly detected.")
    if not relationship_met:
        limitations.append("Income durability requires lease structure, rollover, tenant concentration, or tenant health data.")

    results.append(assess_question(
        "Is the income durable?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    # 3. Is leverage healthy?
    available = []
    missing = []

    for label, exists in [
        ("DSCR", m["dscr"]),
        ("Debt Yield", m["debt_yield"]),
        ("LTV", m["ltv"]),
        ("Debt Balance", m["debt_balance"]),
        ("Debt Service", m["debt_service"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        layers["debt_source_present"]
        and (m["dscr"] or m["debt_yield"] or m["ltv"] or m["debt_service"])
    )

    limitations = []
    if not layers["debt_source_present"]:
        limitations.append("Debt model, loan statement, or debt schedule not clearly detected.")
    if not relationship_met:
        limitations.append("Leverage health requires debt terms and operating income / coverage metrics.")

    results.append(assess_question(
        "Is the leverage healthy?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    # 4. Is further capital justified?
    available = []
    missing = []

    for label, exists in [
        ("CapEx", m["capex"]),
        ("CapEx ROI", m["capex_roi"]),
        ("Yield on Cost", m["yield_on_cost"]),
        ("Incremental NOI", m["incremental_noi"]),
        ("Cost to Complete", m["cost_to_complete"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        m["capex"]
        and (m["incremental_noi"] or m["capex_roi"] or m["yield_on_cost"])
    )

    limitations = []
    if not m["capex"]:
        limitations.append("CapEx spend or budget not detected.")
    if not relationship_met:
        limitations.append("Capital justification requires linking capital spend to incremental NOI, yield on cost, or value creation.")

    results.append(assess_question(
        "Is further capital justified?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    # 5. Is the asset worth its basis?
    available = []
    missing = []

    for label, exists in [
        ("Basis", m["basis"]),
        ("Value", m["value"]),
        ("Cap Rate", m["cap_rate"]),
        ("IRR", m["irr"]),
        ("Equity Multiple", m["equity_multiple"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        (layers["acquisition_underwriting_present"] or m["basis"])
        and (m["value"] or m["cap_rate"] or m["irr"] or m["equity_multiple"])
    )

    limitations = []
    if not layers["acquisition_underwriting_present"] and not m["basis"]:
        limitations.append("Acquisition basis or cost basis not clearly detected.")
    if not relationship_met:
        limitations.append("Worth-basis analysis requires basis plus valuation or return metrics.")

    results.append(assess_question(
        "Is the asset worth its basis?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    # 6. Is risk increasing or decreasing over time?
    available = []
    missing = []

    for label, exists in [
        ("NOI / NOI Trend", m["noi"]),
        ("Revenue / Revenue Trend", m["revenue"]),
        ("Expense / Expense Trend", m["expense"]),
        ("DSCR / DSCR Trend", m["dscr"]),
        ("Occupancy / Occupancy Trend", m["occupancy"]),
    ]:
        available.append(label) if exists else missing.append(label)

    relationship_met = (
        layers["actuals_present"]
        and (m["noi"] or m["revenue"] or m["expense"] or m["dscr"] or m["occupancy"])
    )

    limitations = []
    if not layers["actuals_present"]:
        limitations.append("Actual time-series data not clearly detected.")
    if not relationship_met:
        limitations.append("Risk trend analysis requires metrics across time, not just a single static data point.")

    results.append(assess_question(
        "Is risk increasing or decreasing over time?",
        available,
        missing,
        relationship_required=True,
        relationship_met=relationship_met,
        limitations=limitations,
    ))

    return results


def summarize_extracted_metrics(flexible_result, limit=80):
    extracted = flexible_result.get("extracted_metrics", [])

    simplified = []

    for item in extracted[:limit]:
        simplified.append({
            "metric_name": item.get("metric_name"),
            "category": item.get("category"),
            "value": item.get("value"),
            "source_file": item.get("source_file"),
            "sheet": item.get("sheet"),
            "value_cell": item.get("value_cell"),
            "confidence": item.get("confidence"),
        })

    return simplified


def summarize_missing_metrics(flexible_result, limit=80):
    missing = flexible_result.get("missing_metrics", [])

    high_priority = [
        item for item in missing
        if normalize_text(item.get("priority")) == "high"
    ]

    selected = high_priority[:limit] if high_priority else missing[:limit]

    simplified = []

    for item in selected:
        simplified.append({
            "metric_name": item.get("metric_name"),
            "category": item.get("category"),
            "definition": item.get("definition"),
            "priority": item.get("priority"),
            "source": item.get("source"),
        })

    return simplified


def assess_file_signal(flexible_result):
    total_metrics = flexible_result.get("total_metrics", 0)
    extracted_count = flexible_result.get("extracted_count", 0)

    if total_metrics == 0:
        return {
            "status": "catalog_error",
            "message": "Metric catalog did not load correctly."
        }

    if extracted_count == 0:
        return {
            "status": "no_metrics_found",
            "message": (
                "No recognizable real estate metrics were extracted from the uploaded files. "
                "The files may be blank, unsupported, or not relevant to the current metric catalog."
            )
        }

    if extracted_count < 5:
        return {
            "status": "very_limited_data",
            "message": (
                "Only a small number of metrics were extracted. "
                "Analysis should be treated as highly preliminary."
            )
        }

    return {
        "status": "metrics_found",
        "message": "Metric extraction produced enough signal for preliminary analysis."
    }


def generate_performance_analysis(flexible_result):
    metrics = get_extracted_metrics(flexible_result)

    coverage = core_question_coverage(flexible_result)
    file_signal = assess_file_signal(flexible_result)
    relationships = relationship_check(metrics)

    analysis_context = {
        "analysis_mode": "generalized_relationship_aware_extraction",
        "file_signal": file_signal,
        "metric_catalog_coverage": {
            "total_metrics": flexible_result.get("total_metrics", 0),
            "extracted_count": flexible_result.get("extracted_count", 0),
            "missing_count": flexible_result.get("missing_count", 0),
        },
        "relationship_signals": relationships,
        "core_question_coverage": coverage,
        "extracted_metrics_sample": summarize_extracted_metrics(flexible_result),
        "missing_metrics_sample": summarize_missing_metrics(flexible_result),
        "instruction_to_gpt": (
            "Generate preliminary asset management analysis only from available extracted metrics. "
            "If data is insufficient, say so clearly. Explain what can and cannot be assessed. "
            "Do not invent financial values or assume missing underwriting, business plan, actual, debt, or leasing data. "
            "Pay attention to whether relationships exist, not just whether individual metrics are present."
        ),
    }

    return analysis_context
