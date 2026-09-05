from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import readiness  # noqa: E402


class ReadinessTests(unittest.TestCase):
    def test_checks_process_database_and_redis_in_order(self) -> None:
        calls: list[tuple[object, ...]] = []

        def process_probe(host: str, port: int, timeout: float) -> None:
            calls.append(("process", host, port, timeout))

        def database_probe(timeout: float) -> None:
            calls.append(("postgres", timeout))

        def redis_probe(timeout: float) -> None:
            calls.append(("redis", timeout))

        readiness.check_readiness(
            {
                "READINESS_HOST": "127.0.0.1",
                "READINESS_PORT": "9000",
                "READINESS_TIMEOUT_SECONDS": "3",
            },
            process_probe,
            database_probe,
            redis_probe,
        )

        self.assertEqual(
            calls,
            [
                ("process", "127.0.0.1", 9000, 3.0),
                ("postgres", 3.0),
                ("redis", 3.0),
            ],
        )

    def test_stops_after_the_first_failed_dependency(self) -> None:
        database_probe = Mock(side_effect=RuntimeError("database unavailable"))
        redis_probe = Mock()

        with self.assertRaises(RuntimeError):
            readiness.check_readiness(
                {},
                Mock(),
                database_probe,
                redis_probe,
            )

        redis_probe.assert_not_called()

    def test_rejects_invalid_timeouts(self) -> None:
        for invalid_timeout in ("0", "-1", "11", "nan", "inf", "not-a-number"):
            with self.subTest(invalid_timeout=invalid_timeout):
                with self.assertRaises(readiness.ReadinessConfigurationError):
                    readiness.check_readiness(
                        {"READINESS_TIMEOUT_SECONDS": invalid_timeout},
                        Mock(),
                        Mock(),
                        Mock(),
                    )

    def test_rejects_invalid_ports(self) -> None:
        for invalid_port in ("0", "-1", "65536", "8000.5", "not-a-port"):
            with self.subTest(invalid_port=invalid_port):
                with self.assertRaises(readiness.ReadinessConfigurationError):
                    readiness.check_readiness(
                        {"READINESS_PORT": invalid_port},
                        Mock(),
                        Mock(),
                        Mock(),
                    )

    def test_rejects_empty_host(self) -> None:
        with self.assertRaisesRegex(
            readiness.ReadinessConfigurationError,
            "READINESS_HOST must not be empty",
        ):
            readiness.check_readiness(
                {"READINESS_HOST": " "},
                Mock(),
                Mock(),
                Mock(),
            )

    def test_process_probe_connects_to_the_configured_socket(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection

        with patch.object(
            readiness.socket,
            "create_connection",
            return_value=connection,
        ) as create_connection:
            readiness.check_process("127.0.0.1", 8000, 2.0)

        create_connection.assert_called_once_with(("127.0.0.1", 8000), timeout=2.0)

    def test_database_probe_requires_select_one_and_no_pending_migrations(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.cursor.return_value = cursor
        executor = MagicMock()
        executor.loader.graph.leaf_nodes.return_value = [("accounts", "0001")]
        executor.migration_plan.return_value = []
        executor_factory = Mock(return_value=executor)

        readiness._assert_database_ready(connection, executor_factory)

        cursor.execute.assert_called_once_with("SELECT 1")
        executor.migration_plan.assert_called_once_with([("accounts", "0001")])

    def test_database_probe_rejects_pending_migrations(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.cursor.return_value = cursor
        executor = MagicMock()
        executor.loader.graph.leaf_nodes.return_value = [("accounts", "0002")]
        executor.migration_plan.return_value = [("migration", False)]

        with self.assertRaisesRegex(RuntimeError, "unapplied Django migrations"):
            readiness._assert_database_ready(connection, Mock(return_value=executor))

    def test_redis_probe_requires_true_ping(self) -> None:
        client = Mock()
        client.ping.return_value = False

        with self.assertRaisesRegex(RuntimeError, "unexpected result"):
            readiness._assert_redis_ready(client)

    def test_main_redacts_dependency_error_details(self) -> None:
        secret = "do-not-print-this-secret"
        stderr = io.StringIO()
        stdout = io.StringIO()

        with (
            patch.object(
                readiness,
                "check_readiness",
                side_effect=RuntimeError(f"connection contained {secret}"),
            ),
            redirect_stderr(stderr),
            redirect_stdout(stdout),
        ):
            exit_code = readiness.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
