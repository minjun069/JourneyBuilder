from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from config.runtime_env import (  # noqa: E402
    RuntimeConfigurationError,
    read_bool,
    read_csv,
    read_positive_float,
    read_positive_int,
    require_secret,
)


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_required_secrets_reject_missing_and_insecure_values(self) -> None:
        for value in (None, "", " ", "change-me", "password", "ssafy"):
            environ = {} if value is None else {"TOKEN": value}
            with self.subTest(value=value):
                with self.assertRaises(RuntimeConfigurationError):
                    require_secret("TOKEN", environ)

    def test_required_secrets_accept_disposable_gate_values(self) -> None:
        self.assertEqual(
            require_secret("TOKEN", {"TOKEN": "g1-disposable-42"}),
            "g1-disposable-42",
        )

    def test_boolean_parser_is_explicit(self) -> None:
        self.assertTrue(read_bool("FLAG", False, {"FLAG": "TRUE"}))
        self.assertFalse(read_bool("FLAG", True, {"FLAG": "false"}))
        with self.assertRaises(RuntimeConfigurationError):
            read_bool("FLAG", False, {"FLAG": "yes"})

    def test_numeric_parsers_require_positive_finite_values(self) -> None:
        self.assertEqual(read_positive_int("COUNT", 3, {}), 3)
        self.assertEqual(read_positive_float("TIMEOUT", 2, {}), 2.0)
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeConfigurationError):
                    read_positive_float("TIMEOUT", 2, {"TIMEOUT": value})
        self.assertTrue(math.isfinite(read_positive_float("TIMEOUT", 2, {})))

    def test_csv_parser_rejects_empty_lists(self) -> None:
        self.assertEqual(read_csv("HOSTS", "localhost", {}), ["localhost"])
        with self.assertRaises(RuntimeConfigurationError):
            read_csv("HOSTS", "localhost", {"HOSTS": " , "})


if __name__ == "__main__":
    unittest.main()
