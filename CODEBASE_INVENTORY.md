# Codebase Inventory — what gets scanned and extracted

Generated reference. Every name/keyword/string the system looks for or stores,
with the file and function where it's defined.

---

## 1. File classification keywords

When you upload a file, this function decides which SSOT layer it belongs to,
based on the filename only.

**Defined in:** `flexible_extractor.py` → `classify_file_layer(file_name)`

| Layer returned | Keywords matched (filename, case-insensitive) |
|---|---|
| `actuals_<YYYY>` / `actuals_recent` | `financial statement`, `income statement`, `operating statement`, `p&l`, `pl statement`, `actual`, `actuals`, ` fs `, `_fs_`, `_fs.`, ` fs.`, `t12`, `trailing 12` — then year (2020–2025) appended if found, else `_recent` |
| `underwriting` | `acquisition`, `underwriting`, `proforma`, `pro forma`, `pro-forma`, `uw model`, `deal memo`, plus standalone ` uw` token |
| `business_plan` | `business plan`, `budget`, `forecast`, `revised plan`, `annual plan`, `asset plan`, `hold plan`, plus standalone ` bp ` token |
| `unknown` | (no match → user gets a manual-classification dropdown) |

**Order matters:** financial statements are checked first so "2022 P&L.xlsx" goes to actuals, not BP via the year token.

---

## 2. Metric catalog (the 97 metrics + 306 aliases)

This is what the cell-scanner looks for inside each spreadsheet.

**Source data:** `Snapshot Metric.xlsx` (a 97-row spreadsheet you maintain)
**Loader:** `metric_catalog.py` → `load_metric_catalog()`
**Alias expansion:** `metric_catalog.py` → `build_aliases(metric_name, aliases_text)`

Every metric carries a list of aliases — text fragments the scanner looks for inside cell values. A cell containing "NOI" matches the metric `Net Operating Income (NOI)` because that alias is in the metric's alias list.

### Categories (6) and metric counts

| Category | Count |
|---|---|
| Leasing & Income Durability | 26 |
| Operating Performance | 16 |
| Debt & Leverage | 15 |
| Valuation & Returns | 15 |
| CapEx & Capital Allocation | 13 |
| Investment Basis | 12 |
| **Total** | **97** |

### Alias source

Aliases come from two places, merged:
1. The `aliases` column in `Snapshot Metric.xlsx` (your hand-curated list).
2. Programmatic expansion in `metric_catalog.build_aliases` — see code for the full rules. Examples:
   - Any metric with "NOI" in its name → adds `["NOI", "Net Operating Income"]`
   - Any metric with "DSCR" in its name → adds `["DSCR", "Debt Service Coverage Ratio"]`
   - Any metric matching loan/debt patterns → adds `["Loan Balance", "Debt Balance", "Outstanding Debt", "Loan Amount"]`

⚠️ **Known issue:** several metrics share the same generic aliases (e.g. `Debt Service`, `Free Cash Flow Before Debt Service`, `Cash Flow After Debt Service` all alias `"Debt Service"`). This causes one cell to match multiple metrics. See section 8 below for what this looks like in practice.

---

## 3. Cell-value location heuristics

Once the scanner finds a label cell (e.g. cell B22 = "LTV"), this function decides which nearby cell holds the value.

**Defined in:** `flexible_extractor.py` → `find_nearby_value(ws, row, col)`

**Search order:**
1. **Time-series row sum** — if 6+ numeric cells are to the right of the label, it's probably a monthly/quarterly row. Look for a "Total / Annual / YTD / Full Year / Year Total / T12" column header; use that, else sum the cells.
2. **Same row, cells to the right** (offsets 1..7), preferring non-zero values.
3. **Same column, cells below** (offsets 1..5), preferring non-zero.
4. **Surrounding grid** (rows -2..+3, cols -2..+5) — last-resort fuzzy search.

Returns `(value, value_cell_address, direction)` where direction is `"total_col"`, `"row_sum"`, `"right"`, `"below"`, or `"nearby"`. The direction tags confidence level for ranking when multiple matches exist.

---

## 4. SSOT layer names

The canonical layer keys that the SSOT stores data under, and that scenarios read from.

**Defined in:** `ssot.py` → `KNOWN_LAYERS`

```
underwriting
business_plan
actuals_2020, actuals_2021, actuals_2022, actuals_2023, actuals_2024, actuals_2025
actuals_recent       (fallback when classifier sees a FS but no year)
rent_roll            (reserved for future Lease Review)
debt                 (reserved for future Debt Analysis)
```

These names MUST match `classify_file_layer`'s return values exactly — alignment is enforced by the agent's tool call to `ingest_to_ssot_with_layer`, which validates the layer against this set.

---

## 5. Scenario readiness requirements

Which SSOT layers each scenario needs to be runnable.

**Defined in:** `ssot.py` → `SCENARIO_REQUIREMENTS`

### deal_review
Needs at least the `underwriting` layer.

### perf_vs_plan
Needs any of these layer combinations:
- `underwriting + actuals_2021`
- `underwriting + actuals_2022`
- `underwriting + actuals_2023`
- `underwriting + actuals_recent`
- `business_plan + actuals_2021`
- `business_plan + actuals_2022`
- `business_plan + actuals_2023`
- `business_plan + actuals_recent`

Called by both the UI (to decide if the scenario button is clickable) and the agent (via the `check_scenario_ready` tool).

---

## 6. Scenario metric profiles

Which catalog metrics each scenario actually USES from SSOT when building its GPT prompt. Filters extraction noise so the narrative stays focused.

**Defined in:** `scenarios/profiles.py` → `SCENARIO_PROFILES`
**Applied in:** `scenarios/deal_review.py` and `scenarios/perf_vs_plan.py` via `filter_layer_metrics()`

### deal_review profile
| Category | Rule |
|---|---|
| Investment Basis | ALL 12 metrics |
| Valuation & Returns | ALL 15 metrics |
| Debt & Leverage | Only 8: Original LTV, LTC, Interest Rate, Loan Maturity, Hedging Cost / Swap Cost, Loan Balance, Debt Service Constant, Interest-Only Period Remaining |

### perf_vs_plan profile
| Category | Rule |
|---|---|
| Operating Performance | ALL 16 metrics |
| Leasing & Income Durability | Only 7: Physical/Economic/Leased Occupancy, Vacancy Rate, Retention Rate, Lease-up Velocity, Tenant Delinquency Rate |
| Debt & Leverage | Only 8: Current LTV, DSCR / Debt Coverage Ratio, Debt Yield, Loan Balance, Refinance DSCR, Break-even Occupancy (Monthly), Covenant Headroom, Cash Sweep Trigger Status |

At import time, `_validate_profile_names()` checks every metric name listed here against the live catalog and warns if anything doesn't match — so a typo or catalog rename surfaces immediately.

---

## 7. Agent tools

The functions the agent (`gpt-4o-mini`) can call during follow-up Q&A.

**Defined in:** `tools.py` → `TOOL_IMPLEMENTATIONS`, `TOOL_SCHEMAS`

| Tool | What it does |
|---|---|
| `list_uploaded_files` | List files in `uploads/` |
| `classify_file` | Filename-based layer classification (filename → layer name) |
| `extract_from_file` | Scan one Excel file against the full 97-metric catalog |
| `ingest_to_ssot` | classify + extract + write to SSOT (used by the deterministic pipeline) |
| `ingest_to_ssot_with_layer` | Same but with user-specified layer (manual-override path) |
| `get_ssot_summary` | "What's currently in SSOT?" — layers + files |
| `get_layer_details` | All metrics in one specific layer |
| `check_scenario_ready` | "Can scenario X run yet, given current SSOT?" |
| `run_deal_review` | Generate the Deal Review narrative (LLM call inside) |
| `run_perf_vs_plan` | Generate the Performance vs Plan narrative (LLM call inside) |

**Per-scenario tool subsets:**
- Deal Review agent gets all the above EXCEPT `run_perf_vs_plan`
- Performance Analysis agent gets all the above EXCEPT `run_deal_review`

This prevents an agent from accidentally invoking the wrong scenario's narrative tool.

---

## 8. Currently extracted metrics — live SSOT snapshot

**File:** `assets/current_asset.json` (regenerated every time you ingest)

This section reflects whatever state the SSOT is in right now. For the latest test (`425 Colorado Acquisition Underwriting.xlsx`), here's the snapshot:

### Layer: `underwriting` — 35 metrics extracted

| Metric | Value | Source cell |
|---|---:|---|
| Purchase Price | 28,800,000 | Summary & Assumptions!L22 |
| Closing Costs | 250,000 | Budget & Draw Schedule!G47 |
| Basis per SF / per Unit / per Key | 3,807.60 | Summary & Assumptions!L47 |
| Going-in Cap Rate | 0.06 | Summary & Assumptions!P25 |
| Interest Rate | 0.06 | Summary & Assumptions!L25 |
| LTC | 0.55 | Summary & Assumptions!L23 |
| Levered IRR | 0.21 | Summary & Assumptions!P36 |
| Unlevered IRR | 0.21 | Summary & Assumptions!P36 |
| Equity Multiple | 1.40 | Summary & Assumptions!P37 |
| Effective Gross Revenue / EGI | 4,891,267.50 | Annual CFs!G28 |
| Operating Expenses | 380,100 | Annual CFs!G36 |
| Realized Gain / Loss | -284,212.50 | Annual CFs!G17 |
| Unrealized Gain / Loss | -284,212.50 | Annual CFs!G17 |
| Lease-up Cost per Occupied SF/Unit | 246,280.01 | Summary & Assumptions!N7 |

⚠️ **Extraction-quality issues visible in this snapshot:**

The cell `Summary & Assumptions!K6` (which holds $15,840,000 — the loan amount) got matched to **nine** different metrics, because they all share generic debt-related aliases:

- Debt Amount, Loan Balance, Loan Maturity ⟵ should be 3 different things, all $15.84M
- Debt Service, Debt Service Constant, Debt Yield ⟵ also all $15.84M (wrong)
- DSCR / Debt Coverage Ratio ⟵ should be a ratio (~1.x), not a dollar amount
- Cash Flow After Debt Service, Free Cash Flow Before Debt Service, Credit Loss / Bad Debt ⟵ all $15.84M (definitely wrong)

This is the cost of aggressive aliasing (`"Debt"`, `"Loan"`, `"Loan Amount"` etc. all map to many metrics). The scenario profile filter helps narrow this, but the underlying extraction is over-matching.

**Two ways to fix this** (future work):
- Trim the generic aliases in `metric_catalog.build_aliases` so debt-adjacent metrics don't inherit each other's aliases
- Add a uniqueness constraint at write-time: if two metrics resolve to the same cell, only the most-specific alias wins

---

## Quick reference: where to change things

| To change... | Edit this file |
|---|---|
| Which keywords classify a file | `flexible_extractor.py` → `classify_file_layer` |
| Add/rename a metric or its aliases | `Snapshot Metric.xlsx` (auto-loaded by `metric_catalog.py`) |
| Which metrics belong to a scenario | `scenarios/profiles.py` → `SCENARIO_PROFILES` |
| What layers a scenario requires | `ssot.py` → `SCENARIO_REQUIREMENTS` |
| Add a new scenario | `scenarios/<name>.py` + add to `tools.py`, `agent_loop.py`, and `app.py` |
| Adjust the cell-value search heuristics | `flexible_extractor.py` → `find_nearby_value` |
| Tool descriptions the agent sees | `tools.py` → `TOOL_SCHEMAS` |
