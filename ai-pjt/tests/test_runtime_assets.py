from __future__ import annotations

import unittest

from runtime_assets import verify_runtime_assets


class RuntimeAssetsTests(unittest.TestCase):
    def test_all_direct_catalog_consumers_load_packaged_assets(self) -> None:
        verify_runtime_assets()
