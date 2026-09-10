"""Self-tests for the F12-T01 service and tool security contract."""

from __future__ import annotations

import copy
import unittest

from .service_tool_security_contract import (
    DOWNSTREAM_OWNERS,
    REQUIRED_CASE_GROUPS,
    cases_for_group,
    cases_for_owner,
    load_lifecycle_fixture,
    load_suite,
    validate_suite,
)


class ServiceToolSecurityFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_suite()
        self.lifecycle = load_lifecycle_fixture(self.suite)

    def test_suite_structure_and_lifecycle_references_are_valid(self) -> None:
        self.assertEqual([], validate_suite(self.suite, self.lifecycle))

    def test_every_required_attack_group_has_a_block_case(self) -> None:
        self.assertEqual(
            set(self.suite["required_case_groups"]),
            set(REQUIRED_CASE_GROUPS),
        )
        for group in REQUIRED_CASE_GROUPS:
            with self.subTest(group=group):
                self.assertTrue(
                    any(
                        case["expected"]["decision"] == "block"
                        for case in cases_for_group(self.suite, group)
                    )
                )

    def test_allow_controls_cover_auth_callback_binding_and_tool_paths(self) -> None:
        allow_groups = {
            case["group"]
            for case in self.suite["cases"]
            if case["expected"]["decision"] == "allow"
        }
        self.assertTrue(
            {
                "service_bearer",
                "callback_integrity",
                "replay_idempotency",
                "binding_integrity",
                "tool_authorization",
            }.issubset(allow_groups)
        )

    def test_every_downstream_owner_receives_cases(self) -> None:
        for owner in DOWNSTREAM_OWNERS:
            with self.subTest(owner=owner):
                self.assertTrue(cases_for_owner(self.suite, owner))

    def test_replay_allows_only_existing_results_without_graph_side_effects(self) -> None:
        allow_replays = [
            case
            for case in cases_for_group(self.suite, "replay_idempotency")
            if case["expected"]["decision"] == "allow"
        ]
        self.assertTrue(allow_replays)
        for case in allow_replays:
            with self.subTest(case=case["id"]):
                required = set(case["expected"]["required_effects"])
                forbidden = set(case["expected"]["forbidden_effects"])
                self.assertTrue(
                    {"no_duplicate_side_effect", "no_graph_reexecution"}
                    & required
                )
                self.assertTrue(
                    {"schedule_graph", "invoke_planner", "create_duplicate_message"}
                    & forbidden
                )

    def test_callback_ownership_mismatch_has_no_write_effect(self) -> None:
        case = next(
            case for case in self.suite["cases"] if case["id"] == "F12-OWN-002"
        )
        self.assertEqual(case["expected"]["decision"], "block")
        self.assertEqual(
            case["expected"]["reason_code"],
            "CALLBACK_BINDING_MISMATCH",
        )
        self.assertTrue(
            {"create_assistant_message", "save_terminal_state"}.issubset(
                case["expected"]["forbidden_effects"]
            )
        )

    def test_fixture_rejects_duplicate_case_id(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["id"] = mutated["cases"][0]["id"]

        errors = validate_suite(mutated, self.lifecycle)

        self.assertIn(f"duplicate case id: {mutated['cases'][0]['id']}", errors)

    def test_fixture_rejects_unknown_lifecycle_reference(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][0]["source_fixture_case"] = "missing-case"

        errors = validate_suite(mutated, self.lifecycle)

        self.assertIn(
            "F12-AUTH-001 references unknown lifecycle case 'missing-case'",
            errors,
        )

    def test_fixture_detects_lifecycle_status_drift(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][0]["expected"]["http_status"] = 403

        errors = validate_suite(mutated, self.lifecycle)

        self.assertIn(
            "F12-AUTH-001 http status differs from service_unauthorized",
            errors,
        )

    def test_fixture_rejects_non_placeholder_service_credential(self) -> None:
        mutated = copy.deepcopy(self.suite)
        mutated["cases"][1]["input"]["authorization"] = "Bearer real-looking-token"

        errors = validate_suite(mutated, self.lifecycle)

        self.assertIn(
            "F12-AUTH-002 authorization must use a placeholder",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
