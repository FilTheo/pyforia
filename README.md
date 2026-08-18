# Pyforia

Pyforia is the planned public successor to the existing PyState
inventory-simulation work. The reusable staging implementation now uses the
`pyforia` namespace. This is not yet an installable distribution.

## Current state

- The product name is **Pyforia**.
- The selected license for Pyforia and the fresh active work is **Apache
  License 2.0**. Archived material is not automatically relicensed.
- The future repository URL is intentionally undecided.
- The active source tree is `src/pyforia/`, with no `pystate` compatibility
  package.
- This workspace is an independent local Git repository on `main` with no
  configured remote.
- There is no Pyforia `pyproject.toml`, installable artifact, installation
  guidance, or frozen public export surface yet.
- Private release records and the code-only pre-migration snapshot are kept
  under ignored private material and must not enter public history.

Do not publish, package, or copy the full archive into a public repository. It
contains historical documentation and reports, including private or
experiment-specific context. Future package work must use a deliberate
allowlist across the main tree as well as the archive.

## Fast orientation

Future agents should start here:

1. [`AGENTS.md`](AGENTS.md) — working rules and source-of-truth order.
2. [`knowledge/00_index.md`](knowledge/00_index.md) — task-routed technical
   context. Read the system overview and only the relevant subsystem page.

[`docs/`](docs/README.md) is reserved for future end-user documentation. It is
not a second technical authority while Pyforia remains unbuilt.

## Evidence baseline

The legacy source baseline came from the inventory repository. Immediately
before the namespace migration, its 108 client-independent cases plus the
local extension regression passed:

```text
109 passed in 9.06s
```

After the mechanical move to `src/pyforia`, the same 109 cases plus a new
retired-namespace boundary test passed locally:

```text
110 passed in 12.54s
```

This proves source-path behavior and namespace migration only. It is not
evidence that Pyforia has been built, installed from an artifact, or released.
