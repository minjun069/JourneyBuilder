from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from rag.artifacts import import_artifact, inspect_artifact


DEFAULT_ARTIFACT_DIRECTORY = Path(__file__).resolve().parent / "artifacts"
_OFFICIAL_ARTIFACT_PATTERN = "official__*.tar.gz"
_SEMANTIC_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _version_key(version: Any) -> tuple[int, int, int]:
    if not isinstance(version, str):
        raise ValueError("official artifact catalog version must be a string")
    match = _SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"official artifact catalog version is invalid: {version!r}")
    return tuple(int(part) for part in match.groups())


def select_latest_official_artifact(
    artifact_directory: Path = DEFAULT_ARTIFACT_DIRECTORY,
) -> tuple[Path, dict[str, Any]]:
    """Return the highest qualified official artifact after validating all candidates."""

    paths = sorted(artifact_directory.glob(_OFFICIAL_ARTIFACT_PATTERN))
    if not paths:
        raise ValueError(
            f"no official RAG artifact exists in {artifact_directory.resolve()}"
        )

    candidates: list[
        tuple[tuple[int, int, int], Path, dict[str, Any]]
    ] = []
    seen_versions: set[tuple[int, int, int]] = set()
    for path in paths:
        inspected = inspect_artifact(path)
        manifest = inspected["manifest"]
        if manifest.get("artifact_kind") != "official":
            raise ValueError(f"deployment artifact is not official: {path.name}")
        version = manifest.get("catalog", {}).get("version")
        version_key = _version_key(version)
        expected_artifact_id = f"official-catalog-{str(version).replace('.', '-')}"
        if manifest.get("artifact_id") != expected_artifact_id:
            raise ValueError(
                f"official artifact ID does not match catalog version: {path.name}"
            )
        if version_key in seen_versions:
            raise ValueError(f"duplicate official artifact catalog version: {version}")
        seen_versions.add(version_key)
        candidates.append((version_key, path, inspected))

    _, path, inspected = max(candidates, key=lambda candidate: candidate[0])
    return path, inspected


def bootstrap_latest_official_artifact(
    *,
    database_url: str,
    artifact_directory: Path = DEFAULT_ARTIFACT_DIRECTORY,
) -> dict[str, Any]:
    """Idempotently import and activate the newest committed official artifact."""

    path, inspected = select_latest_official_artifact(artifact_directory)
    manifest = inspected["manifest"]
    expected_active = {
        row["logical_collection"]: row["physical_collection"]
        for row in manifest["active_snapshot"]
    }
    result = import_artifact(
        database_url=database_url,
        artifact_path=path,
        activate=True,
    )
    if result.get("active_indexes") != expected_active:
        raise RuntimeError("RAG artifact activation did not produce the expected pointers")
    return {
        "mode": "import-and-activate",
        "artifact": str(path.resolve()),
        "artifact_sha256": inspected["sha256"],
        "artifact_id": manifest["artifact_id"],
        "catalog_version": manifest["catalog"]["version"],
        "active_indexes": result["active_indexes"],
        "inserted_physical_collections": result["inserted_physical_collections"],
        "reused_physical_collections": result["reused_physical_collections"],
    }


def check_latest_official_artifact(
    artifact_directory: Path = DEFAULT_ARTIFACT_DIRECTORY,
) -> dict[str, Any]:
    path, inspected = select_latest_official_artifact(artifact_directory)
    manifest = inspected["manifest"]
    return {
        "mode": "check-only",
        "artifact": str(path.resolve()),
        "artifact_sha256": inspected["sha256"],
        "artifact_id": manifest["artifact_id"],
        "catalog_version": manifest["catalog"]["version"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the newest committed official RAG artifact and, by default, "
            "idempotently import and activate it."
        )
    )
    parser.add_argument(
        "--artifact-directory",
        type=Path,
        default=DEFAULT_ARTIFACT_DIRECTORY,
    )
    parser.add_argument("--check-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check_only:
            result = check_latest_official_artifact(args.artifact_directory)
        else:
            result = bootstrap_latest_official_artifact(
                database_url=os.environ.get("DATABASE_URL", ""),
                artifact_directory=args.artifact_directory,
            )
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
