# 30 — Execution flow

## 30.1 Inputs to a run

`SimulationEngine.run` combines:

- a ready `InventoryStateDataFrame`;
- one fitted policy, with an optional decision-date policy schedule;
- a complete demand frame or one callable source;
- total, warmup, scoring, and settlement period counts;
- optional ordering constraints;
- optional initial order decision;
- hook behavior supplied by the engine class, including shelf-life behavior.

It returns a `SimulationResult` containing state history, final inventory,
canonical event rows, run settings, and a provenance manifest.

## 30.2 Preflight: fail before mutation

Before period execution, the engine checks:

1. the total period count and that warmup + scoring + settlement equals it;
2. that the scoring window contains at least one period;
3. demand source name, seed, type, and generation settings;
4. that the policy is fitted and compatible with state settings;
5. state readiness, frequency, backorder mode, and initial-decision timing;
6. forecast origin/frequency and scheduled-policy decision dates;
7. the complete demand grid after materializing a callable once;
8. reset constraint state and create private deep copies of mutable run inputs.

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
  -> before_step
  -> before_demand
  -> state.process_demand
       (advance clock, receive, clear backlog, fulfill current demand)
  -> on_stockout
  -> if review period: choose scheduled policy and on_review_period
       -> policy.predict
       -> apply ordered constraint chain
       -> schedule accepted quantity in pipeline
  -> build normalized one-row-per-SKU event
  -> attach constraint audit
  -> after_period_event
  -> validate flow balances
  -> append event
```

Hook placement is part of observable behavior. `before_demand` can alter stock
before the standard transition; `after_period_event` can add audited event
fields before flow validation. There is no post-finalization state-mutation
hook: once the balance checks pass and the event is appended, that period is
accounting evidence. See the resolved legacy-hook finding in
[80.5](80_tests_and_evidence.md#805-resolved-legacy-after_step-hook).

## 30.5 Review and order path

On a decision period:

1. the selected fitted policy reads inventory position;
2. it returns a requested `OrderDecision` without changing state;
3. `_tracked_inventory_update` passes the request through constraints in their
   declared order;
4. requested, adjustment, constrained, and final quantities are captured;
5. event/line/order-size counters are updated;
6. the accepted order is placed in the lead-time pipeline;
7. diagnostics such as target and safety stock flow into state and events.

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
