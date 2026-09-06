"""Keep P08 interface guides aligned with the canonical Django field registry."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from consulting.field_registry import get_step_field_registry


CATALOG_PATH = REPOSITORY_ROOT / "ai-pjt/rag/data/catalog.json"
STEP_REGISTRY_PATH = (
    REPOSITORY_ROOT
    / "contract-fixtures/ai-lifecycle/v2/step-registry.json"
)


def _table_rows(path: Path) -> dict[tuple[str, str], dict[str, object]]:
    rows: dict[tuple[str, str], dict[str, object]] = {}
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| section | field_name |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        if line.startswith("|---"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) != 6:
            raise AssertionError(f"unexpected interface guide row: {line}")
        section, field_name, value_type, unit, constraints, writable = cells
        rows[(section, field_name)] = {
            "value_type": value_type,
            "unit": unit,
            "constraints": json.loads(constraints),
            "writable_by_ai": writable == "허용",
        }
    return rows


class RagInterfaceGuideContractTests(unittest.TestCase):
    def test_catalog_consumers_are_canonical_registered_agent_ids(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        step_registry = json.loads(STEP_REGISTRY_PATH.read_text(encoding="utf-8"))
        registered_agents = set(step_registry["agents"])

        for collection in catalog["collections"]:
            with self.subTest(collection=collection["id"]):
                self.assertTrue(collection["consumers"])
                self.assertLessEqual(
                    set(collection["consumers"]),
                    registered_agents,
                )

    def test_every_interface_guide_matches_the_canonical_registry(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        guides = [
            document
            for document in catalog["documents"]
            if document["document_type"] == "interface-guide"
        ]
        self.assertTrue(guides)

        for guide in guides:
            with self.subTest(document=guide["id"]):
                step_id = guide["applies_to"]["step_id"]
                path = REPOSITORY_ROOT / guide["path"]
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"step_id: {step_id}", text)
                self.assertIn(f"registry_source: {guide['source_locator']}", text)

                expected = {
                    (field["section"], field["path"]): {
                        "value_type": field["value_type"],
                        "unit": field["unit"],
                        "constraints": field["constraints"],
                        "writable_by_ai": field["writable_by_ai"],
                    }
                    for field in get_step_field_registry(step_id)
                    if field["sensitivity"] != "operational"
                }
                self.assertEqual(_table_rows(path), expected)


if __name__ == "__main__":
    unittest.main()
