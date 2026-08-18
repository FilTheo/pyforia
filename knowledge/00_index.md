# Pyforia knowledge index

Status: source-grounded extraction knowledge, not public API documentation.

This folder explains the reusable inventory engine under
[`src/pyforia`](../src/pyforia).
It is the fastest route for an agent that must understand the staging code
before preparing the future **Pyforia** distribution. The import namespace is
`pyforia` and the first version is `0.1.0`; package metadata and the repository
URL remain undecided.

## 0.1 Minimal reading path

Do not load this whole folder by default. Start with
[10 — System context](10_system_context.md), then open only the page needed for
the task:

| Task | Start here | Then read |
|---|---|---|
| Change state or demand processing | [20](20_data_and_time_contracts.md) | [30](30_execution_flow.md), [80](80_tests_and_evidence.md) |
| Add or change a policy | [40](40_policies_and_targets.md) | [20](20_data_and_time_contracts.md), [30](30_execution_flow.md) |
| Add an operational constraint | [50](50_constraints_and_shelf_life.md) | [30](30_execution_flow.md), [60](60_events_evaluation_and_outputs.md) |
| Add a metric | [60](60_events_evaluation_and_outputs.md) | [80](80_tests_and_evidence.md) |
| Prepare the new package | [90](90_pyforia_extraction_guide.md) | [10](10_system_context.md), [80](80_tests_and_evidence.md) |

[70 — Module reference](70_module_reference.md) is a lookup table, not required
reading. [80 — Tests and evidence](80_tests_and_evidence.md) is for validation
scope and known defects. Read multiple subsystem pages only when a change
crosses their boundaries.

## 0.2 Authority and vocabulary

- Current code and tests are the evidence for current behavior. These notes
  explain that evidence; they do not override it.
- “Legacy” identifies the PyState lineage and private pre-migration evidence,
  not code that is safe to discard or an active import namespace.
- “Confirmed” means stated by the repository owner: future name **Pyforia**,
  `pyforia` import/distribution name, version `0.1.0`, approved source layout,
  Apache-2.0 license, no package build yet, URL open.
- “Candidate” means a proposed extraction boundary that still needs approval.
- “Known defect” means observed behavior that should not be normalized into a
  future contract without an explicit decision.
- The retired import name `pystate` is used only for historical lineage and
  private pre-migration evidence; the active staging namespace is `pyforia`.

## 0.3 Non-negotiable scientific posture

The current staging implementation is intentionally fail-closed. Do not restore or
invent forecast targets, opening stock, uncertainty, lead times, order timing,
or missing demand. In particular, do not use a `0.3 * mean` safety-stock
fallback, add marginal quantiles across periods, silently fill scientific
inputs with zero, or treat absent provenance as acceptable.

## 0.4 Related navigation

- [Repository README](../README.md): concise human-facing status.
- [Agent instructions](../AGENTS.md): working rules.
- [Future public docs](../docs/README.md): intentionally empty until the package
  exists.
- [Archive guide](../arxiv/README.md): historical documentation and reports.
