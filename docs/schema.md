# Schema — Global Supply Chain Control Tower

Core design decision: suppliers, warehouses, distribution centers, and customer
regions are **not** separate tables. They are unified into one `nodes` table
distinguished by `node_type`. This is what allows the network graph (Week 4)
and the geospatial map to work without cross-entity joins — every physical
point in the supply chain is just a node with coordinates.

## nodes

| Column | Type | Notes |
|---|---|---|
| node_id | string (PK) | |
| node_type | string | one of: `supplier`, `warehouse`, `distribution_center`, `customer_region` |
| name | string | |
| country | string | |
| region | string | e.g. continent or trade bloc |
| lat | float | |
| lon | float | |
| onboarding_date | date | suppliers only, nullable otherwise |
| esg_score | float | suppliers only, nullable otherwise |

Suggested scale: ~40 suppliers, ~10 warehouses, ~6 distribution centers,
~30 customer regions.

## products

| Column | Type | Notes |
|---|---|---|
| product_id | string (PK) | |
| name | string | |
| category | string | |
| unit_cost | float | |
| unit_price | float | |

Suggested scale: ~150 products across 8–10 categories.

## purchase_orders

| Column | Type | Notes |
|---|---|---|
| po_id | string (PK) | |
| supplier_node_id | string (FK → nodes.node_id) | node_type must be `supplier` |
| product_id | string (FK → products.product_id) | |
| order_date | date | |
| quantity | int | |
| unit_price | float | |
| currency | string | |
| total_value | float | |
| status | string | e.g. `open`, `fulfilled`, `cancelled` |

## invoices

| Column | Type | Notes |
|---|---|---|
| invoice_id | string (PK) | |
| po_id | string (FK → purchase_orders.po_id) | |
| invoice_date | date | |
| amount | float | |
| payment_status | string | e.g. `paid`, `pending`, `overdue` |
| payment_date | date | nullable |

## inventory_snapshots

| Column | Type | Notes |
|---|---|---|
| snapshot_id | string (PK) | |
| warehouse_node_id | string (FK → nodes.node_id) | node_type must be `warehouse` |
| product_id | string (FK → products.product_id) | |
| snapshot_date | date | |
| stock_qty | int | |
| reorder_point | int | placeholder until Week 3 forecasting model replaces it |
| safety_stock | int | |

## shipments

| Column | Type | Notes |
|---|---|---|
| shipment_id | string (PK) | |
| origin_node_id | string (FK → nodes.node_id) | any node_type |
| dest_node_id | string (FK → nodes.node_id) | any node_type |
| product_id | string (FK → products.product_id) | |
| mode | string | `road`, `ocean`, `air`, `rail` |
| ship_date | date | |
| delivery_date | date | |
| planned_transit_days | int | |
| actual_transit_days | int | |
| cost | float | |
| quantity | int | |

## network_edges

| Column | Type | Notes |
|---|---|---|
| edge_id | string (PK) | |
| source_node_id | string (FK → nodes.node_id) | |
| target_node_id | string (FK → nodes.node_id) | |
| mode | string | `road`, `ocean`, `air`, `rail` |
| avg_lead_time_days | float | |
| avg_cost_per_unit | float | |
| capacity | int | used as a constraint in the Week 4 PuLP optimization |

This table is the input to the NetworkX graph — one row per feasible lane
between two nodes.

## supplier_risk_signals

| Column | Type | Notes |
|---|---|---|
| signal_id | string (PK) | |
| supplier_node_id | string (FK → nodes.node_id) | node_type must be `supplier` |
| month | string | `YYYY-MM` |
| country_risk_index | float | from public country risk data |
| currency_volatility | float | from public FX data |
| sanctions_flag | boolean | |

## Relationship summary

- `nodes` → `purchase_orders` (as supplier), `inventory_snapshots` (as warehouse),
  `shipments` (as origin/destination), `network_edges` (as source/target),
  `supplier_risk_signals` (as supplier)
- `products` → `purchase_orders`, `inventory_snapshots`, `shipments`
- `purchase_orders` → `invoices`

## Referential integrity rules to enforce in Great Expectations (Week 2)

- Every `purchase_orders.supplier_node_id` and `supplier_risk_signals.supplier_node_id`
  must reference a node where `node_type = supplier`.
- Every `inventory_snapshots.warehouse_node_id` must reference a node where
  `node_type = warehouse`.
- `shipments.origin_node_id` and `dest_node_id` must never be the same value.
- `network_edges.avg_lead_time_days` and `avg_cost_per_unit` must be positive.