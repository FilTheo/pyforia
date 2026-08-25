# Compare scenarios fairly

An inventory comparison should vary the declared question while holding the
remaining scenario fixed. `SimulationEngine.run_comparison(...)` materializes a
callable demand source once and isolates copied policies and inventory state for
each branch.

Use the same demand path when comparing forecasts or policies. State which
input changes, keep lead time, costs, and windows explicit, and interpret
results on the same scoring window. Notebook 06 demonstrates both a policy
comparison and a forecast-target comparison without claiming that one selected
method is universally best.

For portfolio metrics, name the aggregation grain. For example, a fill rate is
demand weighted, while a stockout-period rate answers a calendar-period
question. Cost results require their active components and rates to be supplied
explicitly.
