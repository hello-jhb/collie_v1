from pathlib import Path
import json
import pandas as pd
import openpyxl

from metric_catalog import load_metric_catalog


UPLOAD_DIR = Path("uploads")
REPOSITORY_DIR = Path("repository")


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def is_numeric(value):
    return isinstance(value, (int, float)) and not pd.isna(value)


def normalize_text(value):
    return clean_text(value).lower()


def cell_address(row, col):
    return openpyxl.utils.get_column_letter(col) + str(row)


def find_nearby_value(ws, row, col):
    """
    Search nearby cells for a value.
    Priority:
    1. Same row, cells to the right
    2. Same column, cells below
    3. Small surrounding area
    """

    # Look right
    for offset in range(1, 6):
        value = ws.cell(row=row, column=col + offset).value
        if is_numeric(value):
            return value, cell_address(row, col + offset), "right"

    # Look below
    for offset in range(1, 6):
        value = ws.cell(row=row + offset, column=col).value
        if is_numeric(value):
            return value, cell_address(row + offset, col), "below"

    # Look nearby grid
    for r_offset in range(-2, 4):
        for c_offset in range(-2, 6):
            r = row + r_offset
            c = col + c_offset

            if r < 1 or c < 1:
                continue

            value = ws.cell(row=r, column=c).value
            if is_numeric(value):
                return value, cell_address(r, c), "nearby"

    return None, None, None


def scan_workbook_for_metric(file_path, metric):
    """
    Search one Excel workbook for one metric.
    Returns best match or None.
    """

    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
    except Exception as e:
        return None

    aliases = metric.get("aliases", [])
    matches = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]

        for row in ws.iter_rows():
            for cell in row:
                cell_text = normalize_text(cell.value)

                if not cell_text:
                    continue

                for alias in aliases:
                    alias_text = normalize_text(alias)

                    if not alias_text:
                        continue

                    if alias_text in cell_text:
                        value, value_cell, direction = find_nearby_value(
                            ws,
                            cell.row,
                            cell.column
                        )

                        if value is not None:
                            confidence = "high" if direction in ["right", "below"] else "medium"

                            matches.append({
                                "metric_id": metric["metric_id"],
                                "metric_name": metric["metric_name"],
                                "category": metric["category"],
                                "definition": metric["definition"],
                                "value": value,
                                "source_file": Path(file_path).name,
                                "sheet": sheet_name,
                                "label_cell": cell.coordinate,
                                "value_cell": value_cell,
                                "matched_alias": alias,
                                "confidence": confidence,
                                "match_method": direction,
                            })

    if not matches:
        return None

    # Prefer high confidence matches first
    matches = sorted(
        matches,
        key=lambda x: 0 if x["confidence"] == "high" else 1
    )

    return matches[0]


def classify_file_layer(file_name):
    """
    Classify a file by its investment lifecycle layer based on its name.
    Returns one of: 'acquisition_underwriting', 'business_plan', 'actuals_2021',
    'actuals_2022', 'actuals_recent', or 'unknown'.
    """
    name_lower = file_name.lower()

    if "acquisition" in name_lower or "underwriting" in name_lower or " uw" in name_lower or "_uw" in name_lower:
        return "acquisition_underwriting"
    if "business plan" in name_lower or "bp" in name_lower or "budget" in name_lower or "forecast" in name_lower:
        return "business_plan"
    if "financial statement" in name_lower or "fs " in name_lower or "actual" in name_lower or "t12" in name_lower:
        if "2021" in name_lower:
            return "actuals_2021"
        if "2022" in name_lower:
            return "actuals_2022"
        if "2023" in name_lower:
            return "actuals_2023"
        return "actuals_recent"
    return "unknown"


def scan_uploaded_files(upload_dir=UPLOAD_DIR):
    """
    Scan all uploaded Excel files against the metric catalog.
    Extracts each metric from EVERY file where found, tagged by source layer,
    so the analysis can compare underwriting vs business plan vs actuals.
    """

    upload_dir = Path(upload_dir)
    REPOSITORY_DIR.mkdir(exist_ok=True)

    catalog = load_metric_catalog()

    excel_files = list(upload_dir.glob("*.xlsx")) + list(upload_dir.glob("*.xlsm"))

    extracted = []
    missing = []

    for metric in catalog:
        all_matches = []

        for file_path in excel_files:
            match = scan_workbook_for_metric(file_path, metric)

            if match:
                match["source_layer"] = classify_file_layer(file_path.name)
                all_matches.append(match)

        if all_matches:
            extracted.extend(all_matches)
        else:
            missing.append({
                "metric_id": metric["metric_id"],
                "metric_name": metric["metric_name"],
                "category": metric["category"],
                "definition": metric["definition"],
                "source": metric.get("source", ""),
                "priority": metric.get("priority", "medium"),
                "aliases": metric.get("aliases", []),
                "status": "missing"
            })

    result = {
        "status": "success",
        "total_metrics": len(catalog),
        "extracted_count": len(extracted),
        "missing_count": len(missing),
        "extracted_metrics": extracted,
        "missing_metrics": missing,
    }

    with open(REPOSITORY_DIR / "flexible_extraction_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    pd.DataFrame(extracted).to_csv(
        REPOSITORY_DIR / "extracted_metrics_report.csv",
        index=False
    )

    pd.DataFrame(missing).to_csv(
        REPOSITORY_DIR / "missing_metrics_report.csv",
        index=False
    )

    return result


if __name__ == "__main__":
    result = scan_uploaded_files()
    print(f"Total metrics: {result['total_metrics']}")
    print(f"Extracted: {result['extracted_count']}")
    print(f"Missing: {result['missing_count']}")
    print("Saved reports to repository/")