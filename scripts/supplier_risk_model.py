"""
Day 6 - Supplier risk scoring model (XGBoost) with SHAP explainability,
tracked in MLflow.

Reads from ../data/marts/ (mart_procurement.csv, mart_logistics.csv,
mart_network.csv). Writes to ../data/models/.

Label design (to avoid leakage): the risk LABEL is built only from
outcome-based signals (cancellations, late shipments, overdue payments,
sanctions) - things you only know AFTER dealing with a supplier. The
model PREDICTS that label using only context-based features (country
risk, currency volatility, ESG score, price deviation, spend
concentration, order volume, lead time) - things you'd know BEFORE or
independent of those outcomes. This keeps the model an actual predictive
tool rather than a circular restatement of the label.

Caveat: there are only 40 suppliers in this dataset. That's a small
sample for ML - performance is estimated with stratified k-fold CV
rather than a single train/test split, and hyperparameters are kept
conservative (shallow trees, few estimators) to avoid overfitting.

Run from inside scripts/, after build_marts.py and forecast_demand.py.
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import xgboost as xgb
import shap
import mlflow
import mlflow.xgboost
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import roc_auc_score, accuracy_score

IN_DIR = "../data/marts"
OUT_DIR = "../data/models"
os.makedirs(OUT_DIR, exist_ok=True)

RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)

# ---------------------------------------------------------------------------
# Load marts
# ---------------------------------------------------------------------------
procurement = pd.read_csv(f"{IN_DIR}/mart_procurement.csv")
logistics = pd.read_csv(f"{IN_DIR}/mart_logistics.csv")
network = pd.read_csv(f"{IN_DIR}/mart_network.csv")

# ---------------------------------------------------------------------------
# Feature engineering: price deviation per PO relative to that product's
# average price (fair comparison across differently-priced products)
# ---------------------------------------------------------------------------
product_avg_price = procurement.groupby("product_id")["unit_price"].transform("mean")
procurement["price_deviation_pct"] = ((procurement["unit_price"] - product_avg_price) / product_avg_price * 100).abs()

# ---------------------------------------------------------------------------
# Supplier-level aggregation from procurement
# ---------------------------------------------------------------------------
proc_agg = procurement.groupby("supplier_node_id").agg(
    supplier_name=("supplier_name", "first"),
    supplier_country=("supplier_country", "first"),
    esg_score=("esg_score", "first"),
    total_spend=("total_value", "sum"),
    num_orders=("po_id", "count"),
    cancellation_rate=("status", lambda s: (s == "cancelled").mean()),
    payment_overdue_rate=("payment_status", lambda s: (s == "overdue").mean()),
    avg_country_risk_index=("country_risk_index", "mean"),
    avg_currency_volatility=("currency_volatility", "mean"),
    sanctions_ever=("sanctions_flag", lambda s: float(s.fillna(False).infer_objects(copy=False).astype(bool).max())),
    price_deviation_pct=("price_deviation_pct", "mean"),
).reset_index()

proc_agg["spend_concentration"] = proc_agg["total_spend"] / proc_agg["total_spend"].sum()

# ---------------------------------------------------------------------------
# Supplier-level aggregation from logistics (outbound legs only)
# ---------------------------------------------------------------------------
supplier_shipments = logistics[logistics["origin_type"] == "supplier"]
log_agg = supplier_shipments.groupby("origin_node_id").agg(
    late_shipment_rate=("is_late", "mean"),
    avg_delay_days=("delay_days", "mean"),
).reset_index().rename(columns={"origin_node_id": "supplier_node_id"})

# ---------------------------------------------------------------------------
# Supplier-level aggregation from network (avg lead time on outbound edges)
# ---------------------------------------------------------------------------
supplier_edges = network[network["origin_type"] == "supplier"]
net_agg = supplier_edges.groupby("origin_node_id").agg(
    avg_lead_time_days=("avg_lead_time_days", "mean"),
).reset_index().rename(columns={"origin_node_id": "supplier_node_id"})

# ---------------------------------------------------------------------------
# Combine into one supplier feature table
# ---------------------------------------------------------------------------
df = proc_agg.merge(log_agg, on="supplier_node_id", how="left")
df = df.merge(net_agg, on="supplier_node_id", how="left")
df[["late_shipment_rate", "avg_delay_days", "avg_lead_time_days"]] = df[
    ["late_shipment_rate", "avg_delay_days", "avg_lead_time_days"]
].fillna(df[["late_shipment_rate", "avg_delay_days", "avg_lead_time_days"]].mean())

print(f"Supplier feature table: {df.shape[0]} suppliers, {df.shape[1]} columns")

# ---------------------------------------------------------------------------
# Build the OUTCOME-based composite risk label (top 30% = high risk)
# ---------------------------------------------------------------------------
def minmax(s):
    return (s - s.min()) / (s.max() - s.min() + 1e-9)

df["composite_outcome_risk"] = (
    0.30 * minmax(df["cancellation_rate"]) +
    0.30 * minmax(df["late_shipment_rate"]) +
    0.25 * minmax(df["payment_overdue_rate"]) +
    0.15 * df["sanctions_ever"]
)
threshold = df["composite_outcome_risk"].quantile(0.70)
df["high_risk_label"] = (df["composite_outcome_risk"] >= threshold).astype(int)
print(f"High-risk label: {df['high_risk_label'].sum()} of {len(df)} suppliers flagged (top 30% by composite outcome risk)")

# ---------------------------------------------------------------------------
# CONTEXT-based predictor features (no leakage from the outcome label)
# ---------------------------------------------------------------------------
FEATURES = [
    "avg_country_risk_index", "avg_currency_volatility", "esg_score",
    "price_deviation_pct", "spend_concentration", "num_orders", "avg_lead_time_days",
]
X = df[FEATURES]
y = df["high_risk_label"]

# ---------------------------------------------------------------------------
# Cross-validated performance estimate (n=40 is too small for a held-out split)
# ---------------------------------------------------------------------------
model_params = dict(
    n_estimators=50, max_depth=3, learning_rate=0.1,
    subsample=0.8, colsample_bytree=0.8,
    eval_metric="logloss", random_state=RANDOM_STATE,
)
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
cv_model = xgb.XGBClassifier(**model_params)

cv_proba = cross_val_predict(cv_model, X, y, cv=cv, method="predict_proba")[:, 1]
cv_pred = (cv_proba >= 0.5).astype(int)
cv_auc = roc_auc_score(y, cv_proba) if y.nunique() > 1 else float("nan")
cv_acc = accuracy_score(y, cv_pred)
print(f"5-fold CV AUC: {cv_auc:.3f} | CV accuracy: {cv_acc:.3f}")

# ---------------------------------------------------------------------------
# Fit final model on all 40 suppliers (for SHAP + scoring - see caveat above)
# ---------------------------------------------------------------------------
final_model = xgb.XGBClassifier(**model_params)
final_model.fit(X, y)
df["predicted_risk_probability"] = final_model.predict_proba(X)[:, 1]

# ---------------------------------------------------------------------------
# SHAP explainability
# ---------------------------------------------------------------------------
explainer = shap.TreeExplainer(final_model)
shap_values = explainer.shap_values(X)

mean_abs_shap = pd.DataFrame({
    "feature": FEATURES,
    "mean_abs_shap": np.abs(shap_values).mean(axis=0),
}).sort_values("mean_abs_shap", ascending=False)
mean_abs_shap.to_csv(f"{OUT_DIR}/supplier_risk_shap_importance.csv", index=False)

top_driver_idx = np.abs(shap_values).argmax(axis=1)
df["top_risk_driver"] = [FEATURES[i] for i in top_driver_idx]

plt.figure()
shap.summary_plot(shap_values, X, feature_names=FEATURES, show=False, plot_type="bar")
plt.tight_layout()
shap_plot_path = f"{OUT_DIR}/supplier_risk_shap_summary.png"
plt.savefig(shap_plot_path, dpi=150)
plt.close()

# ---------------------------------------------------------------------------
# Output supplier risk scores for the Tableau supplier risk dashboard
# ---------------------------------------------------------------------------
output_cols = [
    "supplier_node_id", "supplier_name", "supplier_country", "esg_score",
    "composite_outcome_risk", "high_risk_label", "predicted_risk_probability",
    "top_risk_driver", "cancellation_rate", "late_shipment_rate",
    "payment_overdue_rate", "sanctions_ever",
]
df[output_cols].sort_values("predicted_risk_probability", ascending=False).to_csv(
    f"{OUT_DIR}/supplier_risk_scores.csv", index=False
)

# ---------------------------------------------------------------------------
# MLflow tracking
# ---------------------------------------------------------------------------
mlflow.set_experiment("supplier_risk_scoring")
with mlflow.start_run(run_name="day6_supplier_risk_xgboost"):
    mlflow.log_params(model_params)
    mlflow.log_param("n_suppliers", len(df))
    mlflow.log_param("cv_folds", 5)
    mlflow.log_param("high_risk_threshold_percentile", 0.70)
    mlflow.log_metric("cv_auc", cv_auc)
    mlflow.log_metric("cv_accuracy", cv_acc)
    mlflow.log_metric("n_high_risk_suppliers", int(df["high_risk_label"].sum()))

    mlflow.xgboost.log_model(final_model, name="supplier_risk_model")
    mlflow.log_artifact(f"{OUT_DIR}/supplier_risk_scores.csv")
    mlflow.log_artifact(f"{OUT_DIR}/supplier_risk_shap_importance.csv")
    mlflow.log_artifact(shap_plot_path)

print()
print("Top risk drivers overall:")
print(mean_abs_shap.to_string(index=False))
print()
print("Run 'mlflow ui' from this folder's parent to view the tracked run.")