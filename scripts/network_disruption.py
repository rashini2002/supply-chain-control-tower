"""
Day 7 - Build the supply chain network graph (NetworkX) and run a
disruption simulation: for every node, "what if this node failed?" -
which customer regions lose all supply, and how much revenue is at risk.

Reads from ../data/marts/ (mart_network.csv, mart_logistics.csv) and
../data/raw/ (products.csv, for revenue estimation).
Writes to ../data/models/.

Method: a directed graph is built from the network edges (already spans
supplier -> warehouse -> distribution_center -> customer_region, since
Day 3 aggregated shipments across all three legs into one edge table). A
virtual SOURCE node is connected to every supplier so reachability from
"the supply base as a whole" to each customer region can be checked with
one traversal, before and after removing each candidate node.

Revenue at risk for an exposed customer region = quantity x unit_price
summed across its historical dc_to_customer shipments (a demand-side
value proxy, since this dataset has no direct customer revenue field).

Run from inside scripts/, after build_marts.py.
"""

import os
import numpy as np
import pandas as pd
import networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow

IN_MARTS = "../data/marts"
IN_RAW = "../data/raw"
OUT_DIR = "../data/models"
os.makedirs(OUT_DIR, exist_ok=True)

SOURCE = "__SOURCE__"

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
network = pd.read_csv(f"{IN_MARTS}/mart_network.csv")
logistics = pd.read_csv(f"{IN_MARTS}/mart_logistics.csv")
products = pd.read_csv(f"{IN_RAW}/products.csv")

# ---------------------------------------------------------------------------
# Revenue proxy per customer region: quantity x unit_price on dc_to_customer
# shipments (identified structurally as dest_type == customer_region)
# ---------------------------------------------------------------------------
customer_shipments = logistics[logistics["dest_type"] == "customer_region"].merge(
    products[["product_id", "unit_price"]], on="product_id", how="left"
)
customer_shipments["shipment_value"] = customer_shipments["quantity"] * customer_shipments["unit_price"]
revenue_by_customer = customer_shipments.groupby("dest_node_id")["shipment_value"].sum()

# ---------------------------------------------------------------------------
# Build the directed graph
# ---------------------------------------------------------------------------
G = nx.DiGraph()
for _, row in network.iterrows():
    G.add_node(row["origin_node_id"], node_type=row["origin_type"], country=row["origin_country"], name=row["origin_name"])
    G.add_node(row["dest_node_id"], node_type=row["dest_type"], country=row["dest_country"], name=row["dest_name"])
    G.add_edge(
        row["origin_node_id"], row["dest_node_id"],
        lead_time=row["avg_lead_time_days"], cost=row["avg_cost_per_unit"], capacity=row["capacity"],
    )

supplier_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "supplier"]
customer_nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == "customer_region"]
G.add_node(SOURCE)
for s in supplier_nodes:
    G.add_edge(SOURCE, s, lead_time=0, cost=0, capacity=np.inf)

print(f"Graph built: {G.number_of_nodes() - 1} real nodes, {G.number_of_edges() - len(supplier_nodes)} real edges")

baseline_reachable = set(nx.descendants(G, SOURCE))
baseline_customers_reachable = set(customer_nodes) & baseline_reachable
print(f"Baseline: {len(baseline_customers_reachable)} of {len(customer_nodes)} customer regions reachable from the supply base")

# ---------------------------------------------------------------------------
# Centrality metrics (candidate chokepoints)
# ---------------------------------------------------------------------------
real_graph = G.copy()
real_graph.remove_node(SOURCE)
betweenness = nx.betweenness_centrality(real_graph, weight="lead_time")
in_degree = dict(real_graph.in_degree())
out_degree = dict(real_graph.out_degree())

centrality_df = pd.DataFrame({
    "node_id": list(real_graph.nodes()),
    "node_type": [real_graph.nodes[n].get("node_type") for n in real_graph.nodes()],
    "name": [real_graph.nodes[n].get("name") for n in real_graph.nodes()],
    "betweenness_centrality": [betweenness[n] for n in real_graph.nodes()],
    "in_degree": [in_degree[n] for n in real_graph.nodes()],
    "out_degree": [out_degree[n] for n in real_graph.nodes()],
}).sort_values("betweenness_centrality", ascending=False)
centrality_df.to_csv(f"{OUT_DIR}/network_centrality.csv", index=False)

# ---------------------------------------------------------------------------
# Disruption simulation: remove each warehouse/DC one at a time (suppliers
# and customer regions are endpoints - removing them just removes
# themselves, not an interesting routing question)
# ---------------------------------------------------------------------------
candidate_nodes = [n for n, d in real_graph.nodes(data=True) if d.get("node_type") in ("warehouse", "distribution_center")]

# Baseline shortest (fastest) lead time from the supply base to every
# reachable customer region, used to measure SLOWDOWN even when nothing
# is fully cut off.
baseline_lead_time = nx.single_source_dijkstra_path_length(G, SOURCE, weight="lead_time")
baseline_lead_time = {c: baseline_lead_time[c] for c in baseline_customers_reachable}

def simulate_failure(node_id):
    G_temp = G.copy()
    G_temp.remove_node(node_id)
    reachable_after = set(nx.descendants(G_temp, SOURCE))
    customers_after = set(customer_nodes) & reachable_after
    exposed = baseline_customers_reachable - customers_after
    revenue_at_risk = revenue_by_customer.reindex(list(exposed)).fillna(0).sum()

    still_reachable = baseline_customers_reachable & customers_after
    lead_time_after = nx.single_source_dijkstra_path_length(G_temp, SOURCE, weight="lead_time")
    delays = [
        lead_time_after[c] - baseline_lead_time[c]
        for c in still_reachable if c in lead_time_after
    ]
    avg_delay = float(np.mean(delays)) if delays else 0.0
    max_delay = float(np.max(delays)) if delays else 0.0
    n_rerouted = int(sum(1 for d in delays if d > 0.01))

    return exposed, revenue_at_risk, avg_delay, max_delay, n_rerouted

impact_rows = []
for node_id in candidate_nodes:
    exposed, revenue_at_risk, avg_delay, max_delay, n_rerouted = simulate_failure(node_id)
    impact_rows.append({
        "node_id": node_id,
        "node_type": real_graph.nodes[node_id].get("node_type"),
        "name": real_graph.nodes[node_id].get("name"),
        "country": real_graph.nodes[node_id].get("country"),
        "num_customers_exposed": len(exposed),
        "revenue_at_risk": round(revenue_at_risk, 2),
        "is_single_point_of_failure": len(exposed) > 0,
        "num_customers_rerouted": n_rerouted,
        "avg_lead_time_increase_days": round(avg_delay, 2),
        "max_lead_time_increase_days": round(max_delay, 2),
    })

impact_df = pd.DataFrame(impact_rows).sort_values("avg_lead_time_increase_days", ascending=False)
impact_df.to_csv(f"{OUT_DIR}/network_disruption_impact.csv", index=False)

n_spof = impact_df["is_single_point_of_failure"].sum()
print(f"\n{n_spof} of {len(impact_df)} warehouse/DC nodes are single points of failure for at least one customer region")
print("Network has full redundancy for reachability - measuring lead-time slowdown impact instead.")
print("\nTop 10 nodes by lead-time impact if disrupted:")
print(impact_df.head(10).to_string(index=False))

# ---------------------------------------------------------------------------
# Customer redundancy score: how many distinct DCs could serve each
# customer region (a resilience indicator independent of any single failure)
# ---------------------------------------------------------------------------
dc_nodes = [n for n, d in real_graph.nodes(data=True) if d.get("node_type") == "distribution_center"]
redundancy_rows = []
for c in customer_nodes:
    serving_dcs = [dc for dc in dc_nodes if real_graph.has_edge(dc, c)]
    redundancy_rows.append({
        "customer_node_id": c,
        "name": real_graph.nodes[c].get("name"),
        "country": real_graph.nodes[c].get("country"),
        "num_dcs_serving": len(serving_dcs),
        "baseline_lead_time_days": round(baseline_lead_time.get(c, np.nan), 2),
    })
redundancy_df = pd.DataFrame(redundancy_rows).sort_values("num_dcs_serving")
redundancy_df.to_csv(f"{OUT_DIR}/customer_redundancy.csv", index=False)

# ---------------------------------------------------------------------------
# Chart: top 10 nodes by lead-time slowdown impact
# ---------------------------------------------------------------------------
top10 = impact_df.head(10).iloc[::-1]
plt.figure(figsize=(8, 5))
plt.barh(top10["name"], top10["avg_lead_time_increase_days"])
plt.xlabel("Avg. delivery slowdown if this node fails (days)")
plt.title("Top 10 supply chain nodes by disruption impact")
plt.tight_layout()
chart_path = f"{OUT_DIR}/network_disruption_top10.png"
plt.savefig(chart_path, dpi=150)
plt.close()

# ---------------------------------------------------------------------------
# MLflow tracking (for consistency with Days 5-6's tracked runs)
# ---------------------------------------------------------------------------
mlflow.set_experiment("network_resilience")
with mlflow.start_run(run_name="day7_disruption_simulation"):
    mlflow.log_param("n_nodes", real_graph.number_of_nodes())
    mlflow.log_param("n_edges", real_graph.number_of_edges())
    mlflow.log_param("n_candidate_nodes_tested", len(candidate_nodes))
    mlflow.log_metric("n_single_points_of_failure", int(n_spof))
    mlflow.log_metric("max_lead_time_increase_days", float(impact_df["avg_lead_time_increase_days"].max()))
    mlflow.log_metric("total_baseline_customer_revenue", float(revenue_by_customer.sum()))
    mlflow.log_metric("min_dcs_serving_any_customer", int(redundancy_df["num_dcs_serving"].min()))

    mlflow.log_artifact(f"{OUT_DIR}/network_disruption_impact.csv")
    mlflow.log_artifact(f"{OUT_DIR}/network_centrality.csv")
    mlflow.log_artifact(f"{OUT_DIR}/customer_redundancy.csv")
    mlflow.log_artifact(chart_path)

print("\nRun 'mlflow ui' from this folder's parent to view the tracked run.")