from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend-pjt"


class ASGIRuntimeContractTests(unittest.TestCase):
    def test_backend_image_runs_the_asgi_application(self) -> None:
        dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn('"uvicorn", "config.asgi:application"', dockerfile)
        self.assertNotIn("config.wsgi:application", dockerfile)

    def test_django_declares_its_asgi_application(self) -> None:
        settings = (BACKEND_ROOT / "config" / "settings.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("ASGI_APPLICATION = 'config.asgi.application'", settings)

    def test_sse_gateway_disables_response_buffering(self) -> None:
        nginx = (REPOSITORY_ROOT / "deploy" / "nginx" / "default.conf").read_text(
            encoding="utf-8"
        )

        self.assertIn("/messages/stream/", nginx)
        self.assertIn("proxy_buffering off;", nginx)
        self.assertIn("proxy_cache off;", nginx)
        self.assertIn("proxy_read_timeout 130s;", nginx)


if __name__ == "__main__":
    unittest.main()
