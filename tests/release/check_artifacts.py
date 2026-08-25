"""Reject non-package material from built Pyforia artifacts."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path


def members(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    with tarfile.open(path) as archive:
        return {member.name for member in archive.getmembers()}


def main(dist_dir: Path) -> None:
    artifacts = sorted(dist_dir.glob("pyforia-*") )
    if len(artifacts) != 2:
        raise AssertionError(f"expected one wheel and one sdist, found: {artifacts}")

    forbidden = ("/data/", "/arxiv/", "/docs/", "/examples/", "/.venv/", "__pycache__")
    for artifact in artifacts:
        names = members(artifact)
        metadata_name = "METADATA" if artifact.suffix == ".whl" else "PKG-INFO"
        if not any(name.endswith(metadata_name) for name in names):
            raise AssertionError(f"{artifact.name} lacks distribution metadata")
        if not any(name.endswith("LICENSE") for name in names):
            raise AssertionError(f"{artifact.name} lacks the license")
        leaked = sorted(name for name in names if any(token in f"/{name}" for token in forbidden))
        if leaked:
            raise AssertionError(f"{artifact.name} contains excluded material: {leaked}")
        print(f"checked {artifact.name}: {len(names)} members")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
