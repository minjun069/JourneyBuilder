from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag.bootstrap import (
    bootstrap_latest_official_artifact,
    check_latest_official_artifact,
    select_latest_official_artifact,
)


def _inspection(path: Path, version: str) -> dict:
    suffix = version.replace(".", "-")
    return {
        "artifact": str(path.resolve()),
        "sha256": f"sha-{suffix}",
        "manifest": {
            "artifact_id": f"official-catalog-{suffix}",
            "artifact_kind": "official",
            "catalog": {"version": version},
            "active_snapshot": [
                {
                    "logical_collection": "financial-knowledge",
                    "physical_collection": (
                        f"financial-knowledge__official-catalog-{suffix}"
                    ),
                },
                {
                    "logical_collection": "interface-guides",
                    "physical_collection": (
                        f"interface-guides__official-catalog-{suffix}"
                    ),
                },
            ],
        },
    }


class RAGBootstrapTests(unittest.TestCase):
    def test_selects_highest_validated_catalog_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "official__official-catalog-2-1-0.tar.gz"
            newer = root / "official__official-catalog-2-2-0.tar.gz"
            older.touch()
            newer.touch()

            def inspect(path: Path) -> dict:
                version = "2.2.0" if path == newer else "2.1.0"
                return _inspection(path, version)

            with patch("rag.bootstrap.inspect_artifact", side_effect=inspect):
                selected, inspected = select_latest_official_artifact(root)

        self.assertEqual(selected, newer)
        self.assertEqual(inspected["manifest"]["catalog"]["version"], "2.2.0")

    def test_check_only_never_connects_to_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "official__official-catalog-2-1-0.tar.gz"
            artifact.touch()
            with (
                patch(
                    "rag.bootstrap.inspect_artifact",
                    return_value=_inspection(artifact, "2.1.0"),
                ),
                patch("rag.bootstrap.import_artifact") as importer,
            ):
                result = check_latest_official_artifact(root)

        importer.assert_not_called()
        self.assertEqual(result["mode"], "check-only")
        self.assertEqual(result["catalog_version"], "2.1.0")

    def test_bootstrap_imports_with_activation_and_verifies_both_pointers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "official__official-catalog-2-2-0.tar.gz"
            artifact.touch()
            inspected = _inspection(artifact, "2.2.0")
            expected_active = {
                row["logical_collection"]: row["physical_collection"]
                for row in inspected["manifest"]["active_snapshot"]
            }
            import_result = {
                "active_indexes": expected_active,
                "inserted_physical_collections": list(expected_active.values()),
                "reused_physical_collections": [],
            }
            with (
                patch("rag.bootstrap.inspect_artifact", return_value=inspected),
                patch(
                    "rag.bootstrap.import_artifact",
                    return_value=import_result,
                ) as importer,
            ):
                result = bootstrap_latest_official_artifact(
                    database_url="postgresql://runtime:test@postgres/runtime",
                    artifact_directory=root,
                )

        importer.assert_called_once_with(
            database_url="postgresql://runtime:test@postgres/runtime",
            artifact_path=artifact,
            activate=True,
        )
        self.assertEqual(result["active_indexes"], expected_active)

    def test_missing_or_duplicate_versions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no official RAG artifact"):
                select_latest_official_artifact(Path(directory))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "official__first.tar.gz"
            second = root / "official__second.tar.gz"
            first.touch()
            second.touch()
            with patch(
                "rag.bootstrap.inspect_artifact",
                side_effect=lambda path: _inspection(path, "2.1.0"),
            ):
                with self.assertRaisesRegex(ValueError, "duplicate official artifact"):
                    select_latest_official_artifact(root)

    def test_bootstrap_rejects_unexpected_active_pointer_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "official__official-catalog-2-1-0.tar.gz"
            artifact.touch()
            with (
                patch(
                    "rag.bootstrap.inspect_artifact",
                    return_value=_inspection(artifact, "2.1.0"),
                ),
                patch(
                    "rag.bootstrap.import_artifact",
                    return_value={"active_indexes": {}},
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "expected pointers"):
                    bootstrap_latest_official_artifact(
                        database_url="postgresql://runtime:test@postgres/runtime",
                        artifact_directory=root,
                    )


if __name__ == "__main__":
    unittest.main()
