"""baseline_key 발급·검증 (S15P21B109-137).

Django settings 가 필요해 django.setup() 을 한다(SECRET_KEY 를 써야 한다).
DB 는 쓰지 않는다.
"""

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

from simulations.baseline import (  # noqa: E402
    BASELINE_KEY_PATTERN,
    has_baseline_key_shape,
    issue_baseline_key,
)

MATERIAL = {
    'user_id': 7,
    'session_id': 42,
    'snapshot_digest': 'a' * 64,
    'policy_version': 'simulation-mvp-2026-09-10.v1',
    'contract_version': 'retirement-analysis-v1',
    'mapping_version': 'retirement-input-v1',
    'package_version': '0.1.0',
}


class BaselineKeyTests(unittest.TestCase):
    def test_same_material_yields_the_same_key(self) -> None:
        self.assertEqual(issue_baseline_key(**MATERIAL), issue_baseline_key(**MATERIAL))

    def test_every_material_field_changes_the_key(self) -> None:
        original = issue_baseline_key(**MATERIAL)
        replacements = {
            'user_id': 8,
            'session_id': 43,
            'snapshot_digest': 'b' * 64,
            'policy_version': 'simulation-mvp-2026-09-11.v2',
            'contract_version': 'retirement-analysis-v2',
            'mapping_version': 'retirement-input-v2',
            'package_version': '0.1.1',
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                changed = issue_baseline_key(**{**MATERIAL, field: value})
                self.assertNotEqual(changed, original)

    def test_key_does_not_leak_the_snapshot_digest(self) -> None:
        """digest 는 재료일 뿐 응답에 나가면 안 된다. 키에 부분 문자열로도 남지 않는다."""
        key = issue_baseline_key(**MATERIAL)
        digest = MATERIAL['snapshot_digest']
        self.assertNotIn(digest, key)
        for size in (8, 16):
            self.assertNotIn(digest[:size], key)

    def test_key_shape_is_stable(self) -> None:
        key = issue_baseline_key(**MATERIAL)
        self.assertRegex(key, BASELINE_KEY_PATTERN)
        self.assertTrue(has_baseline_key_shape(key))

    def test_shape_check_rejects_malformed_values(self) -> None:
        for candidate in (None, 1, '', 'bk1.', 'bk2.' + 'a' * 43, 'a' * 47):
            with self.subTest(candidate=candidate):
                self.assertFalse(has_baseline_key_shape(candidate))

    def test_field_boundaries_are_not_ambiguous(self) -> None:
        """재료를 이어붙일 때 구분자가 섞여 서로 다른 입력이 같은 키가 되면 안 된다."""
        left = issue_baseline_key(
            **{**MATERIAL, 'policy_version': 'a|b', 'contract_version': 'c'}
        )
        right = issue_baseline_key(
            **{**MATERIAL, 'policy_version': 'a', 'contract_version': 'b|c'}
        )
        self.assertNotEqual(left, right)


if __name__ == '__main__':
    unittest.main()
