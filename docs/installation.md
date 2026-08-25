# Installation

Pyforia requires Python 3.10 or later. The first public release is `0.1.0`.

## From PyPI

Once the release is available, install the published package:

```bash
pip install pyforia
```

## From GitHub

Until a published package is available, or when you need the current repository
version, install directly from GitHub:

```bash
pip install "git+https://github.com/FilTheo/pyforia.git"
```

For a local checkout, use an editable install while developing:

```bash
pip install -e .
```

Pyforia's base dependencies are NumPy, pandas, and Matplotlib. The base package
does not install a forecasting library. The forecast-integration notebooks use
the separately installed `smooth` package; their setup is explained in the
[forecast integration guide](guides/forecast-integration.md).

## Check the installation

```python
import pyforia

print(pyforia.SimulationEngine)
```

Continue with the [first simulation](tutorials/first-simulation.md).
