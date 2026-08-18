# 80 — Tests and evidence

## 80.1 Current executable evidence

The staging unit directory contains 8 client-independent files, 99 top-level
test functions, and 110 pytest cases after parametrization. The 108 copied
legacy cases, the focused hook regression, and the namespace-boundary
regression all pass against `src/pyforia`. This verifies source-path behavior
after the mechanical namespace migration. It is not evidence for an installed
distribution, wheel, source distribution, or frozen public API.

## 80.2 Test-file map

| File | Top-level test functions | Main evidence |
|---|---:|---|
| `test_data_structures.py` | 23 | IDs, initialization, readiness, receipts, backlog/lost sales, demand and order timing |
| `test_demand_generator.py` | 4 | demand source shapes, negative handling, reproducibility/provenance |
| `test_inventory_evaluation.py` | 8 | canonical validation, evaluator grouping/window choices, metrics and costs |
| `test_order_constraints.py` | 11 | built-in rules, adjustment/raise modes, order dependence, audit and reset |
| `test_policy_target_contracts.py` | 18 | direct targets, horizons, probabilities, dates, quantile guardrails, providers |
| `test_shelf_life.py` | 11 | lot balance, FIFO expiry/consumption, opening-lot modes, event integration |
| `test_simulation_contracts.py` | 22 | preflight, demand grid, hooks, events, schedules, comparisons, provenance |
| `test_extension_contracts.py` | 2 | removed callback surface and absence of an active `pystate` package |

The M5 and SPAR adapter-contract files remain in the private source repository
and are intentionally absent from the staging test tree.

Counts describe the current files and can drift after changes; re-run collection
instead of copying these numbers into release claims.

## 80.3 What the passing suite does not prove

- that `pyforia` is currently available on package registries or collision-free
  in every supported environment;
- that metadata, Apache notices, classifiers, dependencies, or Python versions
  are correct;
- that a source distribution and wheel contain only approved files;
- that installed-wheel provenance is populated;
- that public tutorials and API examples work;
- that all hooks preserve the event/state contract;
- that adapter tests can run after SPAR/M5 code is excluded;
- that results match an external benchmark or real operational system;
- production scale or performance, continuous-time or multi-echelon behavior,
  arbitrary forecasting-model compatibility, or automatic target calibration.

## 80.4 Extraction test strategy

When package construction is authorized, create evidence in layers:

1. copy/adapt only approved reusable modules and core tests (completed in the
   staging source tree);
2. rename imports deliberately and add import-surface tests (completed for the
   source tree);
3. separate core contract tests from private adapter tests (completed in the
   staging test tree);
4. test the supported Python/dependency matrix in clean environments;
5. build sdist and wheel;
6. inspect both archives for forbidden legacy/business material;
7. install each artifact into a clean environment and run public smoke examples;
8. verify version, license, and embedded source provenance from the installed
   artifact;
9. run scientific regression fixtures with immutable expected event ledgers.

No package build has been performed because packaging remains a separate owner
decision and release gate.

## 80.5 Resolved legacy `after_step` hook

The copied engine formerly validated and appended a period event before calling
`after_step`. A custom hook could therefore mutate returned inventory after the
canonical event was recorded. A reproduced case produced event
`ending_on_hand = 0.0` while returned final inventory had `on_hand = 100.0`.

The owner decided not to expose that unrestricted hook in Pyforia 0.1.0. The
staging implementation removes both the invocation and method. A focused
staging test asserts that `SimulationEngine` no longer exposes `after_step`.
Before the namespace migration, the focused test plus all 108
client-independent legacy cases passed together (`109 passed` on 2026-08-18).
After migration, those 109 cases plus the new retired-namespace boundary test
pass locally (`110 passed` on 2026-08-18).

A possible ordered callback-object mechanism is private future work, not a
0.1.0 API promise. Any later callback that requests a physical adjustment must
return explicit adjustment evidence for engine-owned application and validation
before the period event is finalized.

## 80.6 Evidence commands

From the repository root, the configured suite can be checked without creating
a pytest cache:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/filtheo/inventory/.venv/bin/python -m pytest \
  -p no:cacheprovider -q -o addopts='' tests/unit
```

Collect-only accounting for the local staging tests:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/filtheo/inventory/.venv/bin/python -m pytest \
  -p no:cacheprovider -q -o addopts='' --collect-only tests/unit
```

Use a clean installed environment later for package evidence; passing against a
source path can hide missing build files and incorrect distribution metadata.

## 80.7 Documentation coverage audit

This knowledge layer is designed to be mechanically scannable:

- [70](70_module_reference.md) names all 22 candidate source modules;
- this page names all 8 staging unit-test files;
- [20](20_data_and_time_contracts.md) records state/order/demand contracts;
- [30](30_execution_flow.md) records the complete run sequence;
- [40](40_policies_and_targets.md) records all policy families and target modes;
- [50](50_constraints_and_shelf_life.md) records all built-in constraints and
  perishability;
- [60](60_events_evaluation_and_outputs.md) records event and evaluation flow.

Coverage means the area is mapped, not that every implementation line has been
restated. Agents should follow the source links before edits.
