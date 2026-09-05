from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch


AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

import readiness  # noqa: E402


class ReadinessTests(unittest.TestCase):
    def test_requires_database_url_before_running_probes(self) -> None:
        http_probe = Mock()
        database_probe = Mock()

        with self.assertRaisesRegex(
            readiness.ReadinessConfigurationError, "DATABASE_URL is required"
        ):
            readiness.check_readiness({}, http_probe, database_probe)

        http_probe.assert_not_called()
        database_probe.assert_not_called()

    def test_checks_http_then_postgres_with_configured_values(self) -> None:
        calls: list[tuple[str, str, float]] = []

        def http_probe(url: str, timeout: float) -> None:
            calls.append(("http", url, timeout))

        def database_probe(database_url: str, timeout: float) -> None:
            calls.append(("postgres", database_url, timeout))

        readiness.check_readiness(
            {
                "DATABASE_URL": "postgresql://runtime:secret@postgres:5432/retirewise_db",
                "READINESS_HTTP_URL": "http://127.0.0.1:8001/health",
                "READINESS_TIMEOUT_SECONDS": "3",
            },
            http_probe,
            database_probe,
        )

        self.assertEqual(
            calls,
            [
                ("http", "http://127.0.0.1:8001/health", 3.0),
                (
                    "postgres",
                    "postgresql://runtime:secret@postgres:5432/retirewise_db",
                    3.0,
                ),
            ],
        )

    def test_rejects_invalid_timeouts(self) -> None:
        for invalid_timeout in ("0", "-1", "11", "nan", "inf", "not-a-number"):
            with self.subTest(invalid_timeout=invalid_timeout):
                with self.assertRaises(readiness.ReadinessConfigurationError):
                    readiness.check_readiness(
                        {
                            "DATABASE_URL": "postgresql://runtime:secret@postgres/db",
                            "READINESS_TIMEOUT_SECONDS": invalid_timeout,
                        },
                        Mock(),
                        Mock(),
                    )

    def test_rejects_empty_http_url(self) -> None:
        with self.assertRaisesRegex(
            readiness.ReadinessConfigurationError,
            "READINESS_HTTP_URL must not be empty",
        ):
            readiness.check_readiness(
                {
                    "DATABASE_URL": "postgresql://runtime:secret@postgres/db",
                    "READINESS_HTTP_URL": " ",
                },
                Mock(),
                Mock(),
            )

    def test_http_probe_requires_expected_payload(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps({"status": "starting"}).encode()

        with patch.object(readiness, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "unexpected payload"):
                readiness.check_http("http://127.0.0.1:8001/health", 2.0)

    def test_http_probe_requires_200_status(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 503

        with patch.object(readiness, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "non-200 status"):
                readiness.check_http("http://127.0.0.1:8001/health", 2.0)

    def test_postgres_probe_runs_select_one_with_rounded_timeout(self) -> None:
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value = cursor
        connect = Mock(return_value=connection)

        with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}):
            readiness.check_postgres(
                "postgresql://runtime:secret@postgres/db",
                2.1,
            )

        connect.assert_called_once_with(
            "postgresql://runtime:secret@postgres/db",
            connect_timeout=3,
        )
        cursor.execute.assert_called_once_with("SELECT 1")

    def test_main_redacts_dependency_error_details(self) -> None:
        secret = "do-not-print-this-secret"
        stderr = io.StringIO()
        stdout = io.StringIO()

        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": f"postgresql://runtime:{secret}@postgres/db"},
                clear=True,
            ),
            patch.object(readiness, "check_http"),
            patch.object(
                readiness,
                "check_postgres",
                side_effect=RuntimeError(f"failed DSN contained {secret}"),
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
