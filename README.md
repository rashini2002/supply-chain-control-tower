# Global Supply Chain Control Tower

End-to-end data engineering and analytics platform covering procurement
risk, inventory, logistics, and network resilience for a global supply
chain — built as a portfolio project to demonstrate a real DE pipeline
behind a BI dashboard, not just the dashboard itself.

## What this project does

Simulates a global supply chain (suppliers → warehouses → distribution centers → customer regions) and builds:

A demand forecasting model driving dynamic reorder points
A supplier risk scoring model (XGBoost + SHAP), tracked in MLflow
A network graph (NetworkX) with disruption simulation
A shipment routing optimization model (PuLP)
A 5-page Tableau control tower dashboard


## Tech stack

Python, pandas, SDV, Prophet/XGBoost, SHAP, MLflow, NetworkX, PuLP, Tableau

## Repo structure

```
.
├── docs/
│   ├── schema.md          # data model / ERD reference
│   └── DECISIONS.md       # running log of technical decisions
├── scripts/
│   ├── generate_day2_data.py   # suppliers, products, POs, invoices, risk signals
│   └── generate_day3_data.py   # warehouses/DCs/customers, shipments, network edges, inventory
├── data/raw/              # generated synthetic CSVs (see docs/schema.md)
├── dbt/                   # dbt project (staging/marts) - Week 2
├── airflow/dags/          # orchestration DAGs - Week 2
├── models/                # forecasting + risk model code - Week 3
└── notebooks/             # exploration / model dev notebooks
```

## Data model

See [docs/schema.md](docs/schema.md) for the full entity list and column
definitions. Core design choice: suppliers, warehouses, distribution
centers, and customer regions are unified into a single `nodes` table
(distinguished by `node_type`), which is what makes the network graph and
geospatial mapping work cleanly.

## Setup

```bash
git clone https://github.com/<your-username>/supply-chain-control-tower.git
cd supply-chain-control-tower
pip install sdv faker pandas numpy mlflow
```

## Generating the synthetic data


```bash
cd scripts
python generate_day2_data.py   # writes suppliers, products, POs, invoices, risk signals
python generate_day3_data.py   # writes nodes.csv, shipments, network edges, inventory
```

Outputs land in `data/`. Move/rename into `data/raw/` as needed.

**Note:** country risk index and currency volatility values in
`generate_day2_data.py` are illustrative placeholders — see
[docs/DECISIONS.md](docs/DECISIONS.md) for details on what to swap in
before treating this as a finished dataset.

## Current status

- [x] Day 1 — schema design
- [x] Day 2 — supplier, PO, invoice, risk signal data
- [x] Day 3 — warehouse/DC/customer nodes, shipments, network edges, inventory
- [ ] Week 3 — forecasting + supplier risk models (MLflow)
- [ ] Week 4 — network graph + routing optimization (NetworkX + PuLP)
- [ ] Week 5 — GitHub Actions CI + Tableau dashboards
- [ ] Week 6 — documentation + launch
