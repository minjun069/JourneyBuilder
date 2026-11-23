from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rag.catalog_paths import (
    CatalogDocumentPathError,
    resolve_catalog_document_path,
)


class CatalogDocumentPathTests(unittest.TestCase):
    def test_project_path_resolves_below_flattened_package_data_root(self) -> None:
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "app" / "rag" / "data" / "catalog.json"

            resolved = resolve_catalog_document_path(
                catalog,
                "ai-pjt/rag/data/chart-guides/presentation.md",
            )

            self.assertEqual(
                resolved,
                catalog.parent / "chart-guides" / "presentation.md",
            )

    def test_path_outside_approved_catalog_data_prefix_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "app" / "rag" / "data" / "catalog.json"
            invalid_paths = (
                "/etc/passwd",
                "ai-pjt/rag/data/../../secrets.env",
                "other-service/rag/data/manual.md",
            )
            for configured_path in invalid_paths:
                with self.subTest(configured_path=configured_path):
                    with self.assertRaises(CatalogDocumentPathError):
                        resolve_catalog_document_path(catalog, configured_path)
