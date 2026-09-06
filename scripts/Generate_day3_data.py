"""
Day 3 - Generate synthetic warehouse/distribution-center/customer-region
nodes, inventory snapshots, shipments, and the network edge table.

Depends on Day 2 outputs: data/nodes_suppliers.csv, data/products.csv
Run this from the same folder Day 2 was run in (or point IN_DIR below
at wherever those CSVs live).

Flow modeled: supplier -> warehouse -> distribution_center -> customer_region
Each shipment moves one product batch across one leg of that chain.
Network edges are the AGGREGATED lanes (one row per unique source-target-mode
combination) with average lead time / cost / capacity - this is what Week 4's
NetworkX graph and PuLP optimization will read from.
"""

import os
import random
import numpy as np
import pandas as pd
from datetime import date, timedelta
from faker import Faker

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
faker = Faker()
Faker.seed(SEED)

# Assumes this script lives in scripts/ and is run from inside scripts/,
# with the repo structure: repo_root/scripts/, repo_root/data/raw/
IN_DIR = "../data/raw"
OUT_DIR = "../data/raw"
os.makedirs(OUT_DIR, exist_ok=True)

suppliers_df = pd.read_csv(f"{IN_DIR}/nodes_suppliers.csv")
products_df = pd.read_csv(f"{IN_DIR}/products.csv")

N_WAREHOUSES = 10
N_DISTRIBUTION_CENTERS = 6
N_CUSTOMER_REGIONS = 30

# Reuse a small set of hub countries/coords for warehouses & DCs (logistics
# hubs cluster in a handful of major trade/transit countries), and a wider
# spread of destination markets for customer regions.
HUB_LOCATIONS = [
    ("Singapore", 1.35, 103.82), ("Germany", 51.16, 10.45), ("United States", 39.82, -98.58),
    ("United Arab Emirates", 23.42, 53.85), ("Netherlands", 52.13, 5.29), ("China", 35.86, 104.20),
    ("India", 20.59, 78.96), ("Mexico", 23.63, -102.55), ("South Korea", 35.91, 127.77),
    ("Poland", 51.92, 19.15),
]
CUSTOMER_COUNTRIES = [
    ("United States", 39.82, -98.58), ("United Kingdom", 55.38, -3.44), ("Germany", 51.16, 10.45),
    ("France", 46.23, 2.21), ("Japan", 36.20, 138.25), ("Australia", -25.27, 133.78),
    ("Canada", 56.13, -106.35), ("Brazil", -14.24, -51.93), ("South Africa", -30.56, 22.94),
    ("India", 20.59, 78.96), ("South Korea", 35.91, 127.77), ("Spain", 40.46, -3.75),
    ("Italy", 41.87, 12.57), ("Netherlands", 52.13, 5.29), ("UAE", 23.42, 53.85),
]

CATEGORIES_UNUSED = None  # placeholder, not used here

# Country risk tiers - must match the tiers used in generate_day2_data.py's
# COUNTRY_RISK table, so that shipment lateness on the supplier->warehouse
# leg is correlated with the same risk signal the Day 6 model uses. This
# is the fix for the near-random AUC found when this leg's lateness was
# generated independently of supplier risk.
SUPPLIER_COUNTRY_RISK_TIER = {
    "Germany": 0, "Japan": 0, "United States": 0,
    "South Korea": 1, "China": 1, "Poland": 1,
    "India": 2, "Mexico": 2, "Vietnam": 2, "Thailand": 2, "Indonesia": 2, "Philippines": 2,
    "Brazil": 3, "Turkey": 3, "South Africa": 3, "Bangladesh": 3, "Sri Lanka": 3, "Egypt": 3,
    "Nigeria": 4, "Pakistan": 4,
}

# ---------------------------------------------------------------------------
# 1. Warehouses, distribution centers, customer regions -> nodes table
# ---------------------------------------------------------------------------
def jitter(v, spread=1.5):
    return round(v + np.random.uniform(-spread, spread), 4)

new_nodes = []
for i in range(N_WAREHOUSES):
    country, lat, lon = random.choice(HUB_LOCATIONS)
    new_nodes.append({
        "node_id": f"WH{i+1:04d}", "node_type": "warehouse", "name": f"{country} Warehouse {i+1}",
        "country": country, "region": "", "lat": jitter(lat), "lon": jitter(lon),
        "onboarding_date": "", "esg_score": "",
    })
for i in range(N_DISTRIBUTION_CENTERS):
    country, lat, lon = random.choice(HUB_LOCATIONS)
    new_nodes.append({
        "node_id": f"DC{i+1:04d}", "node_type": "distribution_center", "name": f"{country} Distribution Center {i+1}",
        "country": country, "region": "", "lat": jitter(lat), "lon": jitter(lon),
        "onboarding_date": "", "esg_score": "",
    })
for i in range(N_CUSTOMER_REGIONS):
    country, lat, lon = random.choice(CUSTOMER_COUNTRIES)
    new_nodes.append({
        "node_id": f"CUST{i+1:04d}", "node_type": "customer_region", "name": f"{country} Region {i+1}",
        "country": country, "region": "", "lat": jitter(lat, 3), "lon": jitter(lon, 3),
        "onboarding_date": "", "esg_score": "",
    })

new_nodes_df = pd.DataFrame(new_nodes)
suppliers_df_aligned = suppliers_df[new_nodes_df.columns]  # ensure same column order
nodes_df = pd.concat([suppliers_df_aligned, new_nodes_df], ignore_index=True)
nodes_df.to_csv(f"{OUT_DIR}/nodes.csv", index=False)

warehouses = nodes_df[nodes_df.node_type == "warehouse"]
dcs = nodes_df[nodes_df.node_type == "distribution_center"]
customers = nodes_df[nodes_df.node_type == "customer_region"]

# ---------------------------------------------------------------------------
# 2. Mode cost/speed profile (used for both shipments and edges)
# ---------------------------------------------------------------------------
MODE_PROFILE = {
    # mode: (days_per_1000km, cost_per_km_per_unit, base_days)
    "air":   (0.35, 0.045, 1),
    "ocean": (2.2,  0.006, 3),
    "road":  (1.1,  0.018, 0),
    "rail":  (1.5,  0.010, 1),
}

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1-a))

def pick_mode(distance_km):
    if distance_km > 6000:
        return np.random.choice(["ocean", "air"], p=[0.75, 0.25])
    elif distance_km > 1500:
        return np.random.choice(["ocean", "rail", "air"], p=[0.5, 0.3, 0.2])
    else:
        return np.random.choice(["road", "rail"], p=[0.7, 0.3])

# ---------------------------------------------------------------------------
# 3. Shipments across three legs: supplier->warehouse, warehouse->DC, DC->customer
# ---------------------------------------------------------------------------
def make_shipments(origin_df, dest_df, n, product_ids, leg_name, risk_lookup=None):
    rows = []
    for _ in range(n):
        o = origin_df.sample(1).iloc[0]
        d = dest_df.sample(1).iloc[0]
        product_id = random.choice(product_ids)
        distance = max(50, haversine(o.lat, o.lon, d.lat, d.lon))
        mode = pick_mode(distance)
        days_per_1000, cost_per_km_unit, base_days = MODE_PROFILE[mode]
        planned_days = max(1, round(base_days + distance / 1000 * days_per_1000))

        # Risk-correlated lateness: only meaningful on the supplier-origin
        # leg, where "origin country risk" is a real business signal.
        risk_tier = risk_lookup.get(o.country, 0) if risk_lookup else 0
        noise_scale = {"air": 0.5, "road": 0.5, "rail": 1.0, "ocean": 1.5}[mode]
        extra_delay_mean = risk_tier * 0.8
        extra_noise = risk_tier * 0.3
        actual_days = max(1, int(np.random.normal(planned_days + extra_delay_mean, noise_scale + extra_noise)))

        quantity = int(np.random.lognormal(mean=5.2, sigma=0.7))
        quantity = max(5, min(quantity, 15000))
        cost = round(distance * cost_per_km_unit * quantity, 2)
        ship_date = faker.date_between(start_date="-2y", end_date="-5d")
        delivery_date = pd.to_datetime(ship_date) + timedelta(days=int(actual_days))
        rows.append({
            "origin_node_id": o.node_id, "dest_node_id": d.node_id, "product_id": product_id,
            "mode": mode, "ship_date": ship_date, "delivery_date": delivery_date.date(),
            "planned_transit_days": planned_days, "actual_transit_days": actual_days,
            "cost": cost, "quantity": quantity, "leg": leg_name,
        })
    return rows

product_ids = products_df.product_id.tolist()
shipment_rows = []
shipment_rows += make_shipments(suppliers_df, warehouses, 3000, product_ids, "supplier_to_warehouse", risk_lookup=SUPPLIER_COUNTRY_RISK_TIER)
shipment_rows += make_shipments(warehouses, dcs, 2000, product_ids, "warehouse_to_dc")
shipment_rows += make_shipments(dcs, customers, 4000, product_ids, "dc_to_customer")

shipments_df = pd.DataFrame(shipment_rows)
shipments_df.insert(0, "shipment_id", [f"SHP{i+1:06d}" for i in range(len(shipments_df))])
shipments_df.drop(columns=["leg"]).to_csv(f"{OUT_DIR}/shipments.csv", index=False)
# keep the leg-labeled version too - useful for dbt staging / debugging
shipments_df.to_csv(f"{OUT_DIR}/shipments_with_leg.csv", index=False)

# ---------------------------------------------------------------------------
# 4. Network edges - aggregate shipments into lanes, add unused lanes too
#    so the graph has some slack capacity (needed for optimization later)
# ---------------------------------------------------------------------------
agg = shipments_df.groupby(["origin_node_id", "dest_node_id", "mode"]).agg(
    avg_lead_time_days=("actual_transit_days", "mean"),
    avg_cost_per_unit=("cost", lambda s: (s / shipments_df.loc[s.index, "quantity"]).mean()),
    shipment_count=("shipment_id", "count"),
    total_quantity=("quantity", "sum"),
).reset_index()

agg["capacity"] = (agg["total_quantity"] / agg["shipment_count"] * np.random.uniform(1.8, 2.5, len(agg))).round().astype(int)
agg["avg_lead_time_days"] = agg["avg_lead_time_days"].round(1)
agg["avg_cost_per_unit"] = agg["avg_cost_per_unit"].round(3)
agg.insert(0, "edge_id", [f"EDGE{i+1:05d}" for i in range(len(agg))])

edges_df = agg.drop(columns=["shipment_count", "total_quantity"])
edges_df.to_csv(f"{OUT_DIR}/network_edges.csv", index=False)

# ---------------------------------------------------------------------------
# 5. Inventory snapshots - monthly stock levels per warehouse x product
# ---------------------------------------------------------------------------
months = pd.date_range(end=date.today(), periods=24, freq="MS")
inv_rows = []
sid = 1
# each warehouse stocks a random subset of products, not all 150
for _, wh in warehouses.iterrows():
    stocked_products = random.sample(product_ids, k=random.randint(40, 90))
    for product_id in stocked_products:
        base_stock = np.random.randint(200, 5000)
        for m in months:
            # random walk with seasonal-ish noise
            stock_qty = max(0, int(base_stock + np.random.normal(0, base_stock * 0.15)))
            reorder_point = int(base_stock * 0.25)
            safety_stock = int(base_stock * 0.10)
            inv_rows.append({
                "snapshot_id": f"INVSNP{sid:07d}", "warehouse_node_id": wh.node_id,
                "product_id": product_id, "snapshot_date": m.date(),
                "stock_qty": stock_qty, "reorder_point": reorder_point, "safety_stock": safety_stock,
            })
            sid += 1
inventory_df = pd.DataFrame(inv_rows)
inventory_df.to_csv(f"{OUT_DIR}/inventory_snapshots.csv", index=False)

# ---------------------------------------------------------------------------
print("Nodes total:", nodes_df.shape, "-", nodes_df.node_type.value_counts().to_dict())
print("Shipments:", shipments_df.shape)
print("Network edges:", edges_df.shape)
print("Inventory snapshots:", inventory_df.shape)