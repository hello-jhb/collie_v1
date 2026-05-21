from pathlib import Path
import pandas as pd
import re


CATALOG_PATH = Path("Snapshot Metric.xlsx")
REPOSITORY_DIR = Path("repository")


# -----------------------------
# Helpers
# -----------------------------
def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_column_name(name):
    return (
        str(name)
        .strip()
        .lower()
        .replace("\n", " ")
        .replace("_", " ")
    )


def make_metric_id(metric_name):
    text = metric_name.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def split_list(value):
    text = clean_text(value)
    if not text:
        return []

    parts = re.split(r";|,|\n", text)
    return [p.strip() for p in parts if p.strip()]


def get_col(row, col_map, possible_names):
    for name in possible_names:
        key = normalize_column_name(name)
        if key in col_map:
            return clean_text(row[col_map[key]])
    return ""


# -----------------------------
# Inference logic
# -----------------------------
def infer_priority(metric_name, category):
    text = f"{metric_name} {category}".lower()

    high_keywords = [
        "noi", "revenue", "expense", "opex", "dscr", "debt",
        "irr", "equity multiple", "basis", "value", "valuation",
        "occupancy", "cap rate", "capex", "cash flow"
    ]

    for keyword in high_keywords:
        if keyword in text:
            return "High"

    return "Medium"


def infer_layers(source_type, metric_name):
    text = f"{source_type} {metric_name}".lower()
    layers = []

    if any(x in text for x in ["acquisition", "underwriting"]):
        layers.append("Acquisition Underwriting")
    if any(x in text for x in ["business plan", "budget", "bp", "forecast"]):
        layers.append("Business Plan")
    if any(x in text for x in ["actual", "financial statement", "ledger", "gl"]):
        layers.append("Actuals")
    if any(x in text for x in ["rent roll", "lease", "tenant", "occupancy", "walt"]):
        layers.append("Leasing")
    if any(x in text for x in ["debt", "loan", "dscr", "ltv"]):
        layers.append("Debt")
    if any(x in text for x in ["capex", "capital"]):
        layers.append("CapEx")

    return layers or ["General"]


def build_aliases(metric_name, aliases_text):
    aliases = []

    if metric_name:
        aliases.append(metric_name)

    aliases += split_list(aliases_text)

    lower = metric_name.lower()

    # NOI — only the primary metric gets bare "NOI" alias.
    # Sub-metrics (NOI Margin, NOI Growth, etc.) have specific aliases in the catalog.
    # Giving them "NOI" would cause them to match the NOI cell and overwrite the real value.
    if lower in ("net operating income (noi)", "net operating income"):
        aliases += ["NOI", "Net Operating Income"]

    # Revenue aliases removed from programmatic expansion.
    # EGI and PGI have carefully curated aliases in the catalog.
    # Programmatic expansion was causing NOI, PGI, and other metrics to
    # inherit EGI/Revenue aliases and match the wrong cells.

    if "expense" in lower or "opex" in lower:
        aliases += [
            "OpEx",
            "Operating Expenses",
            "Total Operating Expenses",
            "Expenses"
        ]

    # DSCR — only the primary DSCR metric gets the bare "DSCR" alias.
    # Refinance DSCR has its own specific aliases in the catalog.
    if lower.startswith("dscr") or lower == "dscr / debt coverage ratio":
        aliases += ["DSCR", "Debt Service Coverage Ratio"]

    # IRR — do NOT add bare "IRR" to either levered or unlevered; they'd share the first match.
    # Do NOT add cross-aliases (Levered IRR should not list Unlevered IRR, and vice versa).
    if "levered irr" in lower and "unlevered" not in lower:
        aliases += ["Levered IRR", "Equity IRR", "IRR (Levered)"]

    if "unlevered irr" in lower:
        aliases += ["Unlevered IRR", "Property IRR", "IRR (Unlevered)", "Unlevered Return"]

    # Cap rate — do NOT add Going-in Cap Rate and Exit Cap Rate to each other.
    # They are distinct metrics that should only match their own cells.
    if "cap rate" in lower or "capitalization rate" in lower:
        aliases += ["Cap Rate", "Capitalization Rate"]

    if "purchase price" in lower:
        aliases += ["Purchase Price", "Acquisition Price"]

    # Basis — only the primary all-in basis metric gets generic "Basis" aliases.
    # Per-SF basis, market replacement basis, etc. have specific aliases in catalog.
    if "all-in basis" in lower or "all in basis" in lower or "total acquisition cost" in lower:
        aliases += ["Basis", "Total Basis", "Cost Basis"]

    # Occupancy — do NOT add generic "Occupancy" to all occupancy metrics.
    # Physical, Economic, Leased, and Break-even Occupancy each have their
    # own specific aliases in the catalog. A generic "Occupancy" alias on all
    # of them would cause every occupancy cell to match the first one scanned.

    if "walt" in lower or "wale" in lower:
        aliases += ["WALT", "WALE", "Weighted Average Lease Term"]

    if "ltv" in lower:
        aliases += ["LTV", "Loan to Value"]

    # NOTE: bare "Debt" and "Loan" intentionally removed.
    # Any metric with "debt" or "loan" in its name (DSCR, Debt Yield, Loan Maturity, etc.)
    # would inherit these, causing them to match any cell labeled "Acquisition Loan",
    # "Senior Loan", etc. — wrong cell, wrong value.
    # Specific debt aliases (Loan Balance, Debt Balance) are set in the catalog directly.

    # CapEx — only the CapEx Budget (UW projection) gets generic "CapEx" alias.
    # Capital Expenditures (cash flow line item), Spent to Date, Variance, and Remaining
    # have their own specific aliases in the catalog so they don't share the same cell.
    if "capex budget" in lower:
        aliases += ["CapEx", "Capital Expenditure", "Capital Costs"]

    # Deduplicate while preserving order
    cleaned = []
    seen = set()

    for alias in aliases:
        alias = clean_text(alias)
        if alias and alias.lower() not in seen:
            cleaned.append(alias)
            seen.add(alias.lower())

    return cleaned


# -----------------------------
# Main catalog loader
# -----------------------------
def load_metric_catalog(path=CATALOG_PATH):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Metric catalog not found: {path}. "
            "Save 'Snapshot Metric.xlsx' inside your real_estate_ai folder."
        )

    df = pd.read_excel(path)

    # Normalize column mapping
    col_map = {
        normalize_column_name(col): col
        for col in df.columns
    }

    catalog = []

    for _, row in df.iterrows():
        metric_name = get_col(row, col_map, ["Metric Name", "Metric", "Name"])

        if not metric_name:
            continue

        category     = get_col(row, col_map, ["Category"])
        definition   = get_col(row, col_map, ["Definition"])
        formula      = get_col(row, col_map, ["Formula", "Calculation Method"])
        source_type  = get_col(row, col_map, ["Source Type", "Source", "Required Source Type"])
        aliases_text = get_col(row, col_map, ["Aliases", "Search Terms"])
        core_question= get_col(row, col_map, ["Core Question", "Used For Core Question"])
        priority     = get_col(row, col_map, ["Priority"])
        # New columns added during v2 catalog cleanup
        data_nature  = get_col(row, col_map, ["Data Nature", "data_nature"])
        metric_source= get_col(row, col_map, ["Metric Source", "metric_source"]) or "extracted"

        aliases = build_aliases(metric_name, aliases_text)

        if not priority:
            priority = infer_priority(metric_name, category)

        metric = {
            "metric_id":     make_metric_id(metric_name),
            "metric_name":   metric_name,
            "category":      category,
            "definition":    definition,
            "formula":       formula,
            "source":        source_type,
            "aliases":       aliases,
            "core_question": core_question,
            "priority":      priority,
            # v2 fields
            "data_nature":   data_nature,   # projection | actual | mixed
            "metric_source": metric_source, # extracted | calculated
        }

        catalog.append(metric)

    return catalog


def catalog_to_dataframe(catalog):
    rows = []

    for item in catalog:
        rows.append({
            "metric_id": item["metric_id"],
            "metric_name": item["metric_name"],
            "category": item["category"],
            "definition": item["definition"],
            "formula": item["formula"],
            "data_format": item["data_format"],
            "source": item["source"],
            "system": item["system"],
            "stakeholder": item["stakeholder"],
            "layers": "; ".join(item["layers"]),
            "aliases": "; ".join(item["aliases"]),
            "core_question": item["core_question"],
            "priority": item["priority"],
            "dashboard_flag": item["dashboard_flag"],
            "extraction_notes": item["extraction_notes"],
        })

    return pd.DataFrame(rows)


def save_catalog_preview(output_path="repository/metric_catalog_preview.csv"):
    catalog = load_metric_catalog()
    df = catalog_to_dataframe(catalog)

    output_path = Path(output_path)
    output_path.parent.mkdir(exist_ok=True)

    df.to_csv(output_path, index=False)

    return output_path


# -----------------------------
# Test run
# -----------------------------
if __name__ == "__main__":
    catalog = load_metric_catalog()
    print(f"Loaded {len(catalog)} metrics.")

    preview_path = save_catalog_preview()
    print(f"Saved catalog preview to: {preview_path}")