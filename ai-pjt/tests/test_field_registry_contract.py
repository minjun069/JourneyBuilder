from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
import unittest

from pydantic import ValidationError

from core.field_registry import SECTION_META, load_field_registry
from transport.contracts import ChatV2Request


def request_payload() -> dict:
    return {
        "contract_version": "2.0",
        "request_id": "request-field-registry",
        "payload_digest": "0" * 64,
        "session_id": 1,
        "thread_id": "thread-field-registry",
        "step_id": "basic-info",
        "message": "현재 입력 항목을 설명해 주세요.",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=30),
        "customer_info": {"saved_data": {}, "current_form": {}},
    }


class FieldRegistryContractTests(unittest.TestCase):
    def test_request_does_not_accept_remote_registry_or_tool_context(self) -> None:
        request = ChatV2Request.model_validate(request_payload())
        self.assertFalse(hasattr(request, "field_registry"))
        self.assertFalse(hasattr(request, "tool_context"))

        for retired_field, value in (
            ("field_registry", []),
            ("tool_context", {}),
        ):
            with self.subTest(field=retired_field):
                payload = request_payload()
                payload[retired_field] = value
                with self.assertRaises(ValidationError):
                    ChatV2Request.model_validate(payload)

    def test_registry_is_loaded_by_step_id_as_a_fresh_local_projection(self) -> None:
        basic = load_field_registry("basic-info")
        self.assertTrue(basic)
        self.assertEqual({field["step_id"] for field in basic}, {"basic-info"})
        self.assertTrue(any(field["path"] == "monthly_income" for field in basic))

        basic[0]["path"] = "mutated"
        self.assertNotEqual(load_field_registry("basic-info")[0]["path"], "mutated")
        self.assertEqual(load_field_registry("unknown-step"), [])

    def test_ai_local_registry_matches_backend_canonical_contract(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        backend_namespace = runpy.run_path(
            str(repository_root / "backend-pjt" / "consulting" / "field_registry.py")
        )
        backend_loader = backend_namespace["get_step_field_registry"]
        step_ids = sorted({step_id for step_id, _ in SECTION_META.values()})

        for step_id in step_ids:
            with self.subTest(step_id=step_id):
                self.assertEqual(load_field_registry(step_id), backend_loader(step_id))

    def test_empty_step_id_is_rejected(self) -> None:
        for step_id in ("", "   "):
            with self.subTest(step_id=step_id):
                with self.assertRaises(ValueError):
                    load_field_registry(step_id)


if __name__ == "__main__":
    unittest.main()
