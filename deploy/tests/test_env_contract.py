from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deploy.check_env_contract import (
    REPOSITORY_ROOT,
    parse_compose_variables,
    read_env_example,
    validate_release_identity,
    validate_repository,
)


class EnvironmentContractTests(unittest.TestCase):
    def test_repository_contract_is_synchronized(self) -> None:
        self.assertEqual(validate_repository(REPOSITORY_ROOT), [])

    def test_release_identity_is_baked_into_the_ai_image(self) -> None:
        self.assertEqual(validate_release_identity(REPOSITORY_ROOT), [])

    def test_compose_parser_classifies_and_ignores_container_expansion(self) -> None:
        variables, errors = parse_compose_variables(
            '${REQUIRED:?required} ${OPTIONAL:-default} "$${CONTAINER_ONLY}"',
            source="fixture.yml",
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            variables,
            {"REQUIRED": "required", "OPTIONAL": "optional"},
        )

    def test_root_example_requires_explicit_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env.example"
            path.write_text("UNCLASSIFIED=value\n", encoding="utf-8")

            _, errors = read_env_example(path, require_sections=True)

        self.assertEqual(len(errors), 1)
        self.assertIn("must be under an env-contract section", errors[0])


if __name__ == "__main__":
    unittest.main()
