# Getting started

Pyforia connects externally calculated forecast targets to discrete-period
inventory decisions. It does not choose a forecast model, invent uncertainty,
or infer an experimental design.

## 1. Prepare a complete demand calendar

A run requires one row for every SKU and input period. Dates must follow the
declared frequency exactly, and input periods are zero-based.

```python
import pandas as pd

from pyforia import InventoryStateDataFrame, SimulationEngine
from pyforia.policies import OrderUpToPolicy

opening_date = pd.Timestamp("2025-01-01")
skus = ["A", "B"]
n_periods = 30

demand = pd.DataFrame([
    {
        "unique_id": sku,
        "period": period,
        "date": opening_date + pd.Timedelta(days=period + 1),
        "y": realized_demand[(sku, period)],
    }
    for period in range(n_periods)
    for sku in skus
])
```

Missing rows, duplicate SKU-period rows, invalid dates, and negative or
non-finite demand are errors. Pyforia never inserts zero demand for a missing
row.

## 2. Supply opening stock explicitly

Use observed opening stock when it exists:

```python
opening_stock = pd.DataFrame({
    "unique_id": ["A", "B"],
    "observed_on_hand": [18.0, 31.0],
})

inventory = InventoryStateDataFrame(skus, max_lead_time=7)
inventory.initialize_from_observed(
    opening_stock,
    on_hand_column="observed_on_hand",
    start_date=opening_date,
)
```

`initialize_zero()` is available when zero opening stock is the declared
scenario. There are no forecast-fraction or historical mean-plus-z initializer
methods; those heuristics are unsupported. Calculate any non-observed opening
stock outside Pyforia with explicit assumptions and provenance, then pass the
result through the supported observed-state or complete-state paths.

For opening backlog or pipeline, construct the complete state DataFrame
directly and validate it before the run.

## 3. Fit a policy target

For an `(R,S)` policy, `S` covers cumulative demand over `lead_time +
review_period`. The preferred input is one target per SKU calculated outside
Pyforia from joint forecast paths or another documented cumulative-demand
distribution.

```python
decision_date = opening_date
protection_horizon = 9

# One row per SKU. These values were calculated outside Pyforia.
targets = pd.DataFrame({
    "unique_id": ["A", "B"],
    "protection_q95": [94.0, 161.0],
    "protection_end_date": [
        decision_date + pd.Timedelta(days=protection_horizon),
        decision_date + pd.Timedelta(days=protection_horizon),
    ],
})

policy = OrderUpToPolicy(
    lead_time=2,
    review_period=7,
    service_level=0.95,
    allow_backorders=False,
)
policy.fit(
    targets,
    forecast_origin=decision_date,
    forecast_frequency="D",
    target_column="protection_q95",
    target_end_date_column="protection_end_date",
    target_probability=0.95,
    protection_horizon=protection_horizon,
    target_source="external_direct",
)
```

The target probability must equal the policy service level. The horizon and end
date must agree with the origin and frequency. Marginal horizon quantiles cannot
be passed as cumulative targets or summed inside Pyforia.

An independent-normal calculation is available only when its assumptions are
appropriate and every marginal standard deviation is supplied:

```python
marginals = pd.DataFrame([
    {
        "unique_id": sku,
        "fh": fh,
        "date": decision_date + pd.Timedelta(days=fh),
        "mean": forecast_mean[(sku, fh)],
        "std": forecast_std[(sku, fh)],
    }
    for sku in skus
    for fh in range(1, protection_horizon + 1)
])

policy.fit(
    marginals,
    forecast_origin=decision_date,
    forecast_frequency="D",
    forecast_date_column="date",
    mean_column="mean",
    std_column="std",
    target_probability=0.95,
    protection_horizon=protection_horizon,
    aggregation_method="independent_normal",
)
```

This computes `sum(mean) + z * sqrt(sum(std**2))`. Pyforia does not substitute a
standard deviation when `std` is missing.

## 4. Run with explicit timing and experiment windows

```python
result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=30,
    period_frequency="D",
    initial_decision="before_first_demand",
    warmup_periods=5,
    scoring_periods=20,
    settlement_periods=5,
    order_during_settlement=False,
    demand_source_name="experiment_actuals_v1",
    random_seed=None,
)
```

The three window lengths must sum to `n_periods`. Demand still advances state in
warm-up and settlement. `order_during_settlement` controls whether tail review
events can create new pipeline.

`initial_decision="before_first_demand"` records a separate time-zero order
event. `"none"` waits for the first ordinary review event. Lead time zero is
rejected; all accepted orders arrive after at least one period.

```python
scored_events = result.to_event_frame(window="scoring")
all_events = result.to_event_frame(window="all")
summary = result.summary()          # scoring window only
manifest = result.run_manifest      # demand hash, seed, policy, commit, versions
```

Every event records flows directly. The engine asserts physical-stock, backlog,
and pipeline balances per SKU.

## 5. Rolling-origin targets

Fit each snapshot outside the simulator using only information available at its
origin, then map snapshots to inventory decision periods:

```python
result = SimulationEngine().run(
    policy=policy_at_opening,
    policy_schedule={7: policy_at_period_7, 14: policy_at_period_14},
    demand_source=demand,
    inventory=inventory,
    n_periods=21,
    period_frequency="D",
    initial_decision="before_first_demand",
    warmup_periods=0,
    scoring_periods=21,
    settlement_periods=0,
    order_during_settlement=False,
    demand_source_name="rolling_actuals_v1",
    random_seed=None,
)
```

Schedule keys must be actual review events. A snapshot may change fitted targets
but not the policy class, lead time, review period, service level, or backorder
mode. Its declared forecast origin must equal its decision date, and its
forecast frequency must match the simulation frequency.

## 6. Evaluate named grains and explicit costs

```python
from pyforia.evaluation import (
    InventoryEvaluator,
    avg_on_hand,
    cycle_service_level,
    fill_rate,
    stockout_period_rate,
    terminal_backlog_units,
    total_cost,
)

evaluation = InventoryEvaluator().fit(result, window="scoring").evaluate(
    metrics=[
        fill_rate,
        cycle_service_level,
        stockout_period_rate,
        avg_on_hand,
        terminal_backlog_units,
        total_cost,
    ],
    groupby=[],
    context={
        "include_partial_cycles": False,
        "cost_components": ["holding", "shortage", "ordering"],
        "holding_cost_per_unit_period": 0.02,
        "shortage_cost_per_unit": 2.0,
        # Each SKU incurs this fixed cost whenever it has a positive order.
        "order_cost_per_sku_line": 5.0,
        "order_cost_per_unit": 0.0,
    },
)
```

`cycle_service_level` is receipt-to-receipt service and requires an explicit
partial-cycle choice. `stockout_period_rate` is the share of calendar periods
with any stockout. `demand_period_service_level` and
`sku_period_stockout_rate` are the corresponding SKU-period row measures.
Use `backlog_unit_periods` for backlog exposure summed across scored SKU-period
rows, or `terminal_backlog_units` for backlog at the final scored period; there
is no ambiguous `backorder_units_end` metric.

`total_cost` computes only components listed in `cost_components`; every active
rate must be supplied, including deliberate zeros.
Fixed ordering cost is SKU-level: every positive SKU order line incurs
`order_cost_per_sku_line`. Pyforia does not allocate a shared/global order-event
cost across SKUs.

## 7. Optional operational constraints and shelf life

Ordering constraints are independent components applied in the exact sequence
you provide. The standard built-ins cover MOQ, order multiples, maximum order,
and shelf-space units. Shared-unit and shared-volume allocation are deliberately
not standard built-ins in this release.

```python
from pyforia import MaximumOrderQuantity, OrderMultiple, OrderingConstraints

constraints = OrderingConstraints([
    OrderMultiple({"A": 6, "B": 4}, mode="adjust"),
    MaximumOrderQuantity({"A": 60, "B": 40}, mode="adjust"),
])
```

Shelf-life runs require dated opening lots:

```python
from pyforia.core import ShelfLifeEngine

opening_lots = pd.DataFrame({
    "unique_id": ["A", "B"],
    "received_date": [pd.Timestamp("2024-12-31")] * 2,
    "quantity": [18.0, 31.0],
})

shelf_result = ShelfLifeEngine(shelf_life_days=3).run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=30,
    period_frequency="D",
    initial_decision="none",
    warmup_periods=0,
    scoring_periods=30,
    settlement_periods=0,
    order_during_settlement=False,
    demand_source_name="dated_lot_experiment",
    random_seed=None,
    opening_lots=opening_lots,
    opening_expiry_handling="reject",
)
```

Opening lot totals must exactly equal opening on-hand. Shelf life is measured in
calendar days, expiration occurs before demand, and expired units can activate
the explicit `waste` cost component. A zero-quantity receipt is skipped with a
warning. Already-expired opening lots reject by default; the explicit
`expire_before_initial_decision` mode writes them off before policy execution
and records the quantities in the run manifest. Use `preprocessed` only when a
separate workflow has already removed expired lots; the engine verifies that
claim and rejects if any stale opening lot remains.
