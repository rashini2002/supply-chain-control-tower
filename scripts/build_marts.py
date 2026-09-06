"""
Day 4 - Clean and join raw CSVs into 4 analysis-ready marts, with inline
validation checks (nulls, referential integrity, positive-value checks).

Reads from ../data/raw/, writes to ../data/marts/.
Run from inside scripts/, after generate_day2_data.py and
generate_day3_data.py have both been run.
"""

import os
import pandas as pd
import numpy as np

IN_DIR = "../data/raw"
OUT_DIR = "../data/marts"
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Load raw tables
# ---------------------------------------------------------------------------
nodes = pd.read_csv(f"{IN_DIR}/nodes.csv")
products = pd.read_csv(f"{IN_DIR}/products.csv")
purchase_orders = pd.read_csv(f"{IN_DIR}/purchase_orders.csv", parse_dates=["order_date"])
invoices = pd.read_csv(f"{IN_DIR}/invoices.csv", parse_dates=["invoice_date", "payment_date"])
inventory = pd.read_csv(f"{IN_DIR}/inventory_snapshots.csv", parse_dates=["snapshot_date"])
shipments = pd.read_csv(f"{IN_DIR}/shipments.csv", parse_dates=["ship_date", "delivery_date"])
network_edges = pd.read_csv(f"{IN_DIR}/network_edges.csv")
risk_signals = pd.read_csv(f"{IN_DIR}/supplier_risk_signals.csv")

VALIDATION_LOG = []

def log_check(mart_name, check_name, passed, detail=""):
    VALIDATION_LOG.append({"mart": mart_name, "check": check_name, "passed": passed, "detail": detail})
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {mart_name} - {check_name}" + (f" ({detail})" if detail else ""))

def check_no_nulls(df, cols, mart_name, check_name):
    null_counts = df[cols].isnull().sum()
    bad = null_counts[null_counts > 0]
    passed = bad.empty
    log_check(mart_name, check_name, passed, "" if passed else bad.to_dict())

def check_positive(df, cols, mart_name, check_name):
    bad_cols = {c: int((df[c] <= 0).sum()) for c in cols if (df[c] <= 0).sum() > 0}
    passed = len(bad_cols) == 0
    log_check(mart_name, check_name, passed, "" if passed else bad_cols)

def check_fk(df, fk_col, valid_ids, mart_name, check_name):
    missing = ~df[fk_col].isin(valid_ids)
    n_missing = int(missing.sum())
    passed = n_missing == 0
    log_check(mart_name, check_name, passed, "" if passed else f"{n_missing} rows with unmatched {fk_col}")

def check_unique(df, col, mart_name, check_name):
    n_dupes = int(df[col].duplicated().sum())
    passed = n_dupes == 0
    log_check(mart_name, check_name, passed, "" if passed else f"{n_dupes} duplicate {col} values")

valid_node_ids = set(nodes.node_id)
valid_product_ids = set(products.product_id)

# ---------------------------------------------------------------------------
# Mart 1: Procurement (purchase orders + invoices + supplier + product + risk)
# ---------------------------------------------------------------------------
po = purchase_orders.merge(
    nodes[["node_id", "name", "country", "esg_score"]].rename(
        columns={"node_id": "supplier_node_id", "name": "supplier_name", "country": "supplier_country"}
    ),
    on="supplier_node_id", how="left",
).merge(
    products[["product_id", "name", "category"]].rename(columns={"name": "product_name"}),
    on="product_id", how="left",
).merge(
    invoices[["po_id", "invoice_date", "payment_status", "payment_date"]],
    on="po_id", how="left",
)

po["order_month"] = po["order_date"].dt.strftime("%Y-%m")
po = po.merge(
    risk_signals[["supplier_node_id", "month", "country_risk_index", "currency_volatility", "sanctions_flag"]],
    left_on=["supplier_node_id", "order_month"], right_on=["supplier_node_id", "month"], how="left",
).drop(columns=["month"])

risk_coverage = po["country_risk_index"].notnull().mean()
print(f"Procurement mart: risk signal coverage = {risk_coverage:.1%} (orders outside the 24-month risk signal window won't match - expected)")

check_unique(po, "po_id", "mart_procurement", "po_id uniqueness")
check_fk(po, "supplier_node_id", valid_node_ids, "mart_procurement", "supplier_node_id FK")
check_fk(po, "product_id", valid_product_ids, "mart_procurement", "product_id FK")
check_positive(po, ["quantity", "unit_price", "total_value"], "mart_procurement", "positive quantity/price/value")
check_no_nulls(po, ["po_id", "supplier_node_id", "product_id", "order_date"], "mart_procurement", "core fields not null")

po.to_csv(f"{OUT_DIR}/mart_procurement.csv", index=False)

# ---------------------------------------------------------------------------
# Mart 2: Inventory (snapshots + warehouse + product)
# ---------------------------------------------------------------------------
inv = inventory.merge(
    nodes[["node_id", "name", "country"]].rename(
        columns={"node_id": "warehouse_node_id", "name": "warehouse_name", "country": "warehouse_country"}
    ),
    on="warehouse_node_id", how="left",
).merge(
    products[["product_id", "name", "category", "unit_cost"]].rename(columns={"name": "product_name"}),
    on="product_id", how="left",
)
inv["inventory_value"] = inv["stock_qty"] * inv["unit_cost"]
inv["is_below_reorder_point"] = inv["stock_qty"] < inv["reorder_point"]

check_unique(inv, "snapshot_id", "mart_inventory", "snapshot_id uniqueness")
check_fk(inv, "warehouse_node_id", valid_node_ids, "mart_inventory", "warehouse_node_id FK")
check_fk(inv, "product_id", valid_product_ids, "mart_inventory", "product_id FK")
check_positive(inv, ["reorder_point", "safety_stock"], "mart_inventory", "positive reorder_point/safety_stock")
check_no_nulls(inv, ["snapshot_id", "warehouse_node_id", "product_id", "snapshot_date"], "mart_inventory", "core fields not null")

inv.to_csv(f"{OUT_DIR}/mart_inventory.csv", index=False)

# ---------------------------------------------------------------------------
# Mart 3: Logistics (shipments + origin/dest node info + product)
# ---------------------------------------------------------------------------
log_df = shipments.merge(
    nodes[["node_id", "name", "node_type", "country"]].rename(
        columns={"node_id": "origin_node_id", "name": "origin_name", "node_type": "origin_type", "country": "origin_country"}
    ),
    on="origin_node_id", how="left",
).merge(
    nodes[["node_id", "name", "node_type", "country"]].rename(
        columns={"node_id": "dest_node_id", "name": "dest_name", "node_type": "dest_type", "country": "dest_country"}
    ),
    on="dest_node_id", how="left",
).merge(
    products[["product_id", "name", "category"]].rename(columns={"name": "product_name"}),
    on="product_id", how="left",
)
log_df["delay_days"] = log_df["actual_transit_days"] - log_df["planned_transit_days"]
log_df["is_late"] = log_df["delay_days"] > 0
log_df["cost_per_unit"] = (log_df["cost"] / log_df["quantity"]).round(3)

check_unique(log_df, "shipment_id", "mart_logistics", "shipment_id uniqueness")
check_fk(log_df, "origin_node_id", valid_node_ids, "mart_logistics", "origin_node_id FK")
check_fk(log_df, "dest_node_id", valid_node_ids, "mart_logistics", "dest_node_id FK")
same_node = (log_df["origin_node_id"] == log_df["dest_node_id"]).sum()
log_check("mart_logistics", "origin != destination", same_node == 0, "" if same_node == 0 else f"{same_node} rows with same origin/dest")
check_positive(log_df, ["cost", "quantity", "planned_transit_days", "actual_transit_days"], "mart_logistics", "positive cost/quantity/transit days")

log_df.to_csv(f"{OUT_DIR}/mart_logistics.csv", index=False)

# ---------------------------------------------------------------------------
# Mart 4: Network (edges + origin/dest node info, ready for NetworkX + map)
# ---------------------------------------------------------------------------
net = network_edges.merge(
    nodes[["node_id", "name", "node_type", "country", "lat", "lon"]].rename(
        columns={"node_id": "origin_node_id", "name": "origin_name", "node_type": "origin_type",
                 "country": "origin_country", "lat": "origin_lat", "lon": "origin_lon"}
    ),
    on="origin_node_id", how="left",
).merge(
    nodes[["node_id", "name", "node_type", "country", "lat", "lon"]].rename(
        columns={"node_id": "dest_node_id", "name": "dest_name", "node_type": "dest_type",
                 "country": "dest_country", "lat": "dest_lat", "lon": "dest_lon"}
    ),
    on="dest_node_id", how="left",
)

check_unique(net, "edge_id", "mart_network", "edge_id uniqueness")
check_fk(net, "origin_node_id", valid_node_ids, "mart_network", "origin_node_id FK")
check_fk(net, "dest_node_id", valid_node_ids, "mart_network", "dest_node_id FK")
check_positive(net, ["avg_lead_time_days", "avg_cost_per_unit", "capacity"], "mart_network", "positive lead time/cost/capacity")

net.to_csv(f"{OUT_DIR}/mart_network.csv", index=False)

# ---------------------------------------------------------------------------
# Validation summary
# ---------------------------------------------------------------------------
val_df = pd.DataFrame(VALIDATION_LOG)
val_df.to_csv(f"{OUT_DIR}/validation_report.csv", index=False)

n_fail = (~val_df["passed"]).sum()
print()
print(f"Validation checks run: {len(val_df)} | Passed: {(val_df['passed']).sum()} | Failed: {n_fail}")
if n_fail:
    print("FAILED CHECKS:")
    print(val_df[~val_df["passed"]].to_string(index=False))

print()
print("Mart shapes:")
print("mart_procurement:", po.shape)
print("mart_inventory:", inv.shape)
print("mart_logistics:", log_df.shape)
print("mart_network:", net.shape)