# 30 — Execution flow

## 30.1 Inputs to a run

`SimulationEngine.run` combines:

- a ready `InventoryStateDataFrame`;
- one fitted policy, with an optional decision-date policy schedule;
- a complete demand frame or one callable source;
- total, warmup, scoring, and settlement period counts;
- optional ordering constraints;
- optional initial order decision;
- optional ordered typed callbacks;
- engine-owned shelf-life behavior.

It returns a `SimulationResult` containing state history, final inventory,
canonical event rows, run settings, and a provenance manifest.

## 30.2 Preflight: fail before mutation

Before period execution, the engine checks:

1. the total period count and that warmup + scoring + settlement equals it;
2. that the scoring window contains at least one period;
3. demand source name, seed, type, and generation settings;
4. that the policy is fitted and compatible with state settings;
5. state readiness, frequency, backorder mode, and initial-decision timing;
6. validate callback objects and JSON-serializable configuration without
   resetting caller state;
7. forecast origin/frequency and scheduled-policy decision dates;
8. the complete demand grid after materializing a callable once;
9. reset copied constraint state and create private deep copies of mutable run
   inputs;
10. reset the exact caller-supplied callback instances only after the other
    non-mutating preflight checks succeed and immediately before simulation
    execution.

Policy schedules are strict. A scheduled policy must be the same class and have
the same lead time, review period, service level, and shortage mode as the base
policy. Only its fitted target data may differ. Its forecast origin must equal
the decision date and use the simulation frequency.

## 30.3 Optional opening decision

An explicit initial decision can be applied before the first demand period. It
is recorded as its own event. It is never inferred from a normal review cycle.
If schedules are used, the opening decision is only legal at the explicitly
supported opening coordinate.

## 30.4 Exact period sequence

For each demand period the engine performs:

```text
copy opening state
  -> private engine-owned pre-demand phase (including expiry)
  -> state.process_demand
       (advance clock, receive, clear backlog, fulfill current demand)
  -> private engine-owned post-demand phase
  -> ordered on_after_demand callbacks with typed on-hand results
  -> if review period: choose scheduled policy
       -> policy.predict
       -> ordered on_after_prediction callbacks with typed order results
       -> apply ordered constraint chain
       -> schedule accepted quantity in pipeline
  -> build normalized one-row-per-SKU event
  -> attach callback and constraint audit
  -> validate flow balances
  -> append event
```

`on_after_demand` means after demand and before ordering in 0.1.0. It runs once
per warmup, scoring, and settlement demand period, but not at the separate
opening-order coordinate. `on_after_prediction` runs only after an actual
prediction, including an enabled opening decision. Callbacks cannot create a
new review opportunity. All former subclass hooks receiving live state are
absent; once an event is validated it is accounting evidence.

## 30.5 Review and order path

On a decision period:

1. the selected fitted policy reads inventory position;
2. it returns a raw requested `OrderDecision` without changing state;
3. ordered callbacks propose validated absolute order quantities;
4. `_tracked_inventory_update` passes the callback-adjusted request through constraints in their
   declared order;
5. raw, callback-adjusted, constrained, and final quantities are captured;
6. event/line/order-size counters are updated;
7. the accepted order is placed in the lead-time pipeline;
8. diagnostics such as target and safety stock flow into state and events.

The engine performs this path once per enabled decision opportunity. The
current callback result adjusts one composed decision; it does not add a second
supplier-specific order. Multiple applications can still accumulate through
the lower-level inventory operation, but a typed multi-supplier engine surface
is outside 0.1.0.

`order_event_count` represents a system-level event and is stored once on the
first SKU event row so that summing rows remains correct. SKU order-line counts
remain attached to their respective SKUs.

## 30.6 Window semantics

Each period receives exactly one of `warmup`, `scoring`, or `settlement`. The
engine records the window on event rows. `SimulationResult.summary` and normal
result-based evaluation use different interfaces: `summary()` always uses
`scoring`, while `InventoryEvaluator.fit(simulation_result=...)` requires an
explicit window. Warmup advances state before scoring. Settlement observes tail
effects without entering scoring and disables new review decisions by default
unless `order_during_settlement=True`.

## 30.7 Comparison execution

`run_comparison` validates scenario labels first, then materializes the demand
source once. Each scenario receives deep-copied inventory, policy, and
constraints but the same realized demand path. The result manifest marks the
comparison context. This supports paired scenario analysis without accidental
demand resampling or shared mutable stock.
The exact callback instances are reset after branch preflight and before each
branch executes; authoritative branch-specific effects remain in each result's
callback audit.

## 30.8 Output provenance

The run manifest records, where available:

- run UUID and timestamp;
- demand source/type, row count, content fingerprint, seed, and generation
  provenance;
- policy configuration, forecast-target metadata, and target fingerprint;
- opening inventory fingerprint;
- windows, frequency, shortage mode, and other run settings;
- dependency versions;
- package version, source commit, and dirty state.

The current legacy source-commit helper reads a `.git` checkout directly. Installed
wheels do not normally contain `.git`, so commit/dirty provenance can become
`None`. A future package must decide how build-time provenance is embedded; it
must not claim that the current mechanism solves wheel provenance.

## 30.9 State ownership and reproducibility

The caller's original state and fitted policy are not the run's working objects.
Deep copying isolates scenarios and permits post-run inspection. Reproducibility
still requires callers to persist inputs, explicit demand-generation settings,
target data, and package provenance. A seed alone is not a complete run record.
