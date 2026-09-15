from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from django.urls import Resolver404, resolve  # noqa: E402


class DomainApiRoutingContractTests(unittest.TestCase):
    def test_active_routes_use_domain_then_version(self) -> None:
        routes = {
            '/api/auth/v1/me/': 'me',
            '/api/consulting/v1/consultations/': 'consultation-list',
            '/api/consulting/v2/consultations/1/messages/stream/': (
                'v2-consultation-messages-stream'
            ),
            '/api/reports/v1/consultations/1/reports/': None,
            '/api/simulations/v1/consultations/1/simulations/': 'simulation-run',
            '/api/community/v1/posts/': 'post-list-create',
            '/api/admin/v1/ping/': 'admin-ping',
        }

        for path, url_name in routes.items():
            with self.subTest(path=path):
                match = resolve(path)
                if url_name is not None:
                    self.assertEqual(match.url_name, url_name)

    def test_legacy_global_version_routes_are_not_aliases(self) -> None:
        for path in (
            '/api/v1/auth/me/',
            '/api/v1/consultations/',
            '/api/v2/consultations/1/messages/stream/',
            '/api/v1/consultations/1/reports/',
            '/api/v1/consultations/1/simulations/',
            '/api/v1/community/posts/',
            '/api/v1/admin/ping/',
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)


if __name__ == '__main__':
    unittest.main()
