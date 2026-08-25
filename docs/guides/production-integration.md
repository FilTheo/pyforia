# Production integration patterns

Pyforia can sit after a forecasting step and before an external approval or
ordering step. It returns decisions, events, metrics, and run metadata that a
calling workflow can store or review.

```text
scheduled data/forecast job
    -> validate forecast-derived target
    -> Pyforia decision and simulation
    -> review/export event and metric outputs
    -> external approval or order execution
```

Notebook 10 shows one daily-close batch pattern. It is illustrative, not a
required production architecture. A deployment may use another scheduler,
database, approval mechanism, forecasting stack, or monitoring approach. The
durable boundary is Pyforia's documented public API and outputs, not the
notebook's surrounding orchestration.
