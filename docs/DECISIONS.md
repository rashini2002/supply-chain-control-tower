# Technical decisions log

Running log of key technical decisions and trade-offs made during the build.
One entry per work day where a real decision was made — not every day needs
an entry. Add new entries at the bottom, newest last.

Template for new entries:
```
## Day N - <short title>
**Decision:** what was decided
**Why:** the reasoning
**Trade-off / risk:** what this costs or what could break
```

---

## Day 1 - Unified node table instead of separate entity tables
**Decision:** Suppliers, warehouses, distribution centers, and customer
regions are modeled as one `nodes` table with a `node_type` discriminator
column, rather than four separate tables.
**Why:** the Week 4 network graph (NetworkX) and the geospatial map both
need every physical point in the chain to look the same shape — one node ID,
one lat/lon. A unified table avoids UNION-ing four tables every time the
graph is built.
**Trade-off / risk:** type-specific fields (e.g. `esg_score` only applies to
suppliers) sit as nullable columns on a shared table, which is a bit denormalized.
Acceptable for a project at this scale.

## Day 1 - Scoped to 8 core tables, not a fully normalized schema
**Decision:** Kept the schema to 8 tables (nodes, products, purchase_orders,
invoices, inventory_snapshots, shipments, network_edges, supplier_risk_signals)
instead of splitting out separate country/currency/category dimension tables.
**Why:** keeps the dbt staging/mart layer manageable within the 6-week timeline.
**Trade-off / risk:** some repeated string values (country names, categories)
across tables instead of foreign keys to dimension tables — dbt can clean
this up in staging if needed later.

## Day 2 - SDV seed-and-scale instead of fitting on real data
**Decision:** Hand-authored a 300-row purchase order seed encoding the
correlations we want (higher supplier country-risk tier -> wider price
variance and higher cancellation rate), then fit SDV's
GaussianCopulaSynthesizer on that seed and sampled 5,000 rows.
**Why:** there's no real historical procurement dataset to fit on. This is
the standard workaround — SDV learns and scales a designed pattern instead
of raw historical data.
**Trade-off / risk:** the correlation only exists because we hand-designed
it into the seed — it's not evidence of a real-world pattern. Fine for a
portfolio project since the point is demonstrating the pipeline, not making
an empirical claim.

## Day 2 - Placeholder country risk / currency volatility values
**Decision:** Used an illustrative lookup table for country risk index and
currency volatility, loosely modeled on published risk tiers, instead of
live OECD or World Bank data.
**Why:** the build sandbox can't fetch external data files over the network.
**Trade-off / risk:** these numbers are not real published figures. Flagged
to swap in the actual OECD Country Risk Classification or World Bank
Worldwide Governance Indicators (both free CSV downloads) before treating
this as a finished, presentable dataset.

## Day 2 - Invoices derived deterministically from purchase orders
**Decision:** Invoices are generated directly from the purchase_orders table
(skipping cancelled POs) rather than being an independently synthesized table.
**Why:** guarantees referential integrity between POs and invoices with no
extra reconciliation step.
**Trade-off / risk:** none significant — this mirrors how invoicing actually
works in practice (an invoice always originates from an order).

## Day 3 - Modeled three explicit shipment legs, not an any-to-any graph
**Decision:** Shipments are generated only along three legs: supplier→warehouse,
warehouse→distribution_center, distribution_center→customer_region.
**Why:** reflects a realistic supply chain topology instead of an
unconstrained graph where any node could ship to any other node.
**Trade-off / risk:** the network graph in Week 4 will only ever show these
three tiers of connections — fine, since that matches how real supply chains
are structured.

## Day 3 - Mode selection driven by haversine distance, not random
**Decision:** Transportation mode (road/rail/ocean/air) is chosen
probabilistically based on the great-circle distance between origin and
destination, rather than assigned at random.
**Why:** keeps cost and lead-time data internally consistent — long-haul
routes correctly skew toward ocean/air, short-haul toward road/rail.
**Trade-off / risk:** none significant — this only strengthens realism.

## Day 3 - Network edges carry 1.8-2.5x capacity buffer over observed volume
**Decision:** Aggregated network edges (lanes) have a capacity set to
1.8-2.5x the average shipment volume on that lane, not the exact observed volume.
**Why:** the Week 4 PuLP optimization model needs slack in the network to
have real routing decisions to make — a fully saturated network has no
room to reroute anything.
**Trade-off / risk:** the specific multiplier (1.8-2.5x) is an assumption,
not derived from any real capacity planning data — reasonable for this project.

## Day 3 - nodes.csv supersedes nodes_suppliers.csv
**Decision:** Day 3 produces a combined `nodes.csv` (all 86 nodes across all
types); the Day 2 `nodes_suppliers.csv` should be removed from `data/raw/`
once `nodes.csv` is confirmed correct.
**Why:** avoids two divergent node tables being referenced inconsistently
in later dbt staging models.
**Trade-off / risk:** none, as long as the old file is actually removed and
not left to be accidentally joined against.


## Day 4 - Marts built with plain pandas joins + inline checks, not Great Expectations

Decision: Data quality validation for the 4 marts (procurement, inventory, logistics, network) is done with small inline Python check functions (null checks, FK checks, uniqueness, positive-value checks) that print a pass/fail log and write a validation_report.csv, instead of a dedicated data quality framework. Why: consistent with dropping the cloud DE stack — a lightweight, dependency-free approach fits a pure Python/ML/DA scope better. Trade-off / risk: less standardized and less reusable across projects than Great Expectations would be, and the checks are hand-written rather than declaratively configured.


## Day 4 - Supplier risk signals matched to POs by exact order month

Decision: Each purchase order is joined to its supplier's risk signal for the same calendar month (order_date's YYYY-MM), rather than the nearest available month. Why: simplest correct join given risk signals are already monthly; avoids silently attaching a risk score from the wrong period. Trade-off / risk: ~4% of orders fall outside the 24-month risk signal window and get a null risk score for that row — expected and acceptable, but worth remembering when building the supplier risk model in Day 6 (drop or impute these rows rather than treating the null as a data bug).


## Day 4 - Fixed a column-name mismatch in the network mart join

Decision: mart_network joins on origin_node_id/dest_node_id, not source_node_id/target_node_id. Why: the Day 3 script's actual network_edges.csv output uses origin/dest naming (from the shipments groupby), which didn't match the source/target naming used in docs/schema.md. Caught by testing the script end-to-end before delivery rather than assuming the schema doc was accurate. Trade-off / risk: docs/schema.md's network_edges column names are now slightly out of date (says source/target) and should be corrected to origin/dest to match the real data.