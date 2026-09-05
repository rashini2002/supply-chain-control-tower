"""
Day 2 - Generate synthetic supplier, purchase order, invoice, and
supplier risk signal data for the Global Supply Chain Control Tower.

Approach:
- Suppliers and products are generated directly (Faker + numpy) since
  they don't need learned correlations.
- Purchase orders are generated in two stages: a small hand-authored
  "seed" that encodes the correlations we WANT the real data to show
  (higher-risk countries -> more price variance, category -> price
  band, etc.), then SDV's GaussianCopulaSynthesizer learns and scales
  that seed up to full volume. This is the standard way to use SDV
  when you don't have a real historical dataset to fit on.
- Invoices are derived deterministically from purchase orders.
- Country risk / currency volatility values below are ILLUSTRATIVE
  PLACEHOLDERS loosely inspired by published risk tiers (not exact
  OECD/World Bank figures). Before final submission, swap these for
  the real OECD Country Risk Classification or World Bank Worldwide
  Governance Indicators (both public, downloadable as CSV).
"""

import random
import numpy as np
import pandas as pd
from datetime import date, timedelta
from faker import Faker
from sdv.metadata import Metadata
from sdv.single_table import GaussianCopulaSynthesizer

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
faker = Faker()
Faker.seed(SEED)

OUT_DIR = "data"
import os
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. Country risk reference table (placeholder tiers - see docstring above)
# ---------------------------------------------------------------------------
COUNTRY_RISK = {
    # country: (risk_tier 0=low..4=high, base_country_risk_index, base_currency_volatility)
    "Germany":        (0, 12, 0.04),
    "Japan":          (0, 14, 0.05),
    "United States":  (0, 15, 0.03),
    "South Korea":    (1, 22, 0.07),
    "China":          (1, 28, 0.08),
    "Poland":         (1, 30, 0.09),
    "India":          (2, 38, 0.10),
    "Mexico":         (2, 40, 0.12),
    "Vietnam":        (2, 42, 0.11),
    "Thailand":       (2, 35, 0.09),
    "Brazil":         (3, 52, 0.16),
    "Turkey":         (3, 58, 0.22),
    "South Africa":   (3, 55, 0.18),
    "Indonesia":      (2, 44, 0.13),
    "Bangladesh":     (3, 56, 0.15),
    "Sri Lanka":      (3, 60, 0.20),
    "Nigeria":        (4, 68, 0.27),
    "Pakistan":       (4, 66, 0.24),
    "Egypt":          (3, 54, 0.19),
    "Philippines":    (2, 41, 0.12),
}
COUNTRIES = list(COUNTRY_RISK.keys())

# Rough centroid coordinates for the geospatial map later
COUNTRY_COORDS = {
    "Germany": (51.16, 10.45), "Japan": (36.20, 138.25), "United States": (39.82, -98.58),
    "South Korea": (35.91, 127.77), "China": (35.86, 104.20), "Poland": (51.92, 19.15),
    "India": (20.59, 78.96), "Mexico": (23.63, -102.55), "Vietnam": (14.06, 108.28),
    "Thailand": (15.87, 100.99), "Brazil": (-14.24, -51.93), "Turkey": (38.96, 35.24),
    "South Africa": (-30.56, 22.94), "Indonesia": (-0.79, 113.92), "Bangladesh": (23.68, 90.36),
    "Sri Lanka": (7.87, 80.77), "Nigeria": (9.08, 8.68), "Pakistan": (30.38, 69.35),
    "Egypt": (26.82, 30.80), "Philippines": (12.88, 121.77),
}

CATEGORIES = ["Electronics", "Textiles", "Automotive Parts", "Chemicals",
              "Packaging", "Machinery", "Furniture", "Food & Beverage"]

N_SUPPLIERS = 40
N_PRODUCTS = 150

# ---------------------------------------------------------------------------
# 2. Suppliers (nodes table, node_type = 'supplier')
# ---------------------------------------------------------------------------
supplier_rows = []
for i in range(N_SUPPLIERS):
    country = random.choice(COUNTRIES)
    lat_c, lon_c = COUNTRY_COORDS[country]
    supplier_rows.append({
        "node_id": f"SUP{i+1:04d}",
        "node_type": "supplier",
        "name": faker.company(),
        "country": country,
        "region": "",  # optional: fill with continent mapping if needed
        "lat": round(lat_c + np.random.uniform(-2, 2), 4),
        "lon": round(lon_c + np.random.uniform(-2, 2), 4),
        "onboarding_date": faker.date_between(start_date="-6y", end_date="-6M"),
        "esg_score": round(np.clip(np.random.normal(65, 15), 10, 98), 1),
    })
suppliers_df = pd.DataFrame(supplier_rows)
suppliers_df.to_csv(f"{OUT_DIR}/nodes_suppliers.csv", index=False)

# ---------------------------------------------------------------------------
# 3. Products
# ---------------------------------------------------------------------------
product_rows = []
for i in range(N_PRODUCTS):
    category = random.choice(CATEGORIES)
    base_cost = {
        "Electronics": (20, 400), "Textiles": (2, 40), "Automotive Parts": (15, 300),
        "Chemicals": (5, 120), "Packaging": (1, 15), "Machinery": (100, 5000),
        "Furniture": (30, 600), "Food & Beverage": (1, 25),
    }[category]
    unit_cost = round(np.random.uniform(*base_cost), 2)
    product_rows.append({
        "product_id": f"PRD{i+1:04d}",
        "name": f"{category[:4].upper()}-{faker.word().capitalize()}-{i+1}",
        "category": category,
        "unit_cost": unit_cost,
        "unit_price": round(unit_cost * np.random.uniform(1.15, 1.6), 2),
    })
products_df = pd.DataFrame(product_rows)
products_df.to_csv(f"{OUT_DIR}/products.csv", index=False)

# ---------------------------------------------------------------------------
# 4. Purchase order SEED (hand-authored correlations) -> SDV scale-up
# ---------------------------------------------------------------------------
SEED_SIZE = 300
seed_rows = []
for _ in range(SEED_SIZE):
    supplier = suppliers_df.sample(1).iloc[0]
    product = products_df.sample(1).iloc[0]
    risk_tier = COUNTRY_RISK[supplier["country"]][0]

    # Higher risk tier -> wider price variance and more cancellations
    price_variance = 1 + (risk_tier * 0.05)
    unit_price = round(product["unit_cost"] * np.random.uniform(1.0, 1.3) * price_variance, 2)
    quantity = int(np.random.lognormal(mean=5.5, sigma=0.8))
    quantity = max(10, min(quantity, 20000))

    cancel_prob = 0.03 + risk_tier * 0.02
    fulfilled_prob = 0.80 - risk_tier * 0.02
    open_prob = max(0.01, 1 - cancel_prob - fulfilled_prob)
    probs = np.array([fulfilled_prob, open_prob, cancel_prob])
    probs = probs / probs.sum()
    status = np.random.choice(["fulfilled", "open", "cancelled"], p=probs)
    order_date = faker.date_between(start_date="-2y", end_date="today")

    seed_rows.append({
        "supplier_node_id": supplier["node_id"],
        "product_id": product["product_id"],
        "order_date": order_date,
        "quantity": quantity,
        "unit_price": unit_price,
        "currency": "USD",
        "total_value": round(quantity * unit_price, 2),
        "status": status,
        "risk_tier": risk_tier,  # helper column for SDV to learn from; dropped before saving
    })
po_seed_df = pd.DataFrame(seed_rows)

metadata = Metadata.detect_from_dataframe(data=po_seed_df, table_name="purchase_orders")
metadata.update_column(table_name="purchase_orders", column_name="order_date", sdtype="datetime", datetime_format="%Y-%m-%d")
metadata.update_column(table_name="purchase_orders", column_name="supplier_node_id", sdtype="categorical")
metadata.update_column(table_name="purchase_orders", column_name="product_id", sdtype="categorical")
metadata.update_column(table_name="purchase_orders", column_name="status", sdtype="categorical")
metadata.update_column(table_name="purchase_orders", column_name="currency", sdtype="categorical")

synthesizer = GaussianCopulaSynthesizer(metadata)
synthesizer.fit(po_seed_df)

N_PO = 5000
po_df = synthesizer.sample(num_rows=N_PO)
po_df = po_df.drop(columns=["risk_tier"])
po_df["quantity"] = po_df["quantity"].clip(lower=1).round().astype(int)
po_df["unit_price"] = po_df["unit_price"].clip(lower=0.1).round(2)
po_df["total_value"] = (po_df["quantity"] * po_df["unit_price"]).round(2)
po_df.insert(0, "po_id", [f"PO{str(i+1).zfill(6)}" for i in range(len(po_df))])
po_df.to_csv(f"{OUT_DIR}/purchase_orders.csv", index=False)

# ---------------------------------------------------------------------------
# 5. Invoices (derived deterministically from purchase orders)
# ---------------------------------------------------------------------------
invoice_rows = []
for i, po in po_df.iterrows():
    if po["status"] == "cancelled":
        continue
    order_date = pd.to_datetime(po["order_date"])
    invoice_date = order_date + timedelta(days=int(np.random.randint(1, 15)))
    payment_status = np.random.choice(["paid", "pending", "overdue"], p=[0.7, 0.2, 0.1])
    payment_date = None
    if payment_status == "paid":
        payment_date = invoice_date + timedelta(days=int(np.random.randint(5, 45)))
    invoice_rows.append({
        "invoice_id": f"INV{str(i+1).zfill(6)}",
        "po_id": po["po_id"],
        "invoice_date": invoice_date.date(),
        "amount": po["total_value"],
        "payment_status": payment_status,
        "payment_date": payment_date.date() if payment_date is not None else "",
    })
invoices_df = pd.DataFrame(invoice_rows)
invoices_df.to_csv(f"{OUT_DIR}/invoices.csv", index=False)

# ---------------------------------------------------------------------------
# 6. Supplier risk signals (monthly, per supplier)
# ---------------------------------------------------------------------------
months = pd.date_range(end=date.today(), periods=24, freq="MS").strftime("%Y-%m")
risk_rows = []
sig_id = 1
for _, supplier in suppliers_df.iterrows():
    tier, base_cri, base_vol = COUNTRY_RISK[supplier["country"]]
    for m in months:
        risk_rows.append({
            "signal_id": f"RSK{sig_id:06d}",
            "supplier_node_id": supplier["node_id"],
            "month": m,
            "country_risk_index": round(np.clip(np.random.normal(base_cri, 4), 5, 95), 1),
            "currency_volatility": round(np.clip(np.random.normal(base_vol, base_vol * 0.2), 0.01, 0.5), 3),
            "sanctions_flag": bool(np.random.random() < (0.01 if tier < 3 else 0.04)),
        })
        sig_id += 1
risk_df = pd.DataFrame(risk_rows)
risk_df.to_csv(f"{OUT_DIR}/supplier_risk_signals.csv", index=False)

# ---------------------------------------------------------------------------
print("Suppliers:", suppliers_df.shape)
print("Products:", products_df.shape)
print("Purchase orders:", po_df.shape)
print("Invoices:", invoices_df.shape)
print("Supplier risk signals:", risk_df.shape)