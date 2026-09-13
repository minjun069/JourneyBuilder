from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


class RemovedCallbackRouteContractTests(SimpleTestCase):
    def test_http_callback_routes_are_not_resolvable(self):
        for path in (
            "/api/ai/callback",
            "/api/ai/callback/",
            "/api/v2/internal/ai/callback/",
        ):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
