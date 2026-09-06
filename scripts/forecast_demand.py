"""
5 - Demand forecasting model + dynamic reorder points, tracked in MLflow.

Reads from ../data/marts/ (mart_logistics.csv, mart_inventory.csv,
mart_network.csv). Writes to ../data/models/.

Demand signal: quantity shipped on the dc_to_customer leg, aggregated to
monthly totals per product. This is the closest real proxy for "demand"
in this dataset (see docs/DECISIONS.md).

Forecasting: Prophet per product where enough history exists, with a
graceful fallback to a weighted moving average if Prophet isn't installed
or a product doesn't have enough history to fit.

Reorder point formula (classic inventory formula):
    reorder_point = daily_demand_forecast * avg_lead_time_days + safety_stock
    safety_stock  = z * std_dev_of_daily_demand * sqrt(avg_lead_time_days)
z = 1.65 targets roughly a 95% service level.

Run from inside scripts/, after build_marts.py has been run.
"""

import os
import math
import numpy as np
import pandas as pd
import mlflow

try:
    from prophet import Prophet
    HAS_PROPHET = True
except ImportError:
    HAS_PROPHET = False
    print("Prophet not installed - falling back to weighted moving average for all products.")

IN_DIR = "../data/marts"
OUT_DIR = "../data/models"
os.makedirs(OUT_DIR, exist_ok=True)

Z_SCORE = 1.65  # ~95% service level

# ---------------------------------------------------------------------------
# Load marts
# ---------------------------------------------------------------------------
logistics = pd.read_csv(f"{IN_DIR}/mart_logistics.csv", parse_dates=["ship_date"])
inventory = pd.read_csv(f"{IN_DIR}/mart_inventory.csv")
network = pd.read_csv(f"{IN_DIR}/mart_network.csv")

# ---------------------------------------------------------------------------
# Build monthly demand series per product from dc_to_customer shipments
# ---------------------------------------------------------------------------
demand = logistics[logistics["dest_type"] == "customer_region"].copy()
demand["month"] = demand["ship_date"].values.astype("datetime64[M]")
monthly_demand = (
    demand.groupby(["product_id", "month"])["quantity"]
    .sum()
    .reset_index()
    .rename(columns={"quantity": "demand_qty"})
)

# Average lead time for the replenishment leg (supplier -> warehouse) -
# this is what actually determines how far ahead you need to reorder.
replenishment_lead_time = network.loc[
    network["origin_type"] == "supplier", "avg_lead_time_days"
].mean()
print(f"Average supplier->warehouse lead time used for reorder points: {replenishment_lead_time:.1f} days")

# ---------------------------------------------------------------------------
# Forecast next-month demand per product
# ---------------------------------------------------------------------------
def forecast_with_prophet(series_df):
    df = series_df.rename(columns={"month": "ds", "demand_qty": "y"})
    m = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
    m.fit(df)
    future = m.make_future_dataframe(periods=1, freq="MS")
    fcst = m.predict(future)
    return max(0, fcst.iloc[-1]["yhat"])

def forecast_with_moving_average(series_df, window=3):
    recent = series_df.sort_values("month").tail(window)["demand_qty"]
    if len(recent) == 0:
        return 0.0
    weights = np.arange(1, len(recent) + 1)
    return float(np.average(recent, weights=weights))

def backtest_mae(series_df, forecast_fn, holdout=3):
    series_df = series_df.sort_values("month")
    if len(series_df) <= holdout + 3:
        return None  # not enough history to backtest meaningfully
    train = series_df.iloc[:-holdout]
    actuals = series_df.iloc[-holdout:]["demand_qty"].values
    preds = []
    running_train = train.copy()
    for _ in range(holdout):
        pred = forecast_fn(running_train)
        preds.append(pred)
        # roll forward: pretend we now know one more real month
        next_row = series_df.iloc[len(running_train):len(running_train) + 1]
        running_train = pd.concat([running_train, next_row])
    return float(np.mean(np.abs(np.array(preds) - actuals)))

results = []
backtest_maes = []

for product_id, grp in monthly_demand.groupby("product_id"):
    grp = grp.sort_values("month")
    use_prophet = HAS_PROPHET and len(grp) >= 8

    forecast_fn = forecast_with_prophet if use_prophet else forecast_with_moving_average
    model_type = "prophet" if use_prophet else "weighted_moving_average"

    try:
        next_month_forecast = forecast_fn(grp)
    except Exception as e:
        # fall back gracefully if Prophet fails on a specific product's series
        next_month_forecast = forecast_with_moving_average(grp)
        model_type = "weighted_moving_average_fallback"

    mae = backtest_mae(grp, forecast_fn)
    if mae is not None:
        backtest_maes.append(mae)

    daily_demand_forecast = next_month_forecast / 30
    demand_std = grp["demand_qty"].std(ddof=0) / 30 if len(grp) > 1 else 0.0
    safety_stock = Z_SCORE * demand_std * math.sqrt(replenishment_lead_time)
    reorder_point = daily_demand_forecast * replenishment_lead_time + safety_stock

    results.append({
        "product_id": product_id,
        "model_type": model_type,
        "n_months_history": len(grp),
        "forecast_next_month_demand": round(next_month_forecast, 1),
        "daily_demand_forecast": round(daily_demand_forecast, 2),
        "safety_stock": round(safety_stock, 1),
        "reorder_point_model": round(reorder_point, 1),
        "backtest_mae": round(mae, 1) if mae is not None else None,
    })

forecast_df = pd.DataFrame(results)
forecast_df.to_csv(f"{OUT_DIR}/product_demand_forecast.csv", index=False)

# ---------------------------------------------------------------------------
# Compare model-driven reorder points against the placeholder ones from
# mart_inventory (which were just 25% of a random base stock figure)
# ---------------------------------------------------------------------------
comparison = inventory.merge(
    forecast_df[["product_id", "reorder_point_model", "safety_stock", "model_type"]],
    on="product_id", how="left",
)
comparison["reorder_point_placeholder"] = comparison["reorder_point"]
comparison["reorder_point_change"] = (
    comparison["reorder_point_model"] - comparison["reorder_point_placeholder"]
)
comparison.to_csv(f"{OUT_DIR}/inventory_with_dynamic_reorder.csv", index=False)

# ---------------------------------------------------------------------------
# MLflow tracking
# ---------------------------------------------------------------------------
mlflow.set_experiment("demand_forecasting")
with mlflow.start_run(run_name="day5_product_demand_forecast"):
    mlflow.log_param("prophet_available", HAS_PROPHET)
    mlflow.log_param("n_products_forecasted", len(forecast_df))
    mlflow.log_param("n_products_using_prophet", int((forecast_df["model_type"] == "prophet").sum()))
    mlflow.log_param("replenishment_lead_time_days", round(replenishment_lead_time, 1))
    mlflow.log_param("service_level_z_score", Z_SCORE)

    if backtest_maes:
        mlflow.log_metric("mean_backtest_mae", float(np.mean(backtest_maes)))
        mlflow.log_metric("n_products_backtested", len(backtest_maes))

    mlflow.log_artifact(f"{OUT_DIR}/product_demand_forecast.csv")
    mlflow.log_artifact(f"{OUT_DIR}/inventory_with_dynamic_reorder.csv")

print()
print(f"Forecasted {len(forecast_df)} products "
      f"({(forecast_df['model_type'] == 'prophet').sum()} with Prophet, "
      f"{(forecast_df['model_type'] != 'prophet').sum()} with moving average fallback)")
if backtest_maes:
    print(f"Mean backtest MAE across {len(backtest_maes)} products: {np.mean(backtest_maes):.1f} units")
print("Run 'mlflow ui' from this folder's parent to view the tracked run.")