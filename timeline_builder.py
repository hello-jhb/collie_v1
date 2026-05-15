# timeline_builder.py


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def normalize_document_type(value):
    if not value:
        return "unknown"

    return str(value).strip().lower()


def build_investment_timeline(classification_result):
    """
    Converts classified uploaded files into
    an ordered investment timeline.
    """

    classifications = classification_result.get(
        "classifications",
        []
    )

    timeline_events = []

    for item in classifications:

        document_type = normalize_document_type(
            item.get("document_type")
        )

        lifecycle_role = item.get(
            "investment_lifecycle_role",
            "unknown"
        )

        year = safe_int(
            item.get("likely_year")
        )

        file_name = item.get(
            "file_name",
            "Unknown File"
        )

        confidence = item.get(
            "confidence",
            "unknown"
        )

        relevant_tabs = item.get(
            "relevant_tabs",
            []
        )

        # ---------------------------------------------------
        # Determine timeline category
        # ---------------------------------------------------

        if document_type == "acquisition_underwriting":

            timeline_type = "Acquisition"

            description = (
                "Original acquisition underwriting and "
                "investment thesis."
            )

            priority = 1

        elif document_type == "business_plan":

            timeline_type = "Business Plan"

            description = (
                "Updated operating forecast and "
                "post-acquisition business plan."
            )

            priority = 3

        elif document_type == "financial_statement_actuals":

            timeline_type = "Actual Performance"

            description = (
                "Realized operating performance "
                "from financial reporting."
            )

            priority = 4

        elif document_type == "rent_roll":

            timeline_type = "Leasing Snapshot"

            description = (
                "Tenant, lease, and occupancy evidence."
            )

            priority = 5

        elif document_type == "debt_model":

            timeline_type = "Debt / Capital Structure"

            description = (
                "Debt terms, leverage, and financing structure."
            )

            priority = 6

        elif document_type == "capex_tracker":

            timeline_type = "Capital Execution"

            description = (
                "Capital expenditure and project execution tracking."
            )

            priority = 7

        else:

            timeline_type = "Other"

            description = (
                "Supporting or unclassified investment material."
            )

            priority = 99

        # ---------------------------------------------------
        # Build event
        # ---------------------------------------------------

        event = {
            "timeline_type": timeline_type,
            "document_type": document_type,
            "investment_role": lifecycle_role,
            "year": year,
            "file_name": file_name,
            "description": description,
            "confidence": confidence,
            "relevant_tabs": relevant_tabs,
            "priority": priority,
        }

        timeline_events.append(event)

    # ---------------------------------------------------
    # Sort timeline
    # ---------------------------------------------------

    timeline_events = sorted(
        timeline_events,
        key=lambda x: (
            x["year"] if x["year"] is not None else 9999,
            x["priority"]
        )
    )

    # ---------------------------------------------------
    # Build readable narrative summary
    # ---------------------------------------------------

    summary_lines = []

    for event in timeline_events:

        year_text = (
            str(event["year"])
            if event["year"] is not None
            else "Unknown Period"
        )

        line = (
            f"{year_text} — "
            f"{event['timeline_type']}: "
            f"{event['file_name']}"
        )

        summary_lines.append(line)

    narrative_summary = "\n".join(summary_lines)

    return {
        "timeline_events": timeline_events,
        "timeline_summary": narrative_summary,
    }