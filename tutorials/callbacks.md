# Typed simulation callbacks

Pyforia callbacks are ordered objects with two optional intervention phases.
`on_after_demand` runs after receipts, backlog clearance, and demand, but before
that period's order decision. `on_after_prediction` runs only when the policy
actually predicts, before ordering constraints. A callback cannot create an
extra review opportunity.

The complete runnable versions of the built-in and custom examples are in
[`callback_examples.py`](callback_examples.py) and are executed by the unit
suite.

## Configuration-only changes

The built-ins accept a schedule keyed by `unique_id` and `period`, `date`, or
both. Every effect requires nonblank `reason` and `source` provenance.

```python
import pandas as pd

from pyforia import ScheduledOrderMultiplier, SimulationEngine

promotion = ScheduledOrderMultiplier(pd.DataFrame({
    "unique_id": ["A"],
    "date": [pd.Timestamp("2025-02-10")],
    "multiplier": [2.0],
    "reason": ["planned promotion"],
    "source": ["approved promotion calendar v3"],
}))

result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=n_periods,
    period_frequency="D",
    initial_decision="none",
    warmup_periods=0,
    scoring_periods=n_periods,
    settlement_periods=0,
    order_during_settlement=False,
    demand_source_name="promotion_scenario",
    random_seed=None,
    callbacks=[promotion],
)
```

`ScheduledOrderOverride` sets an absolute quantity,
`ScheduledOrderMultiplier` multiplies the current prediction,
`ScheduledOrderHold` sets it to zero, and
`ScheduledInventoryAdjustment` applies signed on-hand units after demand.
Callbacks compose in list order.

## Stateful callbacks and reset

Pyforia reuses the exact callback objects supplied by the caller. Immediately
before a validated run begins, it calls `reset(context)` once on each callback
so the object can clear run-local counters, cached values, or history from a
previous run. This follows the stateful callback pattern used by Keras and
keeps post-run callback state inspectable by the caller.

Reset is deliberately the last preflight lifecycle action. If an unrelated
input such as the demand calendar or policy schedule is invalid, the run fails
without resetting the caller's callback. Reset does not change simulation
phase ordering; it happens before the optional opening decision and all demand
periods.

## One order decision per opportunity

In 0.1.0 the engine requests and places one composed `OrderDecision` at each
enabled decision opportunity. Order callbacks can adjust that decision in
declared callback order, but cannot create separate supplier-specific orders.
Multi-supplier ordering needs its own typed supplier, lead-time, constraint,
cost, delivery, and audit contract and is deferred from this first version.

## A custom calendar rule

Subclass `SimulationCallback` when a schedule is too narrow. DataFrames in the
context and decision are defensive copies. Any pandas or NumPy calculation is
allowed, but only a typed returned result is applied.

```python
import pandas as pd

from pyforia import (
    InventoryAdjustmentResult,
    SimulationCallback,
)


class SecondWeekLoss(SimulationCallback):
    def __init__(self, fraction=0.10):
        self.fraction = float(fraction)

    def on_after_demand(self, context):
        if not 8 <= context.date.day <= 14:
            return None
        state = context.inventory
        return InventoryAdjustmentResult(pd.DataFrame({
            "unique_id": state[context.sku_column],
            "quantity_delta": -self.fraction * state["on_hand"],
            "reason": "second-week handling loss",
            "source": "warehouse loss model v1",
        }))

    def get_config(self):
        return {"fraction": self.fraction}


loss = SecondWeekLoss(0.10)
result = engine.run(..., callbacks=[loss])
```

Preserve the Python source for custom callbacks: the run manifest records the
module, class, list position, enabled phases, and JSON-serializable
`get_config()` output, but it does not embed source code.

## Physical-adjustment boundary in 0.1.0

Only signed on-hand adjustments are supported. Positive values add stock and
negative values remove it. Removal beyond on-hand and additions while backlog
is positive are errors. Backlog, pipeline, demand, identifiers, and timing
cannot be edited. Request another adjustment target through the future public
issue tracker rather than assuming that a DataFrame edit changed the run.

For `ShelfLifeEngine`, additions require `received_date`, cannot be already
expired, and become dated lots. Removals consume oldest lots first. A future
release may add distinctly named phases, but `on_after_demand` will continue to
mean after demand and before ordering.

Inspect `result.to_callback_audit_frame()` for one row per accepted effect and
`result.to_event_frame()` for aggregate accounting quantities. Callback errors
abort the run; no partial `SimulationResult` is returned.
