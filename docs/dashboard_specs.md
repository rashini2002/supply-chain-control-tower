# Tableau build spec — Dashboards 1 & 2

Read this alongside Tableau. One golden rule for this project: **don't
join/relate the mart CSVs to each other in Tableau.** They're at different
grains (PO-level, shipment-level, snapshot-level) — joining them causes
fan-out (inflated sums). Instead, add each CSV as its own separate Data
Source, and build each worksheet against the one source it needs. A
dashboard can freely combine worksheets from different data sources side
by side — no join required.

## Data sources to add (Data > New Data Source, one at a time)

- `mart_procurement.csv`
- `mart_logistics.csv`
- `mart_inventory.csv`
- `supplier_risk_scores.csv` (Day 6 output)
- `recommended_vs_actual_routing.csv` (Day 8 output)
- `inventory_with_dynamic_reorder.csv` (Day 5 output)

After connecting each, check field types: `Is Late`, `Is Below Reorder Point`,
`Sanctions Ever`, `High Risk Label` may import as strings ("True"/"False" or
1/0) rather than native booleans — the calculated fields below assume you
may need to cast them; adjust the syntax if Tableau already reads them as
Boolean.

---

## Dashboard 1 — Executive Overview

### KPI cards (build each as its own single-value worksheet, format as a "Big Number" text table)

| # | KPI | Source | Formula |
|---|---|---|---|
| 1 | Total Spend | mart_procurement | `SUM([Total Value])` |
| 2 | Total Shipments | mart_logistics | `COUNTD([Shipment Id])` |
| 3 | On-Time Delivery Rate | mart_logistics | `AVG(IF [Is Late] = "False" OR [Is Late] = "0" THEN 1 ELSE 0 END)` — format as percent |
| 4 | Total Inventory Value | mart_inventory | `SUM([Inventory Value])` |
| 5 | Number of Suppliers | mart_procurement | `COUNTD([Supplier Node Id])` |
| 6 | High-Risk Suppliers | supplier_risk_scores | `COUNTD(IF [High Risk Label] = 1 THEN [Supplier Node Id] END)` |
| 7 | Est. Monthly Routing Savings | recommended_vs_actual_routing | `SUM([Monthly Savings])` |

### Charts

**A. Monthly spend trend** (line chart, mart_procurement)
- Columns: `MONTH([Order Date])` (continuous, green pill)
- Rows: `SUM([Total Value])`

**B. Top 10 suppliers by spend** (horizontal bar, mart_procurement)
- Rows: `[Supplier Name]`
- Columns: `SUM([Total Value])`
- Filter: Top 10 by `SUM([Total Value])` (right-click the Supplier Name pill → Filter → Top tab)
- Color (optional): `[Supplier Country]`

**C. Shipment flow by stage** (bar chart, mart_logistics)
- New calculated field `Flow Stage`:
  ```
  [Origin Type] + " → " + [Dest Type]
  ```
- Rows: `[Flow Stage]`
- Columns: `SUM([Quantity])`

**D. Cost by transportation mode** (donut/pie, mart_logistics)
- Angle: `SUM([Cost])`
- Color: `[Mode]`

**E. Geographic shipment map** (mart_logistics, filtered to `[Dest Type] = "customer_region"`)
- Tableau auto-geocodes country names — double-click `[Dest Country]` to generate Latitude/Longitude
- Size: `SUM([Quantity])`
- Color: `AVG([Delay Days])` (diverging color = shows both volume and delay hotspots at once)

### Layout
Top row: all 7 KPI cards, tiled horizontally, small fixed height.
Below: 2×2 grid of charts A–D. Map (E) as a wide strip underneath, or swap in for D if space is tight.

### Filters
- Global date range filter on `[Order Date]` (context filter, applied to the procurement-based sheets)
- Optional: `[Supplier Country]` quick filter

---

## Dashboard 2 — Inventory Intelligence

### KPI cards

| # | KPI | Source | Formula |
|---|---|---|---|
| 1 | Total Inventory Value | mart_inventory | `SUM([Inventory Value])` |
| 2 | Total SKUs Tracked | mart_inventory | `COUNTD([Product Id])` |
| 3 | % Below Reorder Point | mart_inventory | `AVG(IF [Is Below Reorder Point] = "True" OR [Is Below Reorder Point] = "1" THEN 1 ELSE 0 END)` — format as percent |
| 4 | Products Needing Reorder | mart_inventory | `COUNTD(IF [Is Below Reorder Point] = "True" THEN [Product Id] END)` |
| 5 | Avg Reorder Point Adjustment | inventory_with_dynamic_reorder | `AVG([Reorder Point Change])` — shows how far the Day 5 model moved reorder points vs. the old placeholder |

### Charts

**A. Inventory value trend** (line, mart_inventory)
- Columns: `MONTH([Snapshot Date])` (continuous)
- Rows: `SUM([Inventory Value])`
- Tip: add a filter to keep only the most recent 12–24 months if it looks too dense.

**B. Inventory by category** (donut, mart_inventory)
- Angle: `SUM([Inventory Value])`
- Color: `[Category]`

**C. ABC classification** (bar or treemap, mart_inventory — this is the standout chart, uses table calculations)

Build it in this order:
1. Put `[Product Id]` on Rows (or Detail), `SUM([Inventory Value])` on Rows/Columns.
2. Sort products descending by `SUM([Inventory Value])`.
3. New calculated field `Running Pct Of Total`:
   ```
   RUNNING_SUM(SUM([Inventory Value])) / TOTAL(SUM([Inventory Value]))
   ```
   Right-click this pill on the shelf → Compute Using → choose `[Product Id]`, sorted by `SUM([Inventory Value])` descending.
4. New calculated field `ABC Class`:
   ```
   IF [Running Pct Of Total] <= 0.7 THEN "A"
   ELSEIF [Running Pct Of Total] <= 0.9 THEN "B"
   ELSE "C"
   END
   ```
5. Build the final chart: Color/Rows = `[ABC Class]`, Columns = `SUM([Inventory Value])` (or `COUNTD([Product Id])` for a "how many SKUs per class" view — A should be few SKUs, high value; C should be many SKUs, low value).

**D. Top overstock products** (table or bar, mart_inventory)
- New calculated field `Overstock Ratio`:
  ```
  SUM([Stock Qty]) / SUM([Reorder Point])
  ```
- Rows: `[Product Name]`, sorted descending by `Overstock Ratio`, filtered to Top 10

**E. Reorder needed table** (table, inventory_with_dynamic_reorder)
- Rows: `[Product Name]`, `[Warehouse Name]`
- Columns: `[Stock Qty]`, `[Reorder Point Model]`, `[Reorder Point Change]`
- Filter: `[Is Below Reorder Point] = True`
- Sort ascending by `[Stock Qty] - [Reorder Point Model]` (most urgent first) — add this as a calculated field `Reorder Urgency` if you want a dedicated sort pill.

### Layout
KPI strip on top (5 cards). Below: value trend (A) full-width or half-width next to the category donut (B). ABC classification (C) as a prominent central chart. Overstock (D) and reorder-needed (E) as two tables side by side at the bottom.

### Filters
- `[Warehouse Name]` quick filter
- `[Category]` quick filter
- Relative date filter on `[Snapshot Date]` defaulted to the latest month, for any KPI/chart that should reflect "right now" rather than history (the trend chart A is the exception — that one should show full history)

# Tableau build spec — Dashboards 3 & 4

Same rule as before: don't join these CSVs together in Tableau — add each as
its own separate Data Source and build each worksheet against the one it
needs. Combine on the dashboard canvas only.

## Data sources to add

- `mart_logistics.csv`
- `mart_procurement.csv`
- `supplier_risk_scores.csv` (Day 6 output)
- `supplier_risk_shap_importance.csv` (Day 6 output)
- `recommended_vs_actual_routing.csv` (Day 8 output)
- `optimized_routing_plan.csv` (Day 8 output)

Field-type check before you start: `Is Late` (mart_logistics) and
`High Risk Label` / `Sanctions Ever` (supplier_risk_scores) may import as
native Booleans or as 0/1 integers depending on how Tableau reads the CSV —
check the icon in the Data pane (`T|F` = boolean, `#` = number) and adjust
the formulas below accordingly, same issue you hit on Dashboards 1 & 2.

---

## Dashboard 3 — Logistics Performance

### KPI cards

| # | KPI | Source | Formula |
|---|---|---|---|
| 1 | Total Shipments | mart_logistics | `COUNTD([Shipment Id])` |
| 2 | On-Time Delivery Rate | mart_logistics | `AVG(IF [Is Late] THEN 0 ELSE 1 END)` (adjust if Is Late is 0/1 instead of boolean) — format percent |
| 3 | Avg Delay (Days) | mart_logistics | `AVG([Delay Days])` |
| 4 | Total Logistics Cost | mart_logistics | `SUM([Cost])` |
| 5 | Avg Cost per Unit | mart_logistics | `AVG([Cost Per Unit])` |
| 6 | Est. Monthly Routing Savings | recommended_vs_actual_routing | `SUM([Monthly Savings])` |

### Charts

**A. Cost by corridor** (bar chart, mart_logistics — the standout chart on this page)
- New calculated field `Corridor`:
  ```
  [Origin Country] + " → " + [Dest Country]
  ```
- Rows: `[Corridor]`
- Columns: `SUM([Cost])`
- Color: `AVG([Delay Days])` (diverging palette) — this turns a simple cost bar chart into a cost-AND-delay view at once
- Filter: Top 15 by `SUM([Cost])`

**B. On-time delivery trend** (line, mart_logistics)
- Columns: `MONTH([Ship Date])` (continuous)
- Rows: the On-Time Delivery Rate calculation from KPI #2
- This is the one chart on the dashboard that should show full history — don't apply a "latest month only" filter here even if you add one elsewhere.

**C. Recommended vs actual routing cost** (side-by-side bar, recommended_vs_actual_routing)
- Rows: `[Customer]`
- Columns: two measures side by side — `SUM([Actual Monthly Cost])` and `SUM([Optimized Monthly Cost])`. Easiest way: drag both onto Columns as separate pills (Tableau will auto-create a dual bar); or use Measure Names/Measure Values with a filter limited to just these two fields.
- Filter: Top 15 customers by `SUM([Monthly Savings])`, descending — this surfaces where the optimization matters most, not a random/alphabetical list
- Caption/subtitle: mention this compares against a randomly-routed baseline (per your DECISIONS.md Day 8 entry) so viewers don't read the gap as a realistic savings claim

**D. Performance by mode** (table, mart_logistics)
- Rows: `[Mode]`
- Columns: `AVG([Delay Days])`, `AVG([Cost Per Unit])`, `COUNTD([Shipment Id])`
- Sort by `AVG([Delay Days])` descending — surfaces which mode is least reliable

### Layout
KPI strip (6 cards) on top. Cost by Corridor (A) as the largest chart — it's the centerpiece. On-Time Trend (B) and Recommended vs Actual (C) side by side below. Mode performance table (D) as a compact strip at the bottom.

### Filters
- Global `[Ship Date]` range filter (context filter), applied to all mart_logistics sheets — but exclude it from chart B if you want that one to always show full history
- Optional: `[Mode]` quick filter

---

## Dashboard 4 — Supplier Performance & Risk

### KPI cards

| # | KPI | Source | Formula |
|---|---|---|---|
| 1 | Total Suppliers | supplier_risk_scores | `COUNTD([Supplier Node Id])` |
| 2 | High-Risk Suppliers | supplier_risk_scores | `COUNTD(IF [High Risk Label] = 1 THEN [Supplier Node Id] END)` |
| 3 | Avg Predicted Risk | supplier_risk_scores | `AVG([Predicted Risk Probability])` — format percent |
| 4 | Suppliers with Sanctions History | supplier_risk_scores | `COUNTD(IF [Sanctions Ever] = 1 THEN [Supplier Node Id] END)` |
| 5 | Avg ESG Score | supplier_risk_scores | `AVG([Esg Score])` |

### Charts

**A. Top risk drivers (SHAP importance)** (horizontal bar, supplier_risk_shap_importance — this is the standout chart, echoes your Day 6 ML work directly)
- Rows: `[Feature]`
- Columns: `SUM([Mean Abs Shap])`
- Sort descending — should read price_deviation_pct and avg_lead_time_days as the top drivers (per your Day 6 findings)
- Caption: "Feature importance from the XGBoost risk model (SHAP values)" — naming the technique explicitly is worth it here, it's a genuine differentiator

**B. Risk score distribution** (bar or histogram, supplier_risk_scores)
- Rows: `[Supplier Name]`
- Columns: `SUM([Predicted Risk Probability])`
- Color: `[High Risk Label]` (2-color: flagged vs not)
- Sort descending by risk probability

**C. Supplier risk scorecard** (table, supplier_risk_scores — the detail view)
- Rows: `[Supplier Name]`, `[Supplier Country]`
- Columns: `[Predicted Risk Probability]`, `[Top Risk Driver]`, `[Cancellation Rate]`, `[Late Shipment Rate]`, `[Payment Overdue Rate]`
- Color the `Predicted Risk Probability` column with a red-scale gradient so high-risk rows visually pop
- Sort descending by risk

**D. Sanctions & country risk flags** (table or icon view, mart_procurement)
- Rows: `[Supplier Name]`, `[Supplier Country]`
- Columns: `AVG([Country Risk Index])`, `AVG([Currency Volatility])`, a calculated field `Sanctions Flag Ever`: `MAX(IF [Sanctions Flag] THEN 1 ELSE 0 END)`
- Filter: only show suppliers where `Sanctions Flag Ever = 1` OR `Country Risk Index` is in the top quartile — this becomes a focused watchlist, not a full 40-row dump

**E. Geographic risk map** (map, supplier_risk_scores or mart_procurement)
- Same technique as Dashboard 1's map: double-click `[Supplier Country]` to geocode
- Color: `AVG([Predicted Risk Probability])`
- Size: `SUM([Total Spend])` (from mart_procurement) if you want spend-weighted risk exposure

### Layout
KPI strip (5 cards) on top. SHAP importance (A) prominent on the left — it's your best chart on this dashboard. Risk distribution (B) or the map (E) beside it. Scorecard table (C) and sanctions watchlist (D) below, side by side.

### Filters
- `[High Risk Label]` quick filter (toggle: show only flagged suppliers)
- `[Supplier Country]` quick filter