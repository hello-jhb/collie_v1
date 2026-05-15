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


def _find_sheet_total_col(ws, max_scan_rows=15):
    """
    Detect a 'Total / Annual / YTD / Full Year / T12' column header in the first
    few rows of the sheet. Returns the column index or None.
    """
    total_keywords = ["total", "annual", "ytd", "full year", "year total", "t12"]
    for r in range(1, max_scan_rows + 1):
        for c in range(1, ws.max_column + 1):
            txt = normalize_text(ws.cell(row=r, column=c).value)
            if any(kw == txt or kw in txt.split() for kw in total_keywords):
                return c
    return None


def find_nearby_value(ws, row, col):
    """
    Search nearby cells for a value.
    Priority:
    0. If the row looks like a time-series (6+ numeric cells to the right of the label),
       prefer the value under a 'Total / Annual / YTD' column header; otherwise sum
       the monthly cells. This handles financial-statement rows like
       'TOTAL OPERATING REVENUE | Jan | Feb | ... | Dec | Total'.
    1. Same row, cells to the right — prefer non-zero (handles Sources/Uses tables
       where the first numeric column ('At Close') can legitimately be zero while
       the meaningful value sits one or two columns further right).
    2. Same column, cells below — same non-zero preference.
    3. Small surrounding area — same non-zero preference.
    """

    # --- Step 0: time-series-aware path ---
    # Scan up to 20 cells to the right of the label and count numeric values.
    numeric_right = []
    for offset in range(1, 21):
        c = col + offset
        if c > ws.max_column:
            break
        v = ws.cell(row=row, column=c).value
        if is_numeric(v):
            numeric_right.append((v, c))

    if len(numeric_right) >= 6:
        # This is a monthly/quarterly time-series row.
        total_col = _find_sheet_total_col(ws)
        if total_col and total_col > col:
            tot_val = ws.cell(row=row, column=total_col).value
            if is_numeric(tot_val) and tot_val != 0:
                return tot_val, cell_address(row, total_col), "total_col"
        # No populated total column — sum the period cells (excluding the total col itself)
        period_vals = [v for v, c in numeric_right if c != total_col]
        if period_vals:
            row_sum = sum(period_vals)
            first_c = period_vals and numeric_right[0][1]
            last_c  = period_vals and [c for v, c in numeric_right if c != total_col][-1]
            return row_sum, f"{cell_address(row, first_c)}:{cell_address(row, last_c)}", "row_sum"

    # --- Step 1: look right, prefer non-zero ---
    zero_fallback = None
    for value, c in numeric_right[:7]:
        if value != 0:
            return value, cell_address(row, c), "right"
        if zero_fallback is None:
            zero_fallback = (value, cell_address(row, c), "right")

    # Look below
    for offset in range(1, 6):
        value = ws.cell(row=row + offset, column=col).value
        if is_numeric(value):
            if value != 0:
                return value, cell_address(row + offset, col), "below"
            if zero_fallback is None:
                zero_fallback = (value, cell_address(row + offset, col), "below")

    # Look nearby grid
    for r_offset in range(-2, 4):
        for c_offset in range(-2, 6):
            r = row + r_offset
            c = col + c_offset
            if r < 1 or c < 1:
                continue
            value = ws.cell(row=r, column=c).value
            if is_numeric(value):
                if value != 0:
                    return value, cell_address(r, c), "nearby"
                if zero_fallback is None:
                    zero_fallback = (value, cell_address(r, c), "nearby")

    # All nearby values were zero — return that rather than nothing
    if zero_fallback is not None:
        return zero_fallback

    return None, None, None


def scan_workbook_for_metric(file_path, metric, relevant_tabs=None):
    """
    Search one Excel workbook for one metric.
    If relevant_tabs is provided, scan those sheets first; fall back to all sheets if none match.
    Returns best match or None.
    """

    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
    except Exception as e:
        return None

    aliases = metric.get("aliases", [])
    matches = []

    if relevant_tabs:
        sheets_to_scan = [s for s in relevant_tabs if s in wb.sheetnames] or wb.sheetnames
    else:
        sheets_to_scan = wb.sheetnames

    for sheet_name in sheets_to_scan:
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
                            # Confidence tiering — time-series-aware methods are strongest
                            # because they read the actual annual total or sum, not a
                            # single adjacent cell which may be a section header neighbor.
                            if direction in ["total_col", "row_sum"]:
                                confidence = "high"
                            elif direction in ["right", "below"]:
                                confidence = "high"
                            else:
                                confidence = "medium"

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

    # Priority:
    # 1. Match method — time-series methods (total_col / row_sum) read the annual
    #    figure; "right" / "below" read a single adjacent cell; "nearby" is last resort.
    # 2. Alias specificity — longer aliases are more specific (e.g. "Total Operating
    #    Revenue" beats "Revenue" so a generic match doesn't outrank a precise one).
    # 3. Confidence tier.
    method_priority = {"total_col": 0, "row_sum": 1, "right": 2, "below": 3, "nearby": 4}
    matches = sorted(
        matches,
        key=lambda x: (
            method_priority.get(x["match_method"], 99),
            -len(x.get("matched_alias") or ""),
            0 if x["confidence"] == "high" else 1,
        )
    )

    return matches[0]


def build_tabs_lookup(classification_result):
    """
    Build a dict of {file_name: [relevant_tabs]} from a classification result.
    """
    if not classification_result:
        return {}

    lookup = {}
    for item in classification_result.get("classifications", []):
        file_name = item.get("file_name")
        tabs = item.get("relevant_tabs") or []
        if file_name and tabs:
            lookup[file_name] = tabs

    return lookup


def scan_uploaded_files(upload_dir=UPLOAD_DIR, classification_result=None):
    """
    Scan all uploaded Excel files against the metric catalog.
    Uses relevant_tabs from classification_result to focus scanning per file.
    """

    upload_dir = Path(upload_dir)
    REPOSITORY_DIR.mkdir(exist_ok=True)

    catalog = load_metric_catalog()

    excel_files = list(upload_dir.glob("*.xlsx")) + list(upload_dir.glob("*.xlsm"))

    tabs_lookup = build_tabs_lookup(classification_result)

    extracted = []
    missing = []

    for metric in catalog:
        best_match = None

        for file_path in excel_files:
            relevant_tabs = tabs_lookup.get(file_path.name)
            match = scan_workbook_for_metric(file_path, metric, relevant_tabs=relevant_tabs)

            if match:
                best_match = match
                break

        if best_match:
            extracted.append(best_match)
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