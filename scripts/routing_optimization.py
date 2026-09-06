"""
Day 8 - Shipment routing optimization (PuLP): decide how much monthly
volume should flow over each DC -> customer_region lane and mode, to
minimize total cost, without making service any worse than today's
actual average delivery speed per customer.

Reads from ../data/marts/ (mart_network.csv, mart_logistics.csv).
Writes to ../data/models/.

Model: a linear program.
  Decision variable   x[dc, customer, mode] = monthly quantity shipped
  Objective            minimize sum(cost_per_unit * x)
  Demand constraint    for each customer: sum(x over dc,mode) >= monthly demand
  Capacity constraint  for each (dc, customer, mode) lane: x <= lane capacity
  Service constraint   for each customer: volume-weighted lead time of the
                        plan <= that customer's actual historical average
                        lead time (don't let the optimizer "solve" cost by
                        making deliveries slower than they are today)

If the service constraint makes the problem infeasible for a given
customer's demand/capacity, the script automatically retries the whole
model with that constraint relaxed and says so - a cost-only answer is
still useful, just flagged as a different comparison basis.

Run from inside scripts/, after build_marts.py.
"""

import os
import numpy as np
import pandas as pd
import pulp
import mlflow

IN_DIR = "../data/marts"
OUT_DIR = "../data/models"
os.makedirs(OUT_DIR, exist_ok=True)

network = pd.read_csv(f"{IN_DIR}/mart_network.csv")
logistics = pd.read_csv(f"{IN_DIR}/mart_logistics.csv", parse_dates=["ship_date"])

# ---------------------------------------------------------------------------
# Available lanes: DC -> customer_region, with cost/lead time/capacity
# ---------------------------------------------------------------------------
lanes = network[(network["origin_type"] == "distribution_center") & (network["dest_type"] == "customer_region")].copy()
lanes = lanes.rename(columns={"origin_node_id": "dc", "dest_node_id": "customer"})
lanes = lanes[["dc", "customer", "mode", "avg_cost_per_unit", "avg_lead_time_days", "capacity"]]

# ---------------------------------------------------------------------------
# Historical baseline per customer: monthly demand, actual cost/unit paid,
# actual average lead time (this is both the "actual" comparison basis and
# the service-level floor the optimizer isn't allowed to violate)
# ---------------------------------------------------------------------------
hist = logistics[(logistics["origin_type"] == "distribution_center") & (logistics["dest_type"] == "customer_region")].copy()
n_months = max(1, (hist["ship_date"].max() - hist["ship_date"].min()).days / 30.4)

customer_stats = hist.groupby("dest_node_id").apply(
    lambda g: pd.Series({
        "monthly_demand": g["quantity"].sum() / n_months,
        "actual_cost_per_unit": (g["cost"].sum() / g["quantity"].sum()) if g["quantity"].sum() > 0 else 0,
        "actual_avg_lead_time": np.average(g["actual_transit_days"], weights=g["quantity"]),
    }),
    include_groups=False,
).reset_index().rename(columns={"dest_node_id": "customer"})

print(f"Optimizing across {lanes['dc'].nunique()} DCs, {customer_stats.shape[0]} customer regions, "
      f"{lanes.shape[0]} candidate lanes (~{n_months:.1f} months of history used for demand/service baselines)")

# ---------------------------------------------------------------------------
# Build and solve the LP
# ---------------------------------------------------------------------------
def build_and_solve(enforce_service_level=True):
    prob = pulp.LpProblem("routing_optimization", pulp.LpMinimize)

    lane_keys = list(lanes.itertuples(index=False, name=None))  # (dc, customer, mode, cost, lead_time, capacity)
    x = {
        (dc, customer, mode): pulp.LpVariable(f"x_{dc}_{customer}_{mode}", lowBound=0)
        for dc, customer, mode, cost, lead_time, capacity in lane_keys
    }
    cost_map = {(dc, customer, mode): cost for dc, customer, mode, cost, lead_time, capacity in lane_keys}
    lead_map = {(dc, customer, mode): lead_time for dc, customer, mode, cost, lead_time, capacity in lane_keys}
    cap_map = {(dc, customer, mode): capacity for dc, customer, mode, cost, lead_time, capacity in lane_keys}

    prob += pulp.lpSum(cost_map[k] * x[k] for k in x)  # objective: minimize total cost

    for k in x:
        prob += x[k] <= cap_map[k]

    for _, row in customer_stats.iterrows():
        c = row["customer"]
        vars_for_c = [k for k in x if k[1] == c]
        if not vars_for_c:
            continue
        prob += pulp.lpSum(x[k] for k in vars_for_c) >= row["monthly_demand"]
        if enforce_service_level:
            prob += pulp.lpSum(lead_map[k] * x[k] for k in vars_for_c) <= row["actual_avg_lead_time"] * row["monthly_demand"]

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    return prob, x, cost_map

prob, x, cost_map = build_and_solve(enforce_service_level=True)
service_relaxed = False
if pulp.LpStatus[prob.status] != "Optimal":
    print(f"Status with service constraint: {pulp.LpStatus[prob.status]} - retrying without it.")
    prob, x, cost_map = build_and_solve(enforce_service_level=False)
    service_relaxed = True

print(f"Solver status: {pulp.LpStatus[prob.status]} (service constraint relaxed: {service_relaxed})")

# ---------------------------------------------------------------------------
# Extract the routing plan
# ---------------------------------------------------------------------------
plan_rows = []
for (dc, customer, mode), var in x.items():
    qty = var.value() or 0
    if qty > 0.5:
        plan_rows.append({
            "dc": dc, "customer": customer, "mode": mode,
            "recommended_monthly_quantity": round(qty, 1),
            "cost_per_unit": cost_map[(dc, customer, mode)],
            "recommended_monthly_cost": round(qty * cost_map[(dc, customer, mode)], 2),
        })
plan_df = pd.DataFrame(plan_rows).sort_values(["customer", "recommended_monthly_cost"], ascending=[True, False])
plan_df.to_csv(f"{OUT_DIR}/optimized_routing_plan.csv", index=False)

# ---------------------------------------------------------------------------
# Recommended vs actual comparison per customer
# ---------------------------------------------------------------------------
optimized_cost_by_customer = plan_df.groupby("customer")["recommended_monthly_cost"].sum()
comparison = customer_stats.copy()
comparison["actual_monthly_cost"] = comparison["monthly_demand"] * comparison["actual_cost_per_unit"]
comparison["optimized_monthly_cost"] = comparison["customer"].map(optimized_cost_by_customer).fillna(comparison["actual_monthly_cost"])
comparison["monthly_savings"] = comparison["actual_monthly_cost"] - comparison["optimized_monthly_cost"]
comparison["savings_pct"] = (comparison["monthly_savings"] / comparison["actual_monthly_cost"] * 100).round(1)
comparison.to_csv(f"{OUT_DIR}/recommended_vs_actual_routing.csv", index=False)

total_actual = comparison["actual_monthly_cost"].sum()
total_optimized = comparison["optimized_monthly_cost"].sum()
total_savings = total_actual - total_optimized
savings_pct = total_savings / total_actual * 100 if total_actual else 0

print(f"\nTotal actual monthly cost:    ${total_actual:,.0f}")
print(f"Total optimized monthly cost: ${total_optimized:,.0f}")
print(f"Estimated monthly savings:    ${total_savings:,.0f} ({savings_pct:.1f}%)")
print(f"(service-level floor {'was relaxed to reach a feasible solution' if service_relaxed else 'held: no customer gets slower average delivery than today'})")

# ---------------------------------------------------------------------------
# MLflow tracking
# ---------------------------------------------------------------------------
mlflow.set_experiment("routing_optimization")
with mlflow.start_run(run_name="day8_pulp_routing_optimization"):
    mlflow.log_param("n_lanes", lanes.shape[0])
    mlflow.log_param("n_customers", customer_stats.shape[0])
    mlflow.log_param("service_level_enforced", not service_relaxed)
    mlflow.log_param("solver_status", pulp.LpStatus[prob.status])
    mlflow.log_metric("total_actual_monthly_cost", float(total_actual))
    mlflow.log_metric("total_optimized_monthly_cost", float(total_optimized))
    mlflow.log_metric("monthly_savings", float(total_savings))
    mlflow.log_metric("savings_pct", float(savings_pct))

    mlflow.log_artifact(f"{OUT_DIR}/optimized_routing_plan.csv")
    mlflow.log_artifact(f"{OUT_DIR}/recommended_vs_actual_routing.csv")

print("\nRun 'mlflow ui' from this folder's parent to view the tracked run.")