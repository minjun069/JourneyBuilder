from __future__ import annotations

import logging
import unittest

from core.logging_setup import StructuredFormatter, configure_logging
from core.observability_masking import masked_exception_fields


TEST_MASKING_KEY = b"f11-t03-observability-test-key-32bytes"
_HANDLER_NAME = "retirewise-structured"


class MaskedExceptionFieldTests(unittest.TestCase):
    """An executor_error must stay diagnosable without leaking raw text."""

    def _fields(self, error: BaseException, **context: object) -> dict:
        return masked_exception_fields(
            error,
            pseudonym_key=TEST_MASKING_KEY,
            **context,
        )

    def test_direct_identifiers_and_secrets_never_reach_log_fields(self) -> None:
        try:
            raise ValueError(
                "rejected hong@example.com / 010-1234-5678 / "
                "Bearer aaaaaaaa.bbbbbbbb.cccccccc"
            )
        except ValueError as error:
            fields = self._fields(error)

        rendered = repr(fields)
        for sentinel in (
            "hong@example.com",
            "010-1234-5678",
            "aaaaaaaa.bbbbbbbb.cccccccc",
        ):
            self.assertNotIn(sentinel, rendered)
        self.assertNotIn("error_message", fields)
        self.assertNotIn("error_traceback", fields)
        self.assertRegex(fields["error_fingerprint"], r"^[0-9a-f]{24}$")

    def test_type_and_fingerprint_survive_for_diagnosis(self) -> None:
        try:
            raise KeyError("current_query")
        except KeyError as error:
            fields = self._fields(error, executor_name="qa")

        self.assertEqual(fields["error_type"], "KeyError")
        self.assertEqual(fields["error_module"], "builtins")
        self.assertEqual(fields["executor_name"], "qa")
        self.assertRegex(fields["error_fingerprint"], r"^[0-9a-f]{24}$")

    def test_masking_failure_fails_closed_instead_of_raw_text(self) -> None:
        try:
            raise ValueError("raw hong@example.com")
        except ValueError as error:
            fields = masked_exception_fields(error, pseudonym_key=b"")

        self.assertEqual(fields.get("masking_status"), "failed_closed")
        self.assertNotIn("hong@example.com", repr(fields))


class StructuredFormatterTests(unittest.TestCase):
    """The default formatter drops extra=; deployments need it rendered."""

    def test_extra_fields_are_rendered_instead_of_dropped(self) -> None:
        record = logging.LogRecord(
            name="graph.executor",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="task executor raised",
            args=(),
            exc_info=None,
        )
        record.event = "executor_error"
        record.detail = {"error_type": "TimeoutError"}

        rendered = StructuredFormatter(
            fmt="%(levelname)s %(name)s %(message)s"
        ).format(record)

        self.assertIn("task executor raised", rendered)
        self.assertIn('"event": "executor_error"', rendered)
        self.assertIn('"error_type": "TimeoutError"', rendered)

    def test_configure_logging_installs_exactly_one_handler(self) -> None:
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        try:
            root.handlers = []
            configure_logging(level="INFO")
            configure_logging(level="INFO")
            named = [
                handler
                for handler in root.handlers
                if getattr(handler, "name", None) == _HANDLER_NAME
            ]
            self.assertEqual(len(named), 1)
            self.assertEqual(root.level, logging.INFO)
        finally:
            root.handlers = saved_handlers
            root.level = saved_level


if __name__ == "__main__":
    unittest.main()
