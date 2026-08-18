# 20 — Data and time contracts

## 20.1 Inventory state schema

`InventoryStateDataFrame` is the authoritative live-state object. It keeps one
row per SKU and uses `unique_id` by default as the identifier column.

| Field | Meaning |
|---|---|
| `on_hand` | usable physical units after the latest transition |
| `backorders` | unmet demand carried forward in backorder mode |
| `in_transit` | per-SKU array; index `0` is the next receipt slot |
| `period`, `date` | current discrete-period coordinate and timestamp |
| `is_review_period` | whether the current period permits a policy decision |
| `target_level`, `safety_stock` | policy diagnostics copied into state/events |
| `latest_order` | order quantity placed at the current decision |
| `latest_received` | pipeline quantity arriving this period |
| `latest_incoming_demand` | demand presented this period |
| `latest_fulfilled` | current demand served from stock |
| `latest_shortage` | current demand not served immediately |
| `latest_backorders_fulfilled` | old backlog cleared by receipts |

The constructor may default transient “latest” flow columns to zero. It does
not infer the scientific opening state. Before simulation, readiness validation
requires one common integer period, one common date, valid pipeline arrays of
the configured maximum lead time, finite nonnegative quantities, and mode
consistency.

## 20.2 Identifiers

Identifiers are deliberately not coerced. All IDs in one object must be
hashable, nonblank, unique, and of one exact Python type. Joins require exact
SKU-set agreement where completeness is scientifically material. This prevents
silent collisions such as integer `1` and string `"1"`, or a missing SKU being
mistaken for zero demand.

## 20.3 Explicit initialization

There are two accepted starting patterns:

- `initialize_zero(...)` declares an intentionally empty system: zero on-hand,
  zero backlog, and an empty pipeline.
- `initialize_from_observed(...)` requires an exact SKU set, complete finite
  nonnegative observed on-hand values, and an explicit date. It starts backlog
  and pipeline at zero.

`initialize_from_forecast(...)` and `initialize_from_historical_data(...)` are
rejection stubs. Earlier heuristic initialization was removed; callers must not
derive an undocumented opening stock level from forecasts or history.

## 20.4 Time model

Simulation uses integer periods plus a pandas-compatible forward frequency.
For a run starting from state date `D0`, demand period `0` occurs at
`D0 + 1 * frequency`, period `1` at `D0 + 2 * frequency`, and so on. Demand
must cover every SKU for every requested period with the exact expected dates.

State period advances before review timing is evaluated. A period is a review
period when the new state period is divisible by the policy review period. The
engine, not a policy, decides whether `predict` is called.

Lead time `L` is at least one. An order placed at decision period `t` has
expected delivery period `t + L` and is stored at pipeline index `L - 1` after
the current period's receipt has been removed and the pipeline shifted.

## 20.5 Demand input

The engine accepts either a complete demand DataFrame or a callable demand
source. A callable is materialized exactly once before mutation begins. The
preflight contract requires:

- periods exactly `0..n_periods-1`;
- one row for every SKU-period pair;
- exact calendar dates;
- finite nonnegative demand;
- no duplicate SKU-period observations;
- explicit source name and generation/provenance fields where applicable.

`DemandGenerator` supports constant, normal, seasonal, trend, and historical
normal-moment generation in batch or callable form. Scalar settings may apply
to all SKUs; mappings must cover the exact SKU set. Negative generated demand
is either rejected or clipped to zero according to the explicit
`negative_demand_handling` mode. The implementation default is rejection; do
not rely on an older docstring suggesting unconditional clipping.

## 20.6 One demand transition

`InventoryStateDataFrame.process_demand` performs this ordered transition:

1. validate IDs, values, date, and frequency;
2. clone state, reset `latest_order`, and increment `period`;
3. set the new date and review-period flag;
4. receive pipeline slot `0` and shift the remaining pipeline left;
5. in backorder mode, use receipts to clear old backlog first;
6. add any remaining receipt to on-hand;
7. align the complete demand vector to state rows;
8. fulfill `min(demand, on_hand)` and reduce on-hand;
9. record shortage;
10. add shortage to backlog, or classify it as lost sales through the event
    semantics when backorders are disabled;
11. create and validate the new state snapshot.

This ordering means receipts can satisfy earlier backlog before the current
period's demand. The event ledger separately records those two fulfillment
flows.

## 20.7 Order decision contract

`OrderDecision` contains one row per explicitly returned SKU and requires a
finite nonnegative `order_quantity`. It carries the policy's `lead_time` and
`review_period`, plus timing and diagnostic columns such as order period,
expected delivery, target, and safety stock.

When an order is applied:

- unknown SKUs are rejected;
- omitted known SKUs are normalized to zero orders;
- every positive order must have order period equal to the current state period;
- expected delivery must equal order period plus lead time;
- the state pipeline must be long enough for that lead time;
- quantity is added to pipeline index `L - 1`;
- `latest_order` accumulates, allowing multiple supplier/order events in one
  decision period;
- on-hand stock does not change.

## 20.8 Balance equations

For each canonical SKU-period row:

```text
demand = fulfilled_current_demand + shortage

ending_on_hand
  = starting_on_hand
  + received
  - old_backorders_fulfilled
  - current_demand_fulfilled
  - expired
  + explicit_inventory_adjustment

ending_backorders
  = starting_backorders
  + new_backorder_increment
  - old_backorders_fulfilled

ending_pipeline
  = starting_pipeline - receipts + orders_placed

ending_inventory_position
  = ending_on_hand + ending_pipeline - ending_backorders
```

These are executable validation rules, not explanatory approximations.
