# 40 — Policies and forecast targets

## 40.1 Shared policy contract

Every policy derives from `BasePolicy` and declares:

- integer `lead_time >= 1`;
- integer `review_period >= 1`;
- optional service level strictly between zero and one;
- an explicit boolean backorder mode.

A policy must be fitted before simulation. `fit` creates and validates target
state; `predict` receives live inventory and returns an `OrderDecision`. The
engine controls review timing and order mutation.

## 40.2 Inventory position

All replenishment rules use:

```text
inventory_position = on_hand + sum(in_transit) - backorders
```

This is different from current physical stock. An order changes inventory
position through the pipeline immediately but changes on-hand only when it is
received.

## 40.3 Target metadata shared across policies

Target validation requires the forecast target to be traceable to:

- an exact SKU;
- forecast origin;
- forward frequency;
- target horizon;
- target end date;
- target probability matching the policy service level;
- an accepted source/aggregation mode.

Quantile-looking labels such as `q95` are parsed and checked against the stated
probability. Direct cumulative targets must declare source
`external_direct`. Target end date must equal origin plus horizon times the
declared offset. One finite nonnegative target is required per SKU.

Marginal quantile summation is rejected. In general,
`sum(Q_p(D_t))` is not the `p` quantile of cumulative demand, so that shortcut
must not reappear under a renamed column.

## 40.4 Order-up-to `(R,S)`

`OrderUpToPolicy` reviews every `R` periods and protects a horizon:

```text
H = lead_time + review_period
requested_order = max(0, S - inventory_position)
```

Exactly one fitting mode is accepted:

### Direct cumulative target

The caller supplies one target `S` per SKU for horizon `H`, with explicit
probability, `external_direct` source, origin, frequency, and end date. An
`aggregation_method` is not accepted because the target is asserted to be
directly calculated outside the policy.

### Independent-normal moments

The caller supplies consecutive per-step mean and standard deviation rows for
forecast horizons `1..H`. Dates must match every horizon step. The policy uses
the explicit independence assumption:

```text
S = sum(step_means) + z(service_level) * sqrt(sum(step_std_deviation^2))
```

This is the only built-in distributional aggregation. It must be labeled as an
independence assumption and is not a general uncertainty model.

## 40.5 Continuous review `(s,Q)` and `(s,S)`

`ContinuousReviewPolicy` is checked once per discrete period
(`review_period = 1`); “continuous” is the traditional policy name, not
continuous physical time.

For `(s,Q)`:

```text
if inventory_position <= s: requested_order = Q
else:                        requested_order = 0
```

- reorder point `s` protects lead-time horizon `L`;
- `Q` is explicit, positive, and has an explicit source;
- the policy never derives `Q` from demand implicitly.

For `(s,S)`:

```text
if inventory_position <= s: requested_order = max(0, S - inventory_position)
else:                        requested_order = 0
```

- `s` protects horizon `L`;
- `S` protects `L + review_period_for_S`;
- the extra review period for `S` is explicit;
- `S >= s` is required for every SKU.

Both targets are external direct cumulative targets with the same strict
probability, origin, frequency, horizon, and date checks described above.

## 40.6 Periodic review `(R,s,S)`

`PeriodicReviewPolicy` is eligible every `R` periods:

```text
if inventory_position <= s: requested_order = max(0, S - inventory_position)
else:                        requested_order = 0
```

Targets come from a `PeriodicReviewTargetProvider`. Built-in providers are:

- `ColumnPeriodicReviewTargets`: selects caller-named `s` and `S` columns from
  an external table;
- `FixedPeriodicReviewTargets`: uses explicit scalars or exact per-SKU maps.

Custom providers are allowed, but their output is centrally validated: exact
SKU coverage, one row per SKU, finite values, `s >= 0`, and `S >= s`. Provider
metadata and manifest data must be JSON-serializable so run provenance does not
depend on an opaque Python object.

## 40.7 Policy schedule

A run can provide refitted policies keyed to decision dates. This models target
updates without mutating one fitted policy during the run. Scheduled policies
must preserve the policy family and operational configuration; only fitted
target content may change. The schedule must cover valid decision periods and
each policy forecast origin must equal its decision date.

## 40.8 Safe extension checklist

Before adding another policy or uncertainty model, decide and test:

1. the precise protection horizon;
2. whether its target is a cumulative distribution, sample path, joint draws,
   or an approximation with named assumptions;
3. origin/frequency/end-date alignment;
4. service-level semantics and whether one probability is sufficient;
5. the exact trigger and order-quantity equation;
6. required target provenance and a stable fingerprint;
7. behavior under backlog and lost-sales modes;
8. event diagnostics needed for later audit.

Do not treat a convenient table shape as enough to establish those semantics.
