# Events, results, and evaluation

`SimulationResult.to_event_frame()` is the authoritative accounting output of a
run. It contains one row per SKU and ordinary period, plus a separate typed
opening-decision row when an initial decision was requested. Use it for metrics,
plots, and downstream analysis instead of caller-owned state snapshots.

## Durable outputs

| Output | Purpose |
|---|---|
| `to_event_frame(window=...)` | Canonical inventory, demand, pipeline, decision, callback, and constraint evidence. |
| `to_callback_audit_frame()` | Accepted typed callback effects and their reason/source information. |
| `run_manifest` | Reproducibility receipt: demand source, package/dependencies, policy, opening state, and run settings. |
| `summary()` | Compact scoring-window convenience summary. |

The event rows are validated against demand fulfillment, physical-stock,
backlog, pipeline, inventory-position, callback-adjustment, and constraint
identities. See the complete [schema reference](../reference/schemas.md).

## Evaluate a run

`InventoryEvaluator` requires an explicit window and grouping when fitting or
evaluating. It can evaluate built-in functions or a custom `BaseInventoryMetric`.

```python
from pyforia.evaluation import InventoryEvaluator, fill_rate, avg_on_hand

report = InventoryEvaluator().fit(result, window="scoring").evaluate(
    metrics=[fill_rate, avg_on_hand],
    groupby=[],
)
```

Cost metrics are intentionally strict: specify active `cost_components` and
every corresponding rate in the evaluation context or event frame. See
[evaluation and metrics](../reference/evaluation.md) for units and denominators.
